import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from signalsmith.actions import resolve_action_config
from signalsmith.config.models import (
    ActionDefinition,
    ActionKind,
    Config,
    DefaultAction,
    IgnoreActionConfig,
    MarkAsReadActionConfig,
    NotifyActionConfig,
    Rule,
    RuleAction,
)
from signalsmith.config.testing import (
    EXPECTED_RESULT_ADAPTER,
    RuleTestCase,
    TemplateResolutionError,
    build_notification,
    build_subject,
    deep_merge,
    resolve_config_templates,
    resolve_parameters,
    run_case,
    run_test_file,
    run_test_files,
)
from signalsmith.github.models import GitHubPullRequest
from signalsmith.versioning import VersionError

# ---------------------------------------------------------------------------
# resolve_config_templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "scope", "expected"),
    [
        (
            "${variables.spam_bots}",
            {"variables": {"spam_bots": ["a", "b"]}},
            ["a", "b"],
        ),
        ("owner/${parameter}-suffix", {"parameter": "myrepo"}, "owner/myrepo-suffix"),
        (
            "${parameter.org}",
            {"parameter": {"org": "acme", "id": "some-team"}},
            "acme",
        ),
        (
            "${parameter.id}",
            {"parameter": {"org": "acme", "id": "some-team"}},
            "some-team",
        ),
        ("no templates here", {}, "no templates here"),
    ],
    ids=[
        "whole-value",
        "interpolation",
        "dotted-path-org",
        "dotted-path-id",
        "passthrough",
    ],
)
def test_resolve_config_templates(
    template: str, scope: dict[str, Any], expected: Any
) -> None:
    assert resolve_config_templates(template, scope) == expected


def test_resolve_config_templates_unknown_reference_raises() -> None:
    with pytest.raises(TemplateResolutionError):
        resolve_config_templates("${parameter.missing}", {"parameter": {}})


def test_resolve_config_templates_recurses_into_dicts_and_lists() -> None:
    scope = {"parameter": "x"}
    obj = {"a": ["${parameter}", {"b": "${parameter}"}], "c": 1, "d": None}
    assert resolve_config_templates(obj, scope) == {
        "a": ["x", {"b": "x"}],
        "c": 1,
        "d": None,
    }


# ---------------------------------------------------------------------------
# resolve_parameters
# ---------------------------------------------------------------------------


def _config_with_variables(variables: dict[str, object]) -> Config:
    return Config(
        rules=[
            Rule(
                id="noop",
                expression="true",
                action=RuleAction(ignore=IgnoreActionConfig()),
            )
        ],
        variables=variables,
    )


@pytest.mark.parametrize(
    ("variables", "parameters", "expected"),
    [
        ({}, None, [None]),
        ({}, ["a", "b"], ["a", "b"]),
        (
            {"spam_bots": ["dependabot[bot]", "renovate[bot]"]},
            "${variables.spam_bots}",
            ["dependabot[bot]", "renovate[bot]"],
        ),
    ],
    ids=["none-sentinel", "inline-list", "from-variables-template"],
)
def test_resolve_parameters(
    variables: dict[str, Any], parameters: list[Any] | str | None, expected: list[Any]
) -> None:
    config = _config_with_variables(variables)
    assert resolve_parameters(parameters, config) == expected


def test_resolve_parameters_non_list_template_raises() -> None:
    config = _config_with_variables({"spam_bots": "not-a-list"})
    with pytest.raises(TemplateResolutionError):
        resolve_parameters("${variables.spam_bots}", config)


# ---------------------------------------------------------------------------
# deep_merge / resolve_account
# ---------------------------------------------------------------------------


def test_deep_merge_nested_dict_and_list_replacement() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": [1, 2]}
    override = {"a": {"b": 99}, "d": [3]}
    assert deep_merge(base, override) == {"a": {"b": 99, "c": 2}, "d": [3]}


def test_deep_merge_does_not_mutate_base() -> None:
    base = {"a": {"b": 1}}
    deep_merge(base, {"a": {"b": 2}})
    assert base == {"a": {"b": 1}}


# ---------------------------------------------------------------------------
# build_notification / build_subject
# ---------------------------------------------------------------------------


