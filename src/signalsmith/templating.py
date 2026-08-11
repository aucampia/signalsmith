"""Jinja-based rendering and rule-expression evaluation.

Two distinct uses share one engine and one context builder here:

- `notice.title`/`notice.body` (`config.models.NoticeConfig`) and
  `notify.title`/`notify.body` (`config.models.NotifyActionConfig`) -
  templates, rendered with `render()`/`render_notice()`/`render_notify()`.
  A `notify` override may reference the already-rendered
  `notice.title`/`notice.body` via `notice` in scope.
- `Rule.expression` (`config.models.Rule`), evaluated by `rules.RuleMatcher`
  via `compile_expression()`/`evaluate()` - a boolean expression, not a
  template, so it is compiled with `Environment.compile_expression` rather
  than `Environment.from_string`. The `subject` name in the context is a lazy
  proxy (`LazySubject`) that fetches on first attribute access, so a cheap
  `notification.*` guard on the left of `and` still gates the GitHub API call.

Both use `StrictUndefined`: a reference to something that isn't in scope
raises rather than silently rendering blank, verbatim, or `None`. For
templates, `render()` catches that and falls back rather than dropping the
notification - see `actions/registry._build_notify` for the
subject-fetch-then-render sequence and the WARNING/ERROR split via
`expected_failure`. Rule expressions do **not** fall back - a bad expression
must not silently read as "no match", so it propagates and the notification
is reported as errored (see `rules.RuleMatcher`).
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

import jinja2
import jinja2.meta

from .config.models import NoticeConfig, NotifyActionConfig
from .github.models import (
    GITHUB_NOTIFICATION_ADAPTER,
    GitHubIssue,
    GitHubNotification,
    GitHubPullRequest,
)
from .notifier import RenderedNotification

logger = logging.getLogger(__name__)


# Exception types for subject fetching. Defined here rather than errors.py
# because SignalsmithError's contract is "abort the command cleanly", but
# a subject fetch failure must degrade to FILTERED_ERROR for one notification,
# not kill the run.
class SubjectFetchError(Exception):
    """Fetching the subject for a rule expression failed.

    CRITICAL: Must NOT subclass AttributeError, TypeError, LookupError, or
    jinja2.TemplateError - if it did, Jinja's Environment.getattr/getitem would
    catch it and convert a hard fetch failure into a silent Undefined (i.e. a
    silent no-match).
    """

    def __init__(self, message: str, subject_type: str, subject_url: str) -> None:
        super().__init__(message)
        self.subject_type = subject_type
        self.subject_url = subject_url


class SubjectUnavailableError(SubjectFetchError):
    """The subject cannot be fetched at all (unsupported type or missing URL)."""


class LazySubject(Mapping[str, Any]):
    """Lazy proxy for the `subject` in a rule expression context.

    Fetches the subject on first attribute/item access, memoizes the result
    (including failures), and presents the same dict-like interface as the
    plain subject dict that templates use. This allows a cheap
    `notification.*` guard on the left of `and` to gate the GitHub API call
    even when both halves live in one expression.

    Jinja's `and`/`or` compile to Python `and`/`or` and short-circuit, so
    `notification.subject.type == "PullRequest" and subject.merged` never
    fetches for an Issue notification.
    """

    __slots__ = ("_data", "_error", "_fetch", "_notification", "_pydantic_model")

    def __init__(
        self,
        notification: GitHubNotification,
        fetcher: Any,  # Callable[[str, str, str], GitHubIssue | GitHubPullRequest]
    ) -> None:
        self._notification = notification
        self._fetch = fetcher
        self._data: dict[str, Any] | None = None
        self._error: Exception | None = None
        self._pydantic_model: GitHubIssue | GitHubPullRequest | None = None

    def _materialize(self) -> dict[str, Any]:
        """Fetch and dump the subject once, memoizing both success and failure."""
        if self._data is not None:
            return self._data
        if self._error is not None:
            raise self._error

        subject_url = self._notification.subject.url
        subject_type = self._notification.subject.type
        updated_at = self._notification.updated_at

        if subject_url is None:
            self._error = SubjectUnavailableError(
                "notification has no subject URL",
                subject_type=subject_type,
                subject_url="",
            )
            raise self._error

        try:
            subject = self._fetch(subject_url, subject_type, updated_at)
        except NotImplementedError as exc:
            self._error = SubjectUnavailableError(
                f"subject type {subject_type!r} is not supported",
                subject_type=subject_type,
                subject_url=subject_url,
            )
            self._error.__cause__ = exc
            raise self._error from exc
        except Exception as exc:
            self._error = SubjectFetchError(
                f"failed to fetch subject: {exc}",
                subject_type=subject_type,
                subject_url=subject_url,
            )
            self._error.__cause__ = exc
            raise self._error from exc

        # Store the pydantic model and dump to the same shape build_context produces
        self._pydantic_model = subject
        self._data = json.loads(subject.model_dump_json())
        return self._data

    def __getattr__(self, name: str) -> Any:
        # CRITICAL: guard underscore/dunder access to prevent copy/pickle/repr/
        # pytest introspection from triggering a network fetch, and to prevent
        # infinite recursion on a half-initialized instance.
        if name.startswith("_"):
            raise AttributeError(name)
        data = self._materialize()
        try:
            return data[name]
        except KeyError:
            # Reproduce the exact StrictUndefined behavior for a missing field
            # in a plain dict - byte-identical error message.
            return ENV.undefined(obj=data, name=name)

    def __getitem__(self, key: str) -> Any:
        # Environment.getitem catches (AttributeError, TypeError, LookupError)
        # from this and converts to Undefined. A SubjectFetchError is none of
        # those, so it propagates.
        return self._materialize()[key]

    def __iter__(self) -> Any:
        return iter(self._materialize())

    def __len__(self) -> int:
        return len(self._materialize())

    def __repr__(self) -> str:
        # Must NOT materialize - debuggers/log formatting must be free.
        if self._data is not None:
            return "<LazySubject fetched>"
        return "<LazySubject unfetched>"

    @property
    def _model(self) -> GitHubIssue | GitHubPullRequest | None:
        """The fetched pydantic model, or None if not yet fetched.

        Underscore-prefixed so Jinja's getattr can never route a real subject
        field to this property. Used only by tests and potential future
        registry integration.
        """
        # Deliberately does not materialize - returns None if unfetched
        return self._pydantic_model


__all__ = [
    "LazySubject",
    "SubjectFetchError",
    "SubjectUnavailableError",
    "build_context",
    "compile_expression",
    "evaluate",
    "references_subject",
    "render_notice",
    "render_notify",
    "template_names",
]

ENV = jinja2.Environment(
    undefined=jinja2.StrictUndefined,
    autoescape=False,  # ruff: ignore[jinja2-autoescape-false] - plain-text desktop notifications, not HTML
    trim_blocks=True,
    lstrip_blocks=True,
)

# `select` calls a test as `test(item, *args)`, so `prefix` below is the
# left-hand list item and `string` is the value passed to `select`:
#   variables.offtopic.prefixes | select('startingwith', full_name)
# tests, for each prefix in the list, whether `full_name` starts with it.
ENV.tests["startingwith"] = lambda prefix, string: string.startswith(prefix)


# Allow `subject|tojson` to work with the LazySubject proxy - without this,
# json.dumps raises "Object of type LazySubject is not JSON serializable".
def _json_default(o: Any) -> Any:
    if isinstance(o, Mapping):
        return dict(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


ENV.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": _json_default}

_TEMPLATE_CACHE: dict[str, jinja2.Template] = {}
_EXPRESSION_CACHE: dict[str, jinja2.environment.TemplateExpression] = {}


def _compile(source: str) -> jinja2.Template:
    """Compile `source` once per process; every render reuses the same object."""
    template = _TEMPLATE_CACHE.get(source)
    if template is None:
        template = ENV.from_string(source)
        _TEMPLATE_CACHE[source] = template
    return template


def compile_expression(source: str) -> jinja2.environment.TemplateExpression:
    """Compile a boolean/value expression (not a `{{ }}` template) once per process.

    Unlike `_compile`, this is for bare expressions like
    `notification.subject.type == "PullRequest" and subject.merged` -
    `Rule.expression` and the test-file `{{ ... }}` whole-string form
    (`config.testing`). `undefined_to_none=False` is deliberate: the default
    would turn a missing attribute into `None` instead of raising, which would
    make a typo silently evaluate to a falsy no-match rather than surfacing as
    an error.

    Raises `jinja2.TemplateSyntaxError` on a syntax error.
    """
    expression = _EXPRESSION_CACHE.get(source)
    if expression is None:
        expression = ENV.compile_expression(source, undefined_to_none=False)
        _EXPRESSION_CACHE[source] = expression
    return expression


def evaluate(source: str, context: Mapping[str, Any]) -> Any:
    """Evaluate `source` as an expression against `context` and return the result.

    Raises `jinja2.TemplateSyntaxError` on a syntax error,
    `jinja2.UndefinedError` on a reference to something not in `context`, and
    `SubjectFetchError` if the expression touches `subject` (via a
    `LazySubject` proxy) and fetching fails.

    A bare undefined reference (e.g. `subject.merged` with no further
    operation on it) does *not* itself raise - `StrictUndefined` only raises
    when something is done with the value (compared, stringified, iterated).
    Truthiness checks in `rules.RuleMatcher` trigger that for free via
    `__bool__`, but a caller like `config.testing` that returns the raw value
    (e.g. a whole-string `{{ parameter.nope }}`) would otherwise get back a
    silent `Undefined` object instead of an error, so it's forced explicitly
    here.
    """
    result = compile_expression(source)(**context)
    if isinstance(result, jinja2.Undefined):
        # StrictUndefined raises on `str()` (its `__str__` is the same
        # `_fail_with_undefined_error` hook `__bool__`/`__iter__` use) - this
        # forces that through the public dunder path rather than calling the
        # private method directly.
        str(result)
    return result


def template_names(source: str) -> set[str]:
    """Top-level names `source` references, e.g. `{"notification", "subject"}`.

    Used to decide whether a `subject` fetch is needed before rendering, so a
    config whose templates never mention `subject` never pays for one.

    Raises `jinja2.TemplateError` on a syntax error - callers that need a
    safe check (does this specific template need `subject`?) should use
    `references_subject` instead, which tolerates that.
    """
    return jinja2.meta.find_undeclared_variables(ENV.parse(source))


def references_subject(source: str) -> bool:
    """Whether `source` references `subject`, tolerating a syntax error.

    Used both to decide whether an on-demand subject fetch is needed
    (`actions.registry._resolve_subject_for_templates`) and to scope the
    WARNING/ERROR split in `render` to templates that actually reference
    `subject` - a template this can't even parse is always a genuine
    problem, so it's treated as "doesn't reference subject" here and
    surfaces as an ERROR when `render` itself tries to compile it.
    """
    try:
        return "subject" in template_names(source)
    except jinja2.TemplateError:
        return False


def render(
    source: str,
    context: Mapping[str, Any],
    *,
    label: str,
    fallback: str,
    expected_failure: str | None = None,
) -> str:
    """Render `source` against `context`, falling back to `fallback` on error.

    The result is stripped: templates are usually written as YAML block
    scalars and often contain `{% if %}` blocks, both of which can leave
    stray leading/trailing whitespace that has no place in a single-line
    desktop-notification title.

    `expected_failure`, when given, names a reason `source` was already known
    to be at risk of failing because it references `subject` (e.g. "subject
    type Release has no fetchable object") - logged at WARNING instead of
    ERROR, but only if `source` actually references `subject`
    (`references_subject`); otherwise the failure is unrelated (a typo, a bad
    filter, a syntax error) and is always a genuine config problem, logged at
    ERROR regardless of `expected_failure`.
    """
    try:
        rendered = _compile(source).render(dict(context))
    except jinja2.TemplateError as exc:
        if expected_failure is not None and references_subject(source):
            logger.warning(
                "Template %r could not render (%s), using fallback: %s",
                label,
                expected_failure,
                exc,
            )
        else:
            logger.error("Template %r failed to render, using fallback: %s", label, exc)
        return fallback
    return rendered.strip()


def build_context(
    notification: GitHubNotification,
    subject: GitHubIssue | GitHubPullRequest | None,
    account: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """Build the Jinja context shared by rule expressions and templates.

    `rules.RuleMatcher` uses this to build the base context for `Rule.expression`
    evaluation, then overwrites `context["subject"]` with a `LazySubject` proxy.
    Templates (`notice`/`notify`) use this directly with a real subject (or None),
    so both paths see the same `notification`/`account`/`variables` shape. Two
    properties aren't part of the JSON dump - `GitHubSubject.web_url` and
    `GitHubRepository.org` are plain `@property`s (`github/models.py`) - and are
    injected here.
    """
    notification_dict = GITHUB_NOTIFICATION_ADAPTER.dump_python(
        notification, mode="json"
    )
    notification_dict["subject"]["web_url"] = notification.subject.web_url
    notification_dict["repository"]["org"] = notification.repository.org

    context: dict[str, Any] = {
        "notification": notification_dict,
        "account": account,
        "variables": variables,
    }
    if subject is not None:
        context["subject"] = json.loads(subject.model_dump_json())
    return context


def _static_default_title(context: Mapping[str, Any]) -> str:
    """The built-in default title, computed without Jinja.

    The ultimate fallback for `notice.title`: plain dict indexing on
    `notification`, which is always present, so this can never itself raise.
    Kept textually identical to `DEFAULT_NOTICE_TITLE`.
    """
    notification = context["notification"]
    return f"{notification['subject']['type']}: {notification['subject']['title']}"


def _static_default_body(context: Mapping[str, Any]) -> str:
    """The built-in default body, computed without Jinja (see `_static_default_title`)."""
    notification = context["notification"]
    return f"{notification['repository']['full_name']} ({notification['reason']})"


def render_notice(
    config: NoticeConfig,
    context: Mapping[str, Any],
    *,
    expected_failure: str | None = None,
) -> RenderedNotification:
    """Render the top-level `notice:` block for one notification.

    `expected_failure`, forwarded to `render`, is set by the caller
    (`actions.registry._resolve_subject_for_templates`) when a `subject`
    fetch was skipped or failed for an expected reason - only templates that
    actually reference `subject` are affected.
    """
    title = render(
        config.title,
        context,
        label="notice.title",
        fallback=_static_default_title(context),
        expected_failure=expected_failure,
    )
    body = render(
        config.body,
        context,
        label="notice.body",
        fallback=_static_default_body(context),
        expected_failure=expected_failure,
    )
    return RenderedNotification(title=title, body=body)


def render_notify(
    config: NotifyActionConfig,
    notice: RenderedNotification,
    context: Mapping[str, Any],
    *,
    expected_failure: str | None = None,
) -> RenderedNotification:
    """Render a `notify` action's `title`/`body`, defaulting to `notice`.

    `title`/`body` are optional on `NotifyActionConfig` - when unset, the
    rendered `notice` value is used as-is (not re-rendered, since it already
    went through `render_notice`'s own fallback handling).
    """
    full_context = {**context, "notice": {"title": notice.title, "body": notice.body}}
    title = (
        render(
            config.title,
            full_context,
            label="notify.title",
            fallback=notice.title,
            expected_failure=expected_failure,
        )
        if config.title is not None
        else notice.title
    )
    body = (
        render(
            config.body,
            full_context,
            label="notify.body",
            fallback=notice.body,
            expected_failure=expected_failure,
        )
        if config.body is not None
        else notice.body
    )
    notification_dict = full_context["notification"]
    url = notification_dict["subject"].get("web_url")
    return RenderedNotification(title=title, body=body, url=url)
