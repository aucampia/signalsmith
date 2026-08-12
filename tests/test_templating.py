import logging
from typing import Any

import jinja2
import pytest

from signalsmith.config.models import NoticeConfig, NotifyActionConfig
from signalsmith.github.models import (
    GitHubIssue,
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
    GitHubUser,
)
from signalsmith.templating import (
    ENV,
    LazySubject,
    SubjectFetchError,
    SubjectUnavailableError,
    build_context,
    compile_expression,
    evaluate,
    references_subject,
    render,
    render_notice,
    render_notify,
    template_names,
)


def _make_notification(subject_url: str | None = None) -> GitHubNotification:
    return GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test Issue", type="Issue", url=subject_url),
        repository=GitHubRepository(id=1, name="repo", full_name="storebrand/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )


def _make_issue() -> GitHubIssue:
    return GitHubIssue(
        id=1,
        number=1,
        title="Test Issue",
        state="open",
        user=GitHubUser(login="someone", id=100, type="User"),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_render_basic() -> None:
    result = render(
        "hello {{ notification.id }}",
        {"notification": {"id": "123"}},
        label="t",
        fallback="fallback",
    )
    assert result == "hello 123"


def test_render_strips_whitespace() -> None:
    result = render(
        "\n  {% if true %}\n  hi\n  {% endif %}\n  ",
        {},
        label="t",
        fallback="fallback",
    )
    assert result == "hi"


def test_render_undefined_reference_falls_back() -> None:
    result = render(
        "{{ notification.nonexistent }}",
        {"notification": {}},
        label="t",
        fallback="fallback",
    )
    assert result == "fallback"


def test_render_undefined_reference_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        render(
            "{{ notification.nonexistent }}",
            {"notification": {}},
            label="my.label",
            fallback="fallback",
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert "my.label" in caplog.records[0].message


def test_render_expected_failure_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = render(
            "{{ subject.user.login }}",
            {},
            label="my.label",
            fallback="fallback",
            expected_failure="subject type Release has no fetchable object",
        )
    assert result == "fallback"
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "subject type Release has no fetchable object" in caplog.records[0].message


def test_render_expected_failure_does_not_suppress_unrelated_errors() -> None:
    """`expected_failure` only changes the log level, not whether a
    successful render still succeeds normally."""
    result = render(
        "{{ notification.id }}",
        {"notification": {"id": "123"}},
        label="t",
        fallback="fallback",
        expected_failure="subject type Release has no fetchable object",
    )
    assert result == "123"


def test_render_expected_failure_ignored_on_syntax_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A syntax error is always ERROR, even with `expected_failure` set -
    `references_subject` can't even parse it, so it never qualifies for
    the WARNING downgrade."""
    with caplog.at_level(logging.WARNING):
        result = render(
            "{{ subject.user.login",
            {},
            label="my.label",
            fallback="fallback",
            expected_failure="subject type Release has no fetchable object",
        )
    assert result == "fallback"
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR


def test_render_expected_failure_ignored_when_template_does_not_reference_subject(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression test: `expected_failure` must not downgrade a failure
    in a template that never references `subject` - that failure is
    unrelated to the subject fetch and is a genuine problem (e.g. a
    typo), so it must still log at ERROR."""
    with caplog.at_level(logging.WARNING):
        result = render(
            "{{ notification.nonexistent }}",
            {"notification": {}},
            label="my.label",
            fallback="fallback",
            expected_failure="subject type Release has no fetchable object",
        )
    assert result == "fallback"
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def test_evaluate_returns_real_python_types() -> None:
    assert evaluate("notification.count", {"notification": {"count": 3}}) == 3
    assert evaluate(
        "variables.spam_bots", {"variables": {"spam_bots": ["a", "b"]}}
    ) == ["a", "b"]
    assert evaluate('a == "a"', {"a": "a"}) is True


def test_evaluate_bare_undefined_reference_raises() -> None:
    """A bare attribute access (no comparison/bool/str applied to it)
    must still raise - `StrictUndefined` only raises when something is
    *done* with the value, so this pins the explicit check in
    `evaluate` that forces it rather than silently returning an
    `Undefined` object."""
    with pytest.raises(jinja2.UndefinedError):
        evaluate("subject.merged", {"subject": {}})


def test_evaluate_nested_attribute_default_does_not_guard_parent() -> None:
    """|default([]) only guards the LAST hop. If the parent key is absent,
    accessing a nested attribute raises before the default filter runs.
    This is why variables.ontopic.prefixes|default([]) fails with an empty
    variables dict - it needs .get() chaining instead."""
    with pytest.raises(jinja2.UndefinedError, match="has no attribute 'ontopic'"):
        evaluate("variables.ontopic.prefixes|default([])", {"variables": {}})


def test_evaluate_get_chaining_guards_nested_access() -> None:
    """variables.get('ontopic', {}).get('prefixes', []) safely handles
    missing parent keys, returning the final default."""
    result = evaluate(
        "variables.get('ontopic', {}).get('prefixes', [])", {"variables": {}}
    )
    assert result == []


def test_evaluate_syntax_error_raises() -> None:
    with pytest.raises(jinja2.TemplateSyntaxError):
        evaluate("a == && b", {})


def test_evaluate_compile_expression_is_cached() -> None:
    assert compile_expression("a == b") is compile_expression("a == b")


# ---------------------------------------------------------------------------
# startingwith test
# ---------------------------------------------------------------------------


def test_startingwith_select_filters_matching_prefixes() -> None:
    result = evaluate(
        "prefixes | select('startingwith', full_name) | list",
        {
            "prefixes": ["storebrand-technology/grc-", "other-"],
            "full_name": "storebrand-technology/grc-foo",
        },
    )
    assert result == ["storebrand-technology/grc-"]


def test_startingwith_select_excludes_non_matching_prefixes() -> None:
    result = evaluate(
        "prefixes | select('startingwith', full_name) | list",
        {"prefixes": ["other-"], "full_name": "storebrand-technology/grc-foo"},
    )
    assert result == []


def test_startingwith_registered_on_shared_environment() -> None:
    """Registered on `ENV` (not some other environment) so both rule
    expressions and `config.testing`'s `{{ }}` resolver see it."""
    assert "startingwith" in ENV.tests


# ---------------------------------------------------------------------------
# references_subject
# ---------------------------------------------------------------------------


def test_references_subject_true_when_referenced() -> None:
    assert references_subject("{{ subject.user.login }}") is True


def test_references_subject_false_when_not_referenced() -> None:
    assert references_subject("{{ notification.id }}") is False


def test_references_subject_false_on_syntax_error() -> None:
    """A template that can't even be parsed is never treated as
    'references subject' - it's always a genuine problem, surfaced as an
    ERROR by `render` rather than silently downgraded."""
    assert references_subject("{{ subject.user.login") is False


# ---------------------------------------------------------------------------
# template_names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("{{ subject.user.login }}", {"subject"}),
        ("{% for a in subject.assignees %}{{ a.login }}{% endfor %}", {"subject"}),
        (
            "{{ notification.id }} {{ account.github.username }}",
            {"notification", "account"},
        ),
        ("plain text, no templates", set()),
        ("{{ notice.title }}", {"notice"}),
    ],
    ids=["attribute", "for-loop", "multiple", "none", "notice"],
)
def test_template_names(source: str, expected: set[str]) -> None:
    assert template_names(source) == expected


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------


def test_build_context_injects_web_url_and_org() -> None:
    notification = _make_notification(
        "https://api.github.com/repos/storebrand/repo/issues/1"
    )
    context = build_context(notification, None, {}, {})
    assert (
        context["notification"]["subject"]["web_url"]
        == "https://github.com/storebrand/repo/issues/1"
    )
    assert context["notification"]["repository"]["org"] == "storebrand"


def test_build_context_web_url_none_when_no_subject_url() -> None:
    notification = _make_notification()
    context = build_context(notification, None, {}, {})
    assert context["notification"]["subject"]["web_url"] is None


def test_build_context_subject_absent_when_not_given() -> None:
    context = build_context(_make_notification(), None, {}, {})
    assert "subject" not in context


def test_build_context_subject_present_when_given() -> None:
    context = build_context(_make_notification(), _make_issue(), {}, {})
    assert context["subject"]["user"]["login"] == "someone"


def test_build_context_account_and_variables_reachable() -> None:
    account = {"github": {"username": "aucampia"}}
    variables = {"spam_bots": ["dependabot[bot]"]}
    context = build_context(_make_notification(), None, account, variables)
    assert context["account"] == account
    assert context["variables"] == variables


# ---------------------------------------------------------------------------
# render_notice
# ---------------------------------------------------------------------------


def test_render_notice_default_config_matches_builtin_default() -> None:
    """`NoticeConfig()`'s field defaults must render identically to the
    plain-Python fallback of last resort - they're kept textually in
    sync deliberately (see `templating._static_default_title`)."""
    notification = _make_notification()
    context = build_context(notification, None, {}, {})

    notice = render_notice(NoticeConfig(), context)

    assert notice.title == "Issue: Test Issue"
    assert notice.body == "storebrand/repo (mention)"


def test_render_notice_custom_templates_reach_subject_and_variables() -> None:
    notification = _make_notification()
    context = build_context(notification, _make_issue(), {}, {"team": "platform"})
    config = NoticeConfig(
        title="{{ subject.user.login }} ({{ variables.team }})",
        body="{{ notification.repository.full_name }}",
    )

    notice = render_notice(config, context)

    assert notice.title == "someone (platform)"
    assert notice.body == "storebrand/repo"


def test_render_notice_bad_title_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notification = _make_notification()
    context = build_context(notification, None, {}, {})
    config = NoticeConfig(title="{{ notification.nonexistent }}")

    with caplog.at_level(logging.WARNING):
        notice = render_notice(config, context)

    assert notice.title == "Issue: Test Issue"
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_render_notice_expected_failure_propagates_to_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notification = _make_notification()
    context = build_context(notification, None, {}, {})
    config = NoticeConfig(body="{{ subject.user.login }}")

    with caplog.at_level(logging.WARNING):
        notice = render_notice(
            config,
            context,
            expected_failure="subject type Release has no fetchable object",
        )

    assert notice.body == "storebrand/repo (mention)"
    assert all(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# render_notify
# ---------------------------------------------------------------------------


def test_render_notify_unset_fields_default_to_notice() -> None:
    notification = _make_notification(
        "https://api.github.com/repos/storebrand/repo/issues/1"
    )
    context = build_context(notification, None, {}, {})
    notice = render_notice(NoticeConfig(), context)

    rendered = render_notify(NotifyActionConfig(), notice, context)

    assert rendered.title == notice.title
    assert rendered.body == notice.body
    assert rendered.url == "https://github.com/storebrand/repo/issues/1"


def test_render_notify_can_reference_notice() -> None:
    notification = _make_notification()
    context = build_context(notification, None, {}, {})
    notice = render_notice(NoticeConfig(), context)
    config = NotifyActionConfig(title="[urgent] {{ notice.title }}")

    rendered = render_notify(config, notice, context)

    assert rendered.title == "[urgent] Issue: Test Issue"
    assert rendered.body == notice.body


def test_render_notify_bad_title_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notification = _make_notification()
    context = build_context(notification, None, {}, {})
    notice = render_notice(NoticeConfig(), context)
    config = NotifyActionConfig(title="{{ notification.nonexistent }}")

    with caplog.at_level(logging.WARNING):
        rendered = render_notify(config, notice, context)

    assert rendered.title == notice.title
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# LazySubject
# ---------------------------------------------------------------------------


def test_lazy_subject_not_fetched_on_construction() -> None:
    """The fetcher should not be called on construction."""
    call_count = 0

    def raising_fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("should not be called")

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, raising_fetcher)

    assert call_count == 0
    assert repr(proxy) == "<LazySubject unfetched>"


def test_lazy_subject_attribute_access_fetches() -> None:
    """Accessing an attribute should fetch once and return the value."""
    call_count = 0

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    result = proxy.state
    assert result == "open"
    assert call_count == 1
    assert repr(proxy) == "<LazySubject fetched>"


def test_lazy_subject_repeated_access_memoizes() -> None:
    """Multiple accesses should only fetch once."""
    call_count = 0
    dump_count = 0

    class CountingIssue(GitHubIssue):
        def model_dump(self, **kwargs: Any) -> Any:
            nonlocal dump_count
            dump_count += 1
            return super().model_dump(**kwargs)

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        issue = _make_issue()
        return CountingIssue(
            id=issue.id,
            number=issue.number,
            title=issue.title,
            state=issue.state,
            user=issue.user,
            assignees=[],
            labels=[],
            created_at=issue.created_at,
            updated_at=issue.updated_at,
        )

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    _ = proxy.state
    _ = proxy.title
    _ = proxy.number

    assert call_count == 1
    assert dump_count == 1


def test_lazy_subject_repeated_access_across_evaluations() -> None:
    """Repeated access across multiple evaluate() calls should only fetch once."""
    call_count = 0

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)
    context = {"subject": proxy}

    evaluate("subject.state", context)
    evaluate("subject.title", context)
    evaluate("subject.number", context)

    assert call_count == 1


