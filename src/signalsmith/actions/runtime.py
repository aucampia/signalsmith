"""Interactive-notification context, only constructed by `daemon`."""

from dataclasses import dataclass

from ..notify_dispatcher import NotificationDispatcher
from ..protocols import NotificationProvider
from ..state.ignore_store import IgnoreStore

__all__ = ["NotifyRuntime"]


@dataclass
class NotifyRuntime:
    """Interactive-notification context, only constructed by `daemon`.

    `run` (and `daemon --non-interactive`, or `daemon` falling back after a
    failed dispatcher start) never has one of these - `NotifyAction` treats
    `None` as "use the plain, non-interactive `send_notification` path".
    """

    dispatcher: NotificationDispatcher
    provider: NotificationProvider
    ignore_store: IgnoreStore
    max_concurrent: int
    wait_timeout: int
