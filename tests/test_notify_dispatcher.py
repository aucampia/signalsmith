import time
from collections.abc import Sequence
from typing import Any

import pytest
from desktop_notifier import Button, Capability

from signalsmith.notifier import RenderedNotification
from signalsmith.notify_dispatcher import NotificationDispatcher


class FakeDesktopNotifier:
    """Stand-in for `desktop_notifier.DesktopNotifier`.

    Runs on the dispatcher's real background event loop (same as production),
    just without touching an actual OS notification backend. `sent` records
    each call's kwargs so a test can grab and invoke the callbacks directly
    to simulate a click/dismiss/button-press.
    """

    def __init__(
        self,
        app_name: str = "Python",
        app_icon: Any = None,
        notification_limit: Any = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.capabilities: frozenset[Capability] = frozenset()

    async def send(self, **kwargs: Any) -> str:
        self.sent.append(kwargs)
        return f"fake-{len(self.sent)}"

    async def get_capabilities(self) -> frozenset[Capability]:
        return self.capabilities


def _wait_until(predicate: Any, timeout: float = 2.0) -> None:
    """Poll until `predicate()` is true; the background loop runs on its own thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for background dispatcher thread")


@pytest.fixture
def fake_notifier(monkeypatch: pytest.MonkeyPatch) -> FakeDesktopNotifier:
    instance = FakeDesktopNotifier()
    monkeypatch.setattr(
        "signalsmith.notify_dispatcher.DesktopNotifier", lambda **kwargs: instance
    )
    return instance


@pytest.fixture
def dispatcher(fake_notifier: FakeDesktopNotifier) -> Any:
    d = NotificationDispatcher(app_name="test")
    yield d
    d.close()


def test_send_calls_notifier_with_title_and_message(
    dispatcher: NotificationDispatcher, fake_notifier: FakeDesktopNotifier
) -> None:
    dispatcher.send(RenderedNotification(title="Title", body="Message"))
    _wait_until(lambda: len(fake_notifier.sent) == 1)

    assert fake_notifier.sent[0]["title"] == "Title"
    assert fake_notifier.sent[0]["message"] == "Message"


def test_click_opens_url(
    dispatcher: NotificationDispatcher,
    fake_notifier: FakeDesktopNotifier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "signalsmith.notify_dispatcher.webbrowser.open", lambda url: opened.append(url)
    )

    dispatcher.send(
        RenderedNotification(title="T", body="M", url="https://example.com/1")
    )
    _wait_until(lambda: len(fake_notifier.sent) == 1)

    fake_notifier.sent[0]["on_clicked"]()

    assert opened == ["https://example.com/1"]


def test_click_without_url_does_not_open_browser(
    dispatcher: NotificationDispatcher,
    fake_notifier: FakeDesktopNotifier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "signalsmith.notify_dispatcher.webbrowser.open", lambda url: opened.append(url)
    )

    dispatcher.send(RenderedNotification(title="T", body="M"))
    _wait_until(lambda: len(fake_notifier.sent) == 1)

    fake_notifier.sent[0]["on_clicked"]()

    assert opened == []


def test_click_resolves_pending_slot(
    dispatcher: NotificationDispatcher, fake_notifier: FakeDesktopNotifier
) -> None:
    dispatcher.send(RenderedNotification(title="T", body="M"))
    _wait_until(lambda: len(fake_notifier.sent) == 1)

    assert dispatcher.wait_for_slot(max_concurrent=1, timeout=0.1) is False

    fake_notifier.sent[0]["on_clicked"]()

    assert dispatcher.wait_for_slot(max_concurrent=1, timeout=2.0) is True


def test_wait_for_slot_blocks_until_dismissed(
    dispatcher: NotificationDispatcher, fake_notifier: FakeDesktopNotifier
) -> None:
    dispatcher.send(RenderedNotification(title="T1", body="M"))
    dispatcher.send(RenderedNotification(title="T2", body="M"))
    _wait_until(lambda: len(fake_notifier.sent) == 2)

    assert dispatcher.wait_for_slot(max_concurrent=2, timeout=0.1) is False

    fake_notifier.sent[0]["on_dismissed"]()

    assert dispatcher.wait_for_slot(max_concurrent=2, timeout=2.0) is True


def test_supports_buttons_true_when_capability_advertised(
    dispatcher: NotificationDispatcher, fake_notifier: FakeDesktopNotifier
) -> None:
    fake_notifier.capabilities = frozenset({Capability.BUTTONS})
    assert dispatcher.supports_buttons is True


def test_supports_buttons_false_when_capability_missing(
    dispatcher: NotificationDispatcher, fake_notifier: FakeDesktopNotifier
) -> None:
    fake_notifier.capabilities = frozenset()
    assert dispatcher.supports_buttons is False


def test_send_passes_buttons_through(
    dispatcher: NotificationDispatcher, fake_notifier: FakeDesktopNotifier
) -> None:
    pressed: list[str] = []
    buttons: Sequence[Button] = [
        Button(title="Dismiss", on_pressed=lambda: pressed.append("dismiss"))
    ]

    dispatcher.send(RenderedNotification(title="T", body="M"), buttons=buttons)
    _wait_until(lambda: len(fake_notifier.sent) == 1)

    sent_buttons = fake_notifier.sent[0]["buttons"]
    assert len(sent_buttons) == 1
    sent_buttons[0].on_pressed()
    assert pressed == ["dismiss"]


def test_close_is_idempotent(
    dispatcher: NotificationDispatcher, fake_notifier: FakeDesktopNotifier
) -> None:
    dispatcher.close()
    dispatcher.close()  # must not raise
