"""Mark a notification as read."""

import logging

from ..github.models import GitHubNotification
from ..notification.models import NotificationOutcome
from ..protocols import NotificationProvider

logger = logging.getLogger(__name__)

__all__ = ["MarkAsReadAction"]


class MarkAsReadAction:
    """Mark a notification as read."""

    outcome = NotificationOutcome.MARKED_AS_READ

    def __init__(
        self,
        notification: GitHubNotification,
        provider: NotificationProvider,
        rule_id: str,
    ) -> None:
        self.notification = notification
        self.provider = provider
        self.rule_id = rule_id

    def execute(self, dry_run: bool = False) -> None:
        """Mark the notification as read or print dry-run message."""
        logger.info(
            "Matched rule %r for notification %s: marking as read",
            self.rule_id,
            self.notification.id,
        )
        if dry_run:
            print(
                f"[DRY RUN] Would mark as read (rule: {self.rule_id}): "
                f"{self.notification.subject.type} - {self.notification.subject.title} "
                f"({self.notification.repository.full_name}, reason: {self.notification.reason})"
            )
        else:
            self.provider.mark_as_read(self.notification.id)