def test_build_notification_applies_defaults() -> None:
    notification = build_notification({})
    assert notification.id == "test-notification"
    assert notification.subject.type == "Issue"
    assert notification.repository.full_name == "owner/repo"


def test_build_notification_partial_override_merges() -> None:
    notification = build_notification(
        {
            "repository": {"full_name": "acme/widgets"},
            "subject": {"type": "PullRequest"},
        }
    )
    assert notification.repository.full_name == "acme/widgets"
    assert notification.subject.type == "PullRequest"
    assert notification.reason == "subscribed"  # untouched default


def test_build_notification_derives_subject_url_from_type() -> None:
    issue_notification = build_notification({"subject": {"type": "Issue"}})
    assert "/issues/" in (issue_notification.subject.url or "")

    pr_notification = build_notification({"subject": {"type": "PullRequest"}})
    assert "/pulls/" in (pr_notification.subject.url or "")


def test_build_subject_issue_defaults() -> None:
    subject = build_subject({}, "Issue")
    assert subject.state == "open"
    assert subject.user.login == "someone"


def test_build_subject_pull_request_has_merged_default() -> None:
    pr = build_subject({}, "PullRequest")
    assert isinstance(pr, GitHubPullRequest)
    # "merged" is an extra (undeclared) field - not a typed attribute, so it's
    # checked via the serialized form rather than static attribute access.
    assert pr.model_dump()["merged"] is False
    assert pr.requested_teams == []


def test_build_subject_pull_request_override_merged() -> None:
    pr = build_subject({"merged": True, "state": "closed"}, "PullRequest")
    assert isinstance(pr, GitHubPullRequest)
    assert pr.model_dump()["merged"] is True
    assert pr.state == "closed"


# ---------------------------------------------------------------------------
# resolve_action_config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected_kind"),
    [
        (
            RuleAction(notify=NotifyActionConfig(title="t", message="m")),
            ActionKind.NOTIFY,
        ),
        (RuleAction(mark_as_read=MarkAsReadActionConfig()), ActionKind.MARK_AS_READ),
    ],
    ids=["notify", "mark_as_read"],
)
def test_resolve_action_config_inline(
    action: RuleAction, expected_kind: ActionKind
) -> None:
    config = _config_with_variables({})
    kind, _ = resolve_action_config(action, config)
    assert kind == expected_kind


def test_resolve_action_config_ref() -> None:
    config = Config(
        actions={"dismiss": ActionDefinition(ignore=IgnoreActionConfig())},
        rules=[Rule(id="noop", expression="true", action=RuleAction(ref="dismiss"))],
    )
    kind, _ = resolve_action_config(RuleAction(ref="dismiss"), config)
    assert kind == ActionKind.IGNORE


# ---------------------------------------------------------------------------
# run_case
# ---------------------------------------------------------------------------


def _bot_pr_config() -> Config:
    return Config(
        variables={"spam_bots": ["dependabot[bot]", "renovate[bot]"]},
        rules=[
            Rule(
                id="bot_pr_mark_as_read",
                expression='notification.subject.type == "PullRequest"',
                subject_expression="subject.user.login in variables.spam_bots",
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            ),
        ],
    )


