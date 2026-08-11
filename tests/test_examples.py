"""Validate the example config and test files in examples/.

These files are referenced from README.md/CONTRIBUTING.md as copy-paste starting
points, so they must both parse as valid Config objects and render their
notice/notify templates without a `TemplateError` (which `templating.render`
would otherwise silently paper over by falling back - so this checks for the
ERROR log a genuine template problem produces, not just the rendered text).
"""

import logging
from pathlib import Path

import pytest

from signalsmith.config.models import Config
from signalsmith.config.testing import CaseResult, run_test_files
from signalsmith.github.models import (
    GitHubIssue,
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
    GitHubUser,
)
from signalsmith.templating import build_context, render_notice, render_notify

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

_SAMPLE_NOTIFICATION = GitHubNotification(
    id="1",
    reason="mention",
    unread=True,
    updated_at="2026-06-17T00:00:00Z",
    subject=GitHubSubject(title="Sample title", type="Issue"),
    repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
    url="https://api.github.com/notifications/threads/1",
    subscription_url="https://api.github.com/notifications/threads/1/subscription",
)

_SAMPLE_ISSUE = GitHubIssue(
    id=1,
    number=1,
    title="Sample title",
    state="open",
    user=GitHubUser(login="someone", id=100, type="User"),
    created_at="2026-01-01T00:00:00Z",
    updated_at="2026-01-01T00:00:00Z",
)


def _case_results() -> list[CaseResult]:
    """Load example config and run all example tests, returning case results."""
    config = Config.load(EXAMPLES_DIR / "config.yaml")
    return run_test_files(config, EXAMPLES_DIR / "tests").results


_RESULTS = _case_results()


def test_example_config_loads() -> None:
    """The example config must parse as a valid signalsmith Config."""
    config = Config.load(EXAMPLES_DIR / "config.yaml")
    assert config.rules


def test_example_tests_are_discovered() -> None:
    """At least one example test case must be discovered."""
    assert _RESULTS, "No example test cases found"


@pytest.mark.parametrize(
    "result",
    _RESULTS,
    ids=[f"{r.file}-{i}-{r.case_name}" for i, r in enumerate(_RESULTS)],
)
def test_example_case_passes(result: CaseResult) -> None:
    """Every example test case must pass."""
    assert result.passed, (
        f"{result.file}: {result.case_name} (parameter={result.parameter!r}): "
        f"expected rule={result.expected_rule} action={result.expected_action}, "
        f"got rule={result.actual_rule} action={result.actual_action}"
        + (f" - {result.error}" if result.error else "")
    )


def test_example_config_templates_render_cleanly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`notice`/`notify` templates must render without a `TemplateError`.

    A sample subject is provided so templates referencing `subject` (e.g.
    `subject.user.login`) are exercised too, not just the notification-only
    path.
    """
    config = Config.load(EXAMPLES_DIR / "config.yaml")
    context = build_context(_SAMPLE_NOTIFICATION, _SAMPLE_ISSUE, {}, config.variables)

    notify_configs = [a.notify for a in config.actions.values() if a.notify]
    notify_configs += [r.action.notify for r in config.rules if r.action.notify]

    with caplog.at_level(logging.WARNING):
        notice = render_notice(config.notice, context)
        for notify_config in notify_configs:
            render_notify(notify_config, notice, context)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"Template rendering error(s): {[r.message for r in errors]}"
