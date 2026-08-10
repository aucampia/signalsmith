"""Persistent, interactive desktop-notification dispatcher for `signalsmith daemon`.

`desktop_notifier`'s click/dismiss/button callbacks only fire while its
asyncio event loop is actively being pumped, and the D-Bus backend caches one
connection bound to whatever loop first drove it. So a single
`DesktopNotifier` must be driven by one dedicated event loop that keeps
running for as long as we want to catch interactions - this module owns
that loop on a background thread for the lifetime of the daemon process.
`signalsmith run` never uses this (see `notifier.send_notification` instead): a
one-shot process has no way to keep the loop alive long enough to matter.
"""

import asyncio
import concurrent.futures
import logging
import threading
import uuid
import webbrowser
from collections.abc import Sequence

from desktop_notifier import Button, Capability, DesktopNotifier

from .notifier import RenderedNotification

logger = logging.getLogger(__name__)

__all__: list[str] = []


class NotificationDispatcher:
    """Sends notifications via a persistent `DesktopNotifier` event loop."""

    def __init__(self, app_name: str = "signalsmith") -> None:
        self._notifier = DesktopNotifier(app_name=app_name)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="signalsmith-notify-loop", daemon=True
        )
        self._thread.start()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._pending: dict[str, threading.Event] = {}
        self._supports_buttons: bool | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _resolve(self, local_id: str) -> None:
        with self._condition:
            event = self._pending.pop(local_id, None)
            self._condition.notify_all()
        if event is not None:
            event.set()

    @property
    def supports_buttons(self) -> bool:
        """Whether the connected notification server advertises action buttons.

        Cached after the first check. On Linux, many minimal/tiling-WM
        notification servers don't render actions as clickable buttons even
        though the library will happily send them - this is an
        environment limitation, not something signalsmith can work around, so we
        just warn once rather than silently doing nothing.
        """
        if self._supports_buttons is None:
            future = asyncio.run_coroutine_threadsafe(
                self._notifier.get_capabilities(), self._loop
            )
            try:
                capabilities = future.result(timeout=5.0)
            except Exception:
                logger.warning(
                    "Could not query notification server capabilities; "
                    "assuming action buttons are unsupported",
                    exc_info=True,
                )
                self._supports_buttons = False
            else:
                self._supports_buttons = Capability.BUTTONS in capabilities
                if not self._supports_buttons:
                    logger.warning(
                        "The connected notification server does not advertise "
                        "action button support; notify_actions buttons may not "
                        "be visible or clickable"
                    )
        return self._supports_buttons

    def send(
        self, rendered: RenderedNotification, buttons: Sequence[Button] = ()
    ) -> None:
        """Send a notification; non-blocking. Click-to-open is unconditional."""
        local_id = uuid.uuid4().hex
        event = threading.Event()
        with self._condition:
            self._pending[local_id] = event

        def _on_clicked() -> None:
            if rendered.url:
                try:
                    webbrowser.open(rendered.url)
                except Exception:
                    logger.exception("Failed to open %s", rendered.url)
            self._resolve(local_id)

        def _on_dismissed() -> None:
            self._resolve(local_id)

        coro = self._notifier.send(
            title=rendered.title,
            message=rendered.message,
            buttons=buttons,
            on_clicked=_on_clicked,
            on_dismissed=_on_dismissed,
        )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _log_failure(fut: concurrent.futures.Future[str]) -> None:
            exc = fut.exception()
            if exc is not None:
                logger.warning("Failed to send desktop notification: %s", exc)
                self._resolve(local_id)

        future.add_done_callback(_log_failure)

    def wait_for_slot(self, max_concurrent: int, timeout: float | None = None) -> bool:
        """Block while `max_concurrent` notifications are already pending.

        Backs the sliding-window concurrency limit for `notify_actions`:
        callers should call this immediately before `send()` for each
        button-bearing notification. Returns False if `timeout` elapsed
        first (caller may send anyway).
        """
        with self._condition:
            return self._condition.wait_for(
                lambda: len(self._pending) < max_concurrent, timeout=timeout
            )

    def close(self, timeout: float = 5.0) -> None:
        """Stop the background loop/thread. Any pending sends are abandoned."""
        if not self._thread.is_alive():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)
