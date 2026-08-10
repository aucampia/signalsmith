import pytest

from signalsmith.actions import SkipAction
from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.notification.models import NotificationOutcome


@pytest.fixture
def notification() -> GitHubNotification:
    return GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test Issue", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )


def test_outcome_is_skipped(notification: GitHubNotification) -> None:
    action = SkipAction(notification, "my_rule")
    assert action.outcome == NotificationOutcome.SKIPPED


def test_execute_dry_run_prints_would_skip(
    notification: GitHubNotification, capsys: pytest.CaptureFixture[str]
) -> None:
    action = SkipAction(notification, "my_rule")
    action.execute(dry_run=True)

    captured = capsys.readouterr()
    assert "Would skip" in captured.out
    assert "my_rule" in captured.out


def test_execute_non_dry_run_prints_nothing(
    notification: GitHubNotification, capsys: pytest.CaptureFixture[str]
) -> None:
    action = SkipAction(notification, "my_rule")
    action.execute(dry_run=False)

    captured = capsys.readouterr()
    assert captured.out == ""
