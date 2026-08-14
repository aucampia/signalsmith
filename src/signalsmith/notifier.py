import logging
from functools import cache

from desktop_notifier.sync import DesktopNotifierSync
from pydantic.dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__: list[str] = []


@dataclass(frozen=True, kw_only=True)
class RenderedNotification:
    """A notify action's title/body after template rendering (see `templating.py`)."""

    title: str
    body: str
    url: str | None = None


@cache
def _get_notifier() -> DesktopNotifierSync:
    return DesktopNotifierSync(app_name="signalsmith")


def send_notification(rendered: RenderedNotification) -> None:
    """Fire-and-forget a plain notification: no click/dismiss/button support.

    Used by `run` always, and by `daemon` whenever it has no
    `NotificationDispatcher` (non-interactive, or dispatcher construction
    failed) - the process isn't guaranteed to stay alive to observe an
    interaction, so none is offered here.
    """
    logger.debug(
        "Sending notification: title=%r body=%r", rendered.title, rendered.body
    )
    try:
        _get_notifier().send(title=rendered.title, message=rendered.body)
    except Exception:
        logger.exception("Failed to send desktop notification")
