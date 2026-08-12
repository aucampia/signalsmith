from unittest.mock import MagicMock

import pytest

import signalsmith.notifier
from signalsmith.notifier import RenderedNotification, send_notification


@pytest.fixture(autouse=True)
def _clear_notifier_cache() -> None:
    signalsmith.notifier._get_notifier.cache_clear()


def test_send_notification_calls_desktop_notifier_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_instance = MagicMock()
    mock_sync_cls = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("signalsmith.notifier.DesktopNotifierSync", mock_sync_cls)

    send_notification(RenderedNotification(title="Title", body="Message"))

    mock_instance.send.assert_called_with(title="Title", message="Message")


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
    send_notification(RenderedNotification(title="Title", body="Message"))
