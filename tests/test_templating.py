import logging

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


class TestRender:
    def test_basic_rendering(self) -> None:
        result = render(
            "hello {{ notification.id }}",
            {"notification": {"id": "123"}},
            label="t",
            fallback="fallback",
        )
        assert result == "hello 123"

    def test_strips_whitespace(self) -> None:
        result = render(
            "\n  {% if true %}\n  hi\n  {% endif %}\n  ",
            {},
            label="t",
            fallback="fallback",
        )
        assert result == "hi"

    def test_undefined_reference_falls_back(self) -> None:
        result = render(
            "{{ notification.nonexistent }}",
            {"notification": {}},
            label="t",
            fallback="fallback",
        )
        assert result == "fallback"

    def test_undefined_reference_logs_error_by_default(
        self, caplog: pytest.LogCaptureFixture
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

    def test_expected_failure_logs_warning_not_error(
        self, caplog: pytest.LogCaptureFixture
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
        assert (
            "subject type Release has no fetchable object" in caplog.records[0].message
        )

    def test_expected_failure_does_not_suppress_unrelated_errors(self) -> None:
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

    def test_expected_failure_ignored_on_syntax_error(
        self, caplog: pytest.LogCaptureFixture
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

    def test_expected_failure_ignored_when_template_does_not_reference_subject(
        self, caplog: pytest.LogCaptureFixture
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


class TestEvaluate:
    def test_returns_real_python_types(self) -> None:
        assert evaluate("notification.count", {"notification": {"count": 3}}) == 3
        assert evaluate(
            "variables.spam_bots", {"variables": {"spam_bots": ["a", "b"]}}
        ) == ["a", "b"]
        assert evaluate('a == "a"', {"a": "a"}) is True

    def test_bare_undefined_reference_raises(self) -> None:
        """A bare attribute access (no comparison/bool/str applied to it)
        must still raise - `StrictUndefined` only raises when something is
        *done* with the value, so this pins the explicit check in
        `evaluate` that forces it rather than silently returning an
        `Undefined` object."""
        with pytest.raises(jinja2.UndefinedError):
            evaluate("subject.merged", {"subject": {}})

    def test_nested_attribute_default_does_not_guard_parent(self) -> None:
        """|default([]) only guards the LAST hop. If the parent key is absent,
        accessing a nested attribute raises before the default filter runs.
        This is why variables.ontopic.prefixes|default([]) fails with an empty
        variables dict - it needs .get() chaining instead."""
        with pytest.raises(jinja2.UndefinedError, match="has no attribute 'ontopic'"):
            evaluate("variables.ontopic.prefixes|default([])", {"variables": {}})

    def test_get_chaining_guards_nested_access(self) -> None:
        """variables.get('ontopic', {}).get('prefixes', []) safely handles
        missing parent keys, returning the final default."""
        result = evaluate(
            "variables.get('ontopic', {}).get('prefixes', [])", {"variables": {}}
        )
        assert result == []

    def test_syntax_error_raises(self) -> None:
        with pytest.raises(jinja2.TemplateSyntaxError):
            evaluate("a == && b", {})

    def test_compile_expression_is_cached(self) -> None:
        assert compile_expression("a == b") is compile_expression("a == b")


class TestStartingwithTest:
    def test_select_filters_matching_prefixes(self) -> None:
        result = evaluate(
            "prefixes | select('startingwith', full_name) | list",
            {
                "prefixes": ["storebrand-technology/grc-", "other-"],
                "full_name": "storebrand-technology/grc-foo",
            },
        )
        assert result == ["storebrand-technology/grc-"]

    def test_select_excludes_non_matching_prefixes(self) -> None:
        result = evaluate(
            "prefixes | select('startingwith', full_name) | list",
            {"prefixes": ["other-"], "full_name": "storebrand-technology/grc-foo"},
        )
        assert result == []

    def test_registered_on_shared_environment(self) -> None:
        """Registered on `ENV` (not some other environment) so both rule
        expressions and `config.testing`'s `{{ }}` resolver see it."""
        assert "startingwith" in ENV.tests


class TestReferencesSubject:
    def test_true_when_subject_referenced(self) -> None:
        assert references_subject("{{ subject.user.login }}") is True

    def test_false_when_not_referenced(self) -> None:
        assert references_subject("{{ notification.id }}") is False

    def test_false_on_syntax_error(self) -> None:
        """A template that can't even be parsed is never treated as
        'references subject' - it's always a genuine problem, surfaced as an
        ERROR by `render` rather than silently downgraded."""
        assert references_subject("{{ subject.user.login") is False


class TestTemplateNames:
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
    def test_template_names(self, source: str, expected: set[str]) -> None:
        assert template_names(source) == expected


class TestBuildContext:
    def test_injects_web_url_and_org(self) -> None:
        notification = _make_notification(
            "https://api.github.com/repos/storebrand/repo/issues/1"
        )
        context = build_context(notification, None, {}, {})
        assert (
            context["notification"]["subject"]["web_url"]
            == "https://github.com/storebrand/repo/issues/1"
        )
        assert context["notification"]["repository"]["org"] == "storebrand"

    def test_web_url_none_when_no_subject_url(self) -> None:
        notification = _make_notification()
        context = build_context(notification, None, {}, {})
        assert context["notification"]["subject"]["web_url"] is None

    def test_subject_absent_when_not_given(self) -> None:
        context = build_context(_make_notification(), None, {}, {})
        assert "subject" not in context

    def test_subject_present_when_given(self) -> None:
        context = build_context(_make_notification(), _make_issue(), {}, {})
        assert context["subject"]["user"]["login"] == "someone"

    def test_account_and_variables_reachable(self) -> None:
        account = {"github": {"username": "aucampia"}}
        variables = {"spam_bots": ["dependabot[bot]"]}
        context = build_context(_make_notification(), None, account, variables)
        assert context["account"] == account
        assert context["variables"] == variables


class TestRenderNotice:
    def test_default_config_matches_builtin_default(self) -> None:
        """`NoticeConfig()`'s field defaults must render identically to the
        plain-Python fallback of last resort - they're kept textually in
        sync deliberately (see `templating._static_default_title`)."""
        notification = _make_notification()
        context = build_context(notification, None, {}, {})

        notice = render_notice(NoticeConfig(), context)

        assert notice.title == "Issue: Test Issue"
        assert notice.body == "storebrand/repo (mention)"

    def test_custom_templates_reach_subject_and_variables(self) -> None:
        notification = _make_notification()
        context = build_context(notification, _make_issue(), {}, {"team": "platform"})
        config = NoticeConfig(
            title="{{ subject.user.login }} ({{ variables.team }})",
            body="{{ notification.repository.full_name }}",
        )

        notice = render_notice(config, context)

        assert notice.title == "someone (platform)"
        assert notice.body == "storebrand/repo"

    def test_bad_title_falls_back_to_builtin_default(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        notification = _make_notification()
        context = build_context(notification, None, {}, {})
        config = NoticeConfig(title="{{ notification.nonexistent }}")

        with caplog.at_level(logging.WARNING):
            notice = render_notice(config, context)

        assert notice.title == "Issue: Test Issue"
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_expected_failure_propagates_to_warning(
        self, caplog: pytest.LogCaptureFixture
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


class TestRenderNotify:
    def test_unset_fields_default_to_notice(self) -> None:
        notification = _make_notification(
            "https://api.github.com/repos/storebrand/repo/issues/1"
        )
        context = build_context(notification, None, {}, {})
        notice = render_notice(NoticeConfig(), context)

        rendered = render_notify(NotifyActionConfig(), notice, context)

        assert rendered.title == notice.title
        assert rendered.body == notice.body
        assert rendered.url == "https://github.com/storebrand/repo/issues/1"

    def test_can_reference_notice(self) -> None:
        notification = _make_notification()
        context = build_context(notification, None, {}, {})
        notice = render_notice(NoticeConfig(), context)
        config = NotifyActionConfig(title="[urgent] {{ notice.title }}")

        rendered = render_notify(config, notice, context)

        assert rendered.title == "[urgent] Issue: Test Issue"
        assert rendered.body == notice.body

    def test_bad_title_falls_back_to_notice(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        notification = _make_notification()
        context = build_context(notification, None, {}, {})
        notice = render_notice(NoticeConfig(), context)
        config = NotifyActionConfig(title="{{ notification.nonexistent }}")

        with caplog.at_level(logging.WARNING):
            rendered = render_notify(config, notice, context)

        assert rendered.title == notice.title
        assert any(r.levelno == logging.ERROR for r in caplog.records)
