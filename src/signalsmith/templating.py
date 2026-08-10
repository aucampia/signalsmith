"""Jinja-based rendering for notification titles/bodies.

Two template kinds share one engine here:

- `notice.title`/`notice.body` (`config.models.NoticeConfig`) - the generic,
  rule-independent notice computed for every notification.
- `notify.title`/`notify.body` (`config.models.NotifyActionConfig`) - a rule's
  optional override, which may reference the already-rendered
  `notice.title`/`notice.body` via `notice` in scope.

Both use `StrictUndefined`: a template referencing something that isn't in
scope raises rather than silently rendering blank or verbatim (the old
`${...}` behavior in the now-deleted `notifier.format_template`). `render()`
catches that and falls back rather than dropping the notification - see
`actions/registry._build_notify` for the subject-fetch-then-render sequence
and the WARNING/ERROR split via `expected_failure`.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

import jinja2
import jinja2.meta
from pydantic.dataclasses import dataclass

from .config.models import NoticeConfig, NotifyActionConfig
from .github.models import (
    GITHUB_NOTIFICATION_ADAPTER,
    GitHubIssue,
    GitHubNotification,
    GitHubPullRequest,
)
from .notifier import RenderedNotification

logger = logging.getLogger(__name__)

__all__ = [
    "Notice",
    "build_context",
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

_TEMPLATE_CACHE: dict[str, jinja2.Template] = {}


def _compile(source: str) -> jinja2.Template:
    """Compile `source` once per process; every render reuses the same object."""
    template = _TEMPLATE_CACHE.get(source)
    if template is None:
        template = ENV.from_string(source)
        _TEMPLATE_CACHE[source] = template
    return template


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


@dataclass(frozen=True, kw_only=True)
class Notice:
    """A rendered generic notice - the output of `notice.title`/`notice.body`."""

    title: str
    body: str


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
    """Build the Jinja context - the same names/values CEL expressions see.

    `notification`/`account`/`variables` are the same JSON-mode values
    `cel_rules.RuleMatcher` builds its activation from, so a path that works
    in a rule `expression` works in a template. Two properties aren't part of
    that JSON dump - `GitHubSubject.web_url` and `GitHubRepository.org` are
    plain `@property`s (`github/models.py`) - and are injected here.
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
) -> Notice:
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
    return Notice(title=title, body=body)


def render_notify(
    config: NotifyActionConfig,
    notice: Notice,
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