def test_run_case_matches_expected_rule_and_action() -> None:
    config = _bot_pr_config()
    case = RuleTestCase(
        name="bot PR marked as read",
        input={
            "notification": {"subject": {"type": "PullRequest"}},
            "subject": {"user": {"login": "dependabot[bot]"}},
        },
        expect={"rule": "bot_pr_mark_as_read", "action": "mark_as_read"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert len(results) == 1
    assert results[0].passed
    assert results[0].actual_rule == "bot_pr_mark_as_read"
    assert results[0].actual_action == "mark_as_read"


def test_run_case_parameterized_over_inline_list() -> None:
    config = _bot_pr_config()
    case = RuleTestCase(
        name="every spam bot triggers",
        parameters=["dependabot[bot]", "renovate[bot]"],
        input={
            "notification": {"subject": {"type": "PullRequest"}},
            "subject": {"user": {"login": "${parameter}"}},
        },
        expect={"rule": "bot_pr_mark_as_read"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert len(results) == 2
    assert all(r.passed for r in results)
    assert [r.parameter for r in results] == ["dependabot[bot]", "renovate[bot]"]


def test_run_case_parameterized_from_variables() -> None:
    config = _bot_pr_config()
    case = RuleTestCase(
        name="every configured spam bot triggers",
        parameters="${variables.spam_bots}",
        input={
            "notification": {"subject": {"type": "PullRequest"}},
            "subject": {"user": {"login": "${parameter}"}},
        },
        expect={"rule": "bot_pr_mark_as_read"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert len(results) == 2
    assert all(r.passed for r in results)


def test_run_case_no_match_falls_to_default_action() -> None:
    config = _bot_pr_config()
    case = RuleTestCase(
        name="unrelated issue notifies by default",
        input={"notification": {"subject": {"type": "Issue"}}},
        expect={"rule": None, "action": "notify"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert results[0].passed
    assert results[0].actual_rule is None
    assert results[0].actual_action == "notify"


def test_run_case_wrong_expected_rule_fails() -> None:
    config = _bot_pr_config()
    case = RuleTestCase(
        name="expects the wrong rule",
        input={
            "notification": {"subject": {"type": "PullRequest"}},
            "subject": {"user": {"login": "dependabot[bot]"}},
        },
        expect={"rule": "some_other_rule"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert not results[0].passed
    assert results[0].actual_rule == "bot_pr_mark_as_read"


def test_run_case_right_rule_wrong_action_fails() -> None:
    config = _bot_pr_config()
    case = RuleTestCase(
        name="right rule, wrong expected action",
        input={
            "notification": {"subject": {"type": "PullRequest"}},
            "subject": {"user": {"login": "dependabot[bot]"}},
        },
        expect={"rule": "bot_pr_mark_as_read", "action": "notify"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert not results[0].passed
    assert results[0].actual_action == "mark_as_read"


@pytest.mark.parametrize(
    "case",
    [
        RuleTestCase(
            name="bad input template",
            input={"notification": {"subject": {"type": "${parameter.nope}"}}},
            expect={"rule": None},
        ),
        RuleTestCase(
            name="bad expect template",
            input={"notification": {"subject": {"type": "Issue"}}},
            expect={"rule": "${parameter.nope}"},
        ),
        RuleTestCase(
            name="invalid expect.action literal",
            input={"notification": {"subject": {"type": "Issue"}}},
            expect={"rule": None, "action": "not-a-real-action"},
        ),
    ],
    ids=["bad-input-template", "bad-expect-template", "invalid-expect-action"],
)
def test_run_case_reports_error_not_raise(case: RuleTestCase) -> None:
    config = _bot_pr_config()
    results = run_case(config, case, {}, "test.yaml")
    assert not results[0].passed
    assert results[0].error is not None


def test_run_case_uses_account_for_username_expressions() -> None:
    config = Config(
        rules=[
            Rule(
                id="reviewer_or_assignee",
                expression='notification.subject.type == "PullRequest"',
                subject_expression="subject.assignees.exists(a, a.login == account.github.username)",
                action=RuleAction(notify=NotifyActionConfig(title="t", message="m")),
            )
        ],
    )
    case = RuleTestCase(
        name="assigned to me",
        input={
            "account": {"github": {"username": "testuser"}},
            "notification": {"subject": {"type": "PullRequest"}},
            "subject": {"assignees": [{"login": "testuser", "id": 1, "type": "User"}]},
        },
        expect={"rule": "reviewer_or_assignee", "action": "notify"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert results[0].passed


def test_run_case_file_input_provides_default_overridden_by_case_input() -> None:
    """File-level `input:` is a default that case-level `input:` overrides."""
    config = _bot_pr_config()
    file_input = {
        "account": {"github": {"username": "file-user"}},
        "notification": {"subject": {"type": "PullRequest"}},
        "subject": {"user": {"login": "renovate[bot]"}},
    }
    case = RuleTestCase(
        name="case overrides just the login",
        input={"subject": {"user": {"login": "dependabot[bot]"}}},
        expect={"rule": "bot_pr_mark_as_read"},
    )
    results = run_case(config, case, file_input, "test.yaml")
    assert results[0].passed


def test_run_case_expect_can_reference_parameter() -> None:
    """`expect.rule`/`expect.action` may themselves use `${parameter...}`."""
    config = Config(
        rules=[
            Rule(
                id="rule_a",
                expression='notification.repository.full_name.startsWith("a-")',
                action=RuleAction(ignore=IgnoreActionConfig()),
            ),
            Rule(
                id="rule_b",
                expression='notification.repository.full_name.startsWith("b-")',
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            ),
        ],
    )
    case = RuleTestCase(
        name="rule id and action vary by parameter",
        parameters=[
            {"prefix": "a-", "rule": "rule_a", "action": "ignore"},
            {"prefix": "b-", "rule": "rule_b", "action": "mark_as_read"},
        ],
        input={
            "notification": {"repository": {"full_name": "${parameter.prefix}repo"}}
        },
        expect={"rule": "${parameter.rule}", "action": "${parameter.action}"},
    )
    results = run_case(config, case, {}, "test.yaml")
    assert len(results) == 2
    assert all(r.passed for r in results)
    assert [r.actual_rule for r in results] == ["rule_a", "rule_b"]


# ---------------------------------------------------------------------------
# expect.rule required key
# ---------------------------------------------------------------------------


def test_expected_result_requires_rule_key() -> None:
    with pytest.raises(ValidationError):
        EXPECTED_RESULT_ADAPTER.validate_python({"action": "notify"})


def test_expected_result_rule_null_is_valid() -> None:
    expected = EXPECTED_RESULT_ADAPTER.validate_python({"rule": None})
    assert expected.rule is None


# ---------------------------------------------------------------------------
# run_test_files: discovery, aggregation, filtering
# ---------------------------------------------------------------------------


def _write_bot_config_test_file(path: Path) -> None:
    # Block style throughout: unquoted "${...}" inside a flow mapping ("{ }")
    # breaks the YAML parser, since "{" is a flow indicator there.
    path.write_text(
        """
version: '1.0'
cases:
  - name: bot PR marked as read
    parameters: ${variables.spam_bots}
    input:
      notification:
        subject:
          type: PullRequest
      subject:
        user:
          login: ${parameter}
    expect:
      rule: bot_pr_mark_as_read
      action: mark_as_read
"""
    )


def test_run_test_files_discovers_and_aggregates(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    _write_bot_config_test_file(tests_dir / "spam-bots.yaml")

    config = _bot_pr_config()
    report = run_test_files(config, tests_dir)

    assert len(report.results) == 2
    assert report.passed == 2
    assert report.failed == 0
    assert all(r.file == "spam-bots.yaml" for r in report.results)


def test_run_test_files_name_filter(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "a.yaml").write_text(
        """
version: '1.0'
input:
  notification: { subject: { type: Issue } }
cases:
  - name: case one
    expect: { rule: null, action: notify }
  - name: case two
    expect: { rule: null, action: notify }
"""
    )

    config = Config(
        rules=[
            Rule(
                id="noop",
                expression="false",
                action=RuleAction(ignore=IgnoreActionConfig()),
            )
        ],
        default_action=DefaultAction.NOTIFY,
    )
    report = run_test_files(config, tests_dir, name_filter="one")
    assert len(report.results) == 1
    assert report.results[0].case_name == "case one"


def test_run_test_files_empty_dir_returns_no_results(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    report = run_test_files(_bot_pr_config(), tests_dir)
    assert report.results == []


# ---------------------------------------------------------------------------
# test file version checking
# ---------------------------------------------------------------------------


def test_run_test_file_without_version_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_text("cases: []\n")
    with pytest.raises(VersionError):
        run_test_file(_bot_pr_config(), path)


def test_run_test_file_wrong_major_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_text("version: '2.0'\ncases: []\n")
    with pytest.raises(VersionError) as exc_info:
        run_test_file(_bot_pr_config(), path)
    assert "Update the test file" in str(exc_info.value)


@pytest.mark.parametrize(
    ("version", "expect_warning"),
    [("1.0", False), ("1.9", True)],
    ids=["matching", "newer-minor"],
)
def test_run_test_file_compatible_version_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, version: str, expect_warning: bool
) -> None:
    path = tmp_path / "a.yaml"
    path.write_text(f"version: '{version}'\ncases: []\n")
    with caplog.at_level(logging.WARNING):
        assert run_test_file(_bot_pr_config(), path) == []
    assert any("newer" in record.message for record in caplog.records) == expect_warning