def test_lazy_subject_missing_field_returns_undefined() -> None:
    """A missing field should return Undefined with the same message as a plain dict."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    result = proxy.nonexistent

    assert isinstance(result, jinja2.Undefined)

    with pytest.raises(jinja2.UndefinedError, match="has no attribute 'nonexistent'"):
        str(result)


def test_lazy_subject_missing_field_same_as_plain_dict() -> None:
    """The Undefined for a missing field should match a plain dict's behavior."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    proxy_result = proxy.nonexistent

    plain_dict = {"state": "open"}
    dict_result = ENV.undefined(obj=plain_dict, name="nonexistent")

    assert isinstance(proxy_result, jinja2.Undefined)
    assert isinstance(dict_result, jinja2.Undefined)

    proxy_error = None
    dict_error = None
    try:
        str(proxy_result)
    except jinja2.UndefinedError as e:
        proxy_error = str(e)

    try:
        str(dict_result)
    except jinja2.UndefinedError as e:
        dict_error = str(e)

    assert proxy_error == dict_error


def test_lazy_subject_mapping_surface_getitem() -> None:
    """subject['key'] should work."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    assert proxy["state"] == "open"


def test_lazy_subject_mapping_surface_contains() -> None:
    """'key' in subject should work."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    assert "state" in proxy
    assert "nonexistent" not in proxy


