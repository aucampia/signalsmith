from unittest.mock import MagicMock

import pytest

from signalsmith.config.models import NotifyActionConfig
from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.notifier import (
    RenderedNotification,
    format_template,
    render_notification,
    send_notification,
)


def _make_notification(subject_url: str | None = None) -> GitHubNotification:
    return GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test Issue", type="Issue", url=subject_url),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )


_NOTIFICATION_NO_SUBJECT_URL = _make_notification()
_NOTIFICATION_WITH_SUBJECT_URL = _make_notification(
    "https://api.github.com/repos/owner/repo/issues/1"
)


@pytest.mark.parametrize(
    ("notification", "template", "expected"),
    [
        (
            _NOTIFICATION_NO_SUBJECT_URL,
            "You were mentioned in: ${notification.subject.title}",
            "You were mentioned in: Test Issue",
        ),
        (
            _NOTIFICATION_NO_SUBJECT_URL,
            "${notification.repository.full_name} - ${notification.subject.title}",
            "owner/repo - Test Issue",
        ),
        (
            _NOTIFICATION_NO_SUBJECT_URL,
            "Test ${unknown.variable} here",
            "Test ${unknown.variable} here",
        ),
        (
            _NOTIFICATION_WITH_SUBJECT_URL,
            "${notification.subject.web_url}",
            "https://github.com/owner/repo/issues/1",
        ),
        (_NOTIFICATION_NO_SUBJECT_URL, "${notification.subject.web_url}", ""),
    ],
    ids=["simple", "multiple-vars", "unknown-var", "web-url", "web-url-empty"],
)
def test_format_template(
    notification: GitHubNotification, template: str, expected: str
) -> None:
    assert format_template(template, notification) == expected


@pytest.mark.parametrize(
    ("notification", "expected_url"),
    [
        (_NOTIFICATION_NO_SUBJECT_URL, None),
        (
            _NOTIFICATION_WITH_SUBJECT_URL,
            "https://github.com/owner/repo/issues/1",
        ),
    ],
    ids=["no-subject-url", "with-subject-url"],
)
def test_render_notification(
    notification: GitHubNotification, expected_url: str | None
) -> None:
    config = NotifyActionConfig(
        title="${notification.subject.type}: ${notification.subject.title}",
        message="${notification.repository.full_name} (${notification.reason})",
    )

    rendered = render_notification(config, notification)

    assert rendered.title == "Issue: Test Issue"
    assert rendered.message == "owner/repo (mention)"
    assert rendered.url == expected_url


def test_send_notification_calls_desktop_notifier_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_instance = MagicMock()
    mock_sync_cls = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("signalsmith.notifier.DesktopNotifierSync", mock_sync_cls)

    send_notification(RenderedNotification(title="Title", message="Message"))

    mock_sync_cls.assert_called_once_with(app_name="signalsmith")
    mock_instance.send.assert_called_once_with(title="Title", message="Message")


def test_send_notification_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_instance = MagicMock()
    mock_instance.send.side_effect = RuntimeError("boom")
    monkeypatch.setattr(
        "signalsmith.notifier.DesktopNotifierSync",
        MagicMock(return_value=mock_instance),
    )

    # Must not raise: a failed send must never propagate and crash NotifyAction.
    send_notification(RenderedNotification(title="Title", message="Message"))
