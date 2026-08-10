"""Offline test harness for config filter rules.

Lets users write YAML test files (e.g. next to their config, under a `tests/`
directory) asserting that a given (partial) notification/subject matches the
expected rule and action - without any GitHub API access. Each case's
notification/subject/account partials live under an `input:` block, which can
also be set at the file level as shared defaults that every case's `input:`
deep-merges over. See doc/test-format.md for the test file schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jinja2
import yaml
from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass as pydantic_dataclass

from .. import templating
from ..actions.factory import resolve_action_config
from ..github.models import (
    GITHUB_NOTIFICATION_ADAPTER,
    GitHubIssue,
    GitHubNotification,
    GitHubPullRequest,
)
from ..rules import RuleMatcher
from ..versioning import TEST_VERSION, SchemaVersion, check_file_version
from .models import ActionKind, Config, DefaultAction

__all__ = [
    "CaseResult",
    "ExpectedResult",
    "RuleTestCase",
    "RuleTestFile",
    "TemplateResolutionError",
    "TestReport",
    "build_notification",
    "build_subject",
    "deep_merge",
    "resolve_config_templates",
    "resolve_parameters",
    "run_case",
    "run_test_file",
    "run_test_files",
]

_WHOLE_EXPRESSION_RE = re.compile(r"\A\{\{(.*)\}\}\Z", re.DOTALL)

DEFAULT_ACCOUNT: dict[str, Any] = {"github": {"username": "testuser"}}

_NOTIFICATION_DEFAULTS: dict[str, Any] = {
    "id": "test-notification",
    "reason": "subscribed",
    "unread": True,
    "updated_at": "2026-01-01T00:00:00Z",
    "subject": {"title": "Test Subject", "type": "Issue"},
    "repository": {"id": 1, "name": "repo", "full_name": "owner/repo"},
    "url": "https://api.github.com/notifications/threads/test-notification",
    "subscription_url": "https://api.github.com/notifications/threads/test-notification/subscription",
}

_ISSUE_DEFAULTS: dict[str, Any] = {
    "id": 1,
    "number": 1,
    "title": "Test Subject",
    "state": "open",
    "user": {"login": "someone", "id": 100, "type": "User"},
    "assignees": [],
    "labels": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

# `merged` is not a declared GitHubPullRequest field, but the model allows
# extra fields (real API responses always include it) - default it here so
# expressions like `subject.merged or ...` don't hit a missing-attribute
# error (StrictUndefined) when a test case doesn't care about it.
_PULL_REQUEST_DEFAULTS: dict[str, Any] = {
    **_ISSUE_DEFAULTS,
    "draft": False,
    "requested_reviewers": [],
    "requested_teams": [],
    "mergeable_state": None,
    "merged": False,
}


class TemplateResolutionError(ValueError):
    """A `{{ ... }}` reference in a test file could not be resolved."""


# ---------------------------------------------------------------------------
# Test file schema
# ---------------------------------------------------------------------------


@pydantic_dataclass(kw_only=True)
class ExpectedResult:
    rule: str | None
    action: ActionKind | None = None


EXPECTED_RESULT_ADAPTER: TypeAdapter[ExpectedResult] = TypeAdapter(ExpectedResult)


@pydantic_dataclass(kw_only=True)
class RuleTestCase:
    name: str
    parameters: list[Any] | str | None = None
    # `input.notification` / `input.subject` / `input.account`, deep-merged
    # over the file-level `input:` block (see `RuleTestFile.input`).
    input: dict[str, Any] = field(default_factory=dict)
    # Raw (unvalidated) dict rather than `ExpectedResult`: `rule`/`action` may
    # themselves contain `{{ parameter... }}` references, so they're only
    # template-resolved and validated per parameter, inside `run_case`.
    expect: dict[str, Any]


@pydantic_dataclass(kw_only=True)
class RuleTestFile:
    version: SchemaVersion = TEST_VERSION
    # File-level defaults for `input.notification` / `input.subject` /
    # `input.account`; each case's `input:` is deep-merged over this.
    input: dict[str, Any] = field(default_factory=dict)
    cases: list[RuleTestCase] = field(default_factory=list)


RULE_TEST_FILE_ADAPTER: TypeAdapter[RuleTestFile] = TypeAdapter(RuleTestFile)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    file: str
    case_name: str
    parameter: Any
    passed: bool
    expected_rule: str | None
    actual_rule: str | None
    expected_action: str | None
    actual_action: str | None
    error: str | None = None


@dataclass
class TestReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------


def resolve_config_templates(obj: Any, scope: dict[str, Any]) -> Any:
    """Resolve `{{ ... }}` references in a (possibly nested) YAML value.

    A string that is *exactly* `{{ expr }}` is replaced by the resolved
    value, keeping its real type (dict/list/scalar/bool) via
    `templating.evaluate`. A `{{ expr }}` embedded in a longer string is
    rendered to text instead, same as a `notice`/`notify` template.
    """
    if isinstance(obj, str):
        whole_match = _WHOLE_EXPRESSION_RE.fullmatch(obj)
        try:
            if whole_match:
                return templating.evaluate(whole_match.group(1).strip(), scope)
            if "{{" in obj:
                return templating.ENV.from_string(obj).render(scope)
        except jinja2.TemplateError as exc:
            raise TemplateResolutionError(f"Cannot resolve {obj!r}: {exc}") from exc
        return obj
    if isinstance(obj, dict):
        return {
            key: resolve_config_templates(value, scope) for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [resolve_config_templates(value, scope) for value in obj]
    return obj


def resolve_parameters(parameters: list[Any] | str | None, config: Config) -> list[Any]:
    """Expand a case's `parameters` into a concrete list.

    `None` means the case isn't parameterized and runs once. A string must be
    a whole `{{ ... }}` template resolving to a list (e.g.
    `{{ variables.spam_bots }}`); a list is used as-is.
    """
    if parameters is None:
        return [None]
    if isinstance(parameters, str):
        resolved = resolve_config_templates(parameters, {"variables": config.variables})
        if not isinstance(resolved, list):
            raise TemplateResolutionError(
                f"'parameters' template {parameters!r} must resolve to a list, "
                f"got {type(resolved).__name__}"
            )
        return resolved
    return parameters


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge `override` over `base`; lists/scalars in override replace."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged[key], value) if key in merged else value
        return merged
    return override


# ---------------------------------------------------------------------------
# Notification/subject construction
# ---------------------------------------------------------------------------


def build_notification(partial: dict[str, Any]) -> GitHubNotification:
    """Build a `GitHubNotification` from a partial dict merged over defaults."""
    merged = deep_merge(_NOTIFICATION_DEFAULTS, partial)
    subject = merged["subject"]
    if not subject.get("url"):
        kind = "pulls" if subject.get("type") == "PullRequest" else "issues"
        subject = {
            **subject,
            "url": f"https://api.github.com/repos/{merged['repository']['full_name']}/{kind}/1",
        }
        merged = {**merged, "subject": subject}
    return GITHUB_NOTIFICATION_ADAPTER.validate_python(merged)


def build_subject(
    partial: dict[str, Any], subject_type: str
) -> GitHubIssue | GitHubPullRequest:
    """Build a `GitHubIssue`/`GitHubPullRequest` from a partial dict merged over defaults."""
    if subject_type == "PullRequest":
        return GitHubPullRequest.model_validate(
            deep_merge(_PULL_REQUEST_DEFAULTS, partial)
        )
    return GitHubIssue.model_validate(deep_merge(_ISSUE_DEFAULTS, partial))


# ---------------------------------------------------------------------------
# Case/file execution
# ---------------------------------------------------------------------------


def run_case(
    config: Config,
    case: RuleTestCase,
    file_input: dict[str, Any],
    file_name: str,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    merged_input = deep_merge(file_input, case.input)
    for parameter in resolve_parameters(case.parameters, config):
        scope: dict[str, Any] = {"variables": config.variables}
        if parameter is not None:
            scope["parameter"] = parameter

        account = deep_merge(DEFAULT_ACCOUNT, merged_input.get("account") or {})
        scope["account"] = account

        # `expect.rule`/`expect.action` may themselves reference `{{ parameter... }}`
        # (e.g. one case asserting a different rule per parameter), so they're
        # resolved and validated here, per parameter - separately from the
        # notification/subject build below, so a failure here is reported
        # without a stale/missing expected value.
        try:
            expected = EXPECTED_RESULT_ADAPTER.validate_python(
                resolve_config_templates(case.expect, scope)
            )
        except Exception as exc:
            results.append(
                CaseResult(
                    file=file_name,
                    case_name=case.name,
                    parameter=parameter,
                    passed=False,
                    expected_rule=case.expect.get("rule"),
                    actual_rule=None,
                    expected_action=case.expect.get("action"),
                    actual_action=None,
                    error=str(exc),
                )
            )
            continue

        try:
            notification_partial = resolve_config_templates(
                merged_input.get("notification") or {}, scope
            )
            subject_partial = resolve_config_templates(
                merged_input.get("subject") or {}, scope
            )
            notification = build_notification(notification_partial)
            subject = build_subject(subject_partial, notification.subject.type)

            rule_matcher = RuleMatcher(
                config.rules,
                account=account,
                variables=config.variables,
            )
            matched = rule_matcher.find_matching_rule(
                notification, lambda *_args, _subject=subject: _subject
            )

            actual_rule = matched.id if matched else None
            if matched is not None:
                actual_kind, _ = resolve_action_config(
                    matched.action, config, rule_id=matched.id
                )
            else:
                actual_kind = (
                    ActionKind.NOTIFY
                    if config.default_action == DefaultAction.NOTIFY
                    else ActionKind.IGNORE
                )
            actual_action = actual_kind.value

            passed = actual_rule == expected.rule
            if passed and expected.action is not None:
                passed = actual_kind == expected.action

            results.append(
                CaseResult(
                    file=file_name,
                    case_name=case.name,
                    parameter=parameter,
                    passed=passed,
                    expected_rule=expected.rule,
                    actual_rule=actual_rule,
                    expected_action=expected.action.value
                    if expected.action is not None
                    else None,
                    actual_action=actual_action,
                )
            )
        except Exception as exc:
            results.append(
                CaseResult(
                    file=file_name,
                    case_name=case.name,
                    parameter=parameter,
                    passed=False,
                    expected_rule=expected.rule,
                    actual_rule=None,
                    expected_action=expected.action.value
                    if expected.action is not None
                    else None,
                    actual_action=None,
                    error=str(exc),
                )
            )
    return results


def run_test_file(config: Config, path: Path) -> list[CaseResult]:
    data = yaml.safe_load(path.read_text()) or {}
    check_file_version(
        data,
        TEST_VERSION,
        label=f"Test file {path}",
        remedy="Update the test file to a compatible version.",
    )
    test_file = RULE_TEST_FILE_ADAPTER.validate_python(data)
    results: list[CaseResult] = []
    for case in test_file.cases:
        results.extend(run_case(config, case, test_file.input, path.name))
    return results


def run_test_files(
    config: Config, tests_dir: Path, name_filter: str | None = None
) -> TestReport:
    report = TestReport()
    paths = sorted(set(tests_dir.glob("*.yaml")) | set(tests_dir.glob("*.yml")))
    for path in paths:
        for result in run_test_file(config, path):
            if name_filter and name_filter not in result.case_name:
                continue
            report.results.append(result)
    return report