def test_lazy_subject_mapping_surface_length() -> None:
    """subject|length should work."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    assert len(proxy) > 0


def test_lazy_subject_mapping_surface_get() -> None:
    """subject.get('key', default) should work."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    assert proxy.get("state") == "open"
    assert proxy.get("nonexistent", "default") == "default"


def test_lazy_subject_mapping_surface_dict_conversion() -> None:
    """dict(subject) should work."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)

    as_dict = dict(proxy)
    assert as_dict["state"] == "open"
    assert isinstance(as_dict, dict)


def test_lazy_subject_underscore_access_does_not_fetch() -> None:
    """Accessing underscore/dunder attributes should not trigger a fetch."""
    call_count = 0

    def raising_fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("should not be called")

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, raising_fetcher)

    with pytest.raises(AttributeError):
        _ = proxy._nope

    repr(proxy)

    assert call_count == 0


def test_lazy_subject_fetch_failure_raises_subject_fetch_error() -> None:
    """A fetch failure should raise SubjectFetchError with the original as __cause__."""
    original_error = RuntimeError("network timeout")

    def failing_fetcher(url: str, type: str, updated_at: str) -> Any:
        raise original_error

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, failing_fetcher)

    with pytest.raises(SubjectFetchError) as exc_info:
        _ = proxy.state

    assert exc_info.value.__cause__ is original_error
    assert "failed to fetch subject" in str(exc_info.value)
    assert exc_info.value.subject_type == "Issue"
    assert "issues/1" in exc_info.value.subject_url


def test_lazy_subject_fetch_failure_memoized() -> None:
    """A fetch failure should be memoized - second access re-raises the same error."""
    call_count = 0

    def failing_fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("network timeout")

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, failing_fetcher)

    with pytest.raises(SubjectFetchError):
        _ = proxy.state

    with pytest.raises(SubjectFetchError):
        _ = proxy.title

    assert call_count == 1


def test_lazy_subject_not_implemented_becomes_subject_unavailable() -> None:
    """NotImplementedError from the fetcher should become SubjectUnavailableError."""

    def unsupported_fetcher(url: str, type: str, updated_at: str) -> Any:
        raise NotImplementedError("unsupported subject type")

    notification = GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Discussion",
            type="Discussion",
            url="https://api.github.com/repos/o/r/discussions/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="o/r"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )
    proxy = LazySubject(notification, unsupported_fetcher)

    with pytest.raises(SubjectUnavailableError) as exc_info:
        _ = proxy.state

    assert "not supported" in str(exc_info.value)
    assert exc_info.value.subject_type == "Discussion"


def test_lazy_subject_missing_url_raises_subject_unavailable() -> None:
    """notification.subject.url is None should raise SubjectUnavailableError without calling fetcher."""
    call_count = 0

    def raising_fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("should not be called")

    notification = _make_notification(subject_url=None)
    proxy = LazySubject(notification, raising_fetcher)

    with pytest.raises(SubjectUnavailableError) as exc_info:
        _ = proxy.state

    assert "has no subject URL" in str(exc_info.value)
    assert call_count == 0


def test_lazy_subject_short_circuit_and() -> None:
    """Jinja's `and` should short-circuit and not fetch if the left side is False."""
    call_count = 0

    def raising_fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("should not be called")

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, raising_fetcher)
    context = {"subject": proxy}

    result = evaluate("false and subject.state", context)
    assert result is False
    assert call_count == 0


def test_lazy_subject_short_circuit_or() -> None:
    """Jinja's `or` should short-circuit and not fetch if the left side is True."""
    call_count = 0

    def raising_fetcher(url: str, type: str, updated_at: str) -> Any:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("should not be called")

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, raising_fetcher)
    context = {"subject": proxy}

    result = evaluate("true or subject.state", context)
    assert result is True
    assert call_count == 0


def test_lazy_subject_tojson_filter() -> None:
    """subject|tojson should work thanks to the _json_default policy."""

    def fetcher(url: str, type: str, updated_at: str) -> Any:
        return _make_issue()

    notification = _make_notification(
        subject_url="https://api.github.com/repos/o/r/issues/1"
    )
    proxy = LazySubject(notification, fetcher)
    context = {"subject": proxy}

    result = evaluate("subject|tojson", context)

    import json

    parsed = json.loads(result)
    assert parsed["state"] == "open"
