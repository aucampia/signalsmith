from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from signalsmith.actions import NotifyAction, NotifyRuntime
from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.notification.models import NotificationOutcome
from signalsmith.notifier import RenderedNotification
from signalsmith.state.spool import SpoolManager


@pytest.fixture
def notification() -> GitHubNotification:
    return GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test Issue",
            url="https://api.github.com/repos/owner/repo/issues/1",
            type="Issue",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )


@pytest.fixture
def spool(tmp_path: Path) -> SpoolManager:
    return SpoolManager(tmp_path / "spool", tmp_path / "trash")


def make_action(
    notification: GitHubNotification,
    spool: SpoolManager,
    notify_runtime: NotifyRuntime | None = None,
) -> NotifyAction:
    return NotifyAction(
        notification,
        RenderedNotification(title="Title", body="Message"),
        spool,
        "my_rule",
        provider_name="github",
        notify_runtime=notify_runtime,
    )


def test_outcome_is_notified(
    notification: GitHubNotification, spool: SpoolManager
) -> None:
    action = make_action(notification, spool)
    assert action.outcome == NotificationOutcome.NOTIFIED


def test_execute_without_runtime_uses_plain_send(
    notification: GitHubNotification,
    spool: SpoolManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_send = MagicMock()
    monkeypatch.setattr("signalsmith.actions.notify.send_notification", mock_send)

    action = make_action(notification, spool, notify_runtime=None)
    action.execute(dry_run=False)

    mock_send.assert_called_once()
    rendered = mock_send.call_args.args[0]
    assert rendered.title == "Title"
    assert rendered.body == "Message"


def test_execute_with_runtime_sends_dismiss_and_ignore_buttons(
    notification: GitHubNotification, spool: SpoolManager
) -> None:
    dispatcher = MagicMock()
    provider = MagicMock()
    ignore_store = MagicMock()
    runtime = NotifyRuntime(
        dispatcher=dispatcher,
        provider=provider,
        ignore_store=ignore_store,
        max_concurrent=5,
        wait_timeout=20,
    )

    action = make_action(notification, spool, notify_runtime=runtime)
    action.execute(dry_run=False)

    dispatcher.wait_for_slot.assert_called_once_with(5, timeout=20)
    _, kwargs = dispatcher.send.call_args
    buttons = kwargs["buttons"]
    assert [b.title for b in buttons] == ["Dismiss", "Ignore"]

    buttons[0].on_pressed()
    provider.mark_as_read.assert_called_once_with("123")

    buttons[1].on_pressed()
    ignore_store.add.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/issues/1", notification
    )


def test_execute_with_runtime_and_no_subject_url_omits_ignore_button(
    notification: GitHubNotification, spool: SpoolManager
) -> None:
    notification = replace(
        notification, subject=replace(notification.subject, url=None)
    )
    dispatcher = MagicMock()
    runtime = NotifyRuntime(
        dispatcher=dispatcher,
        provider=MagicMock(),
        ignore_store=MagicMock(),
        max_concurrent=5,
        wait_timeout=20,
    )

    action = make_action(notification, spool, notify_runtime=runtime)
    action.execute(dry_run=False)

    _, kwargs = dispatcher.send.call_args
    assert [b.title for b in kwargs["buttons"]] == ["Dismiss"]


def test_execute_dry_run_never_sends(
    notification: GitHubNotification,
    spool: SpoolManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_send = MagicMock()
    monkeypatch.setattr("signalsmith.actions.notify.send_notification", mock_send)
    dispatcher = MagicMock()
    runtime = NotifyRuntime(
        dispatcher=dispatcher,
        provider=MagicMock(),
        ignore_store=MagicMock(),
        max_concurrent=5,
        wait_timeout=20,
    )

    action = make_action(notification, spool, notify_runtime=runtime)
    action.execute(dry_run=True)

    mock_send.assert_not_called()
    dispatcher.send.assert_not_called()


def test_execute_dry_run_prints_rendered_title_and_body(
    notification: GitHubNotification,
    spool: SpoolManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    action = make_action(notification, spool)
    action.execute(dry_run=True)

    out = capsys.readouterr().out
    assert "Title" in out
    assert "Message" in out
