"""Ignore a notification (no desktop alert, stays unread on backend)."""

import logging

from ..github.models import GitHubNotification
from ..notification.models import NotificationOutcome

logger = logging.getLogger(__name__)

__all__ = ["IgnoreAction"]


class IgnoreAction:
    """Ignore a notification (no desktop alert, stays unread on backend)."""

    outcome = NotificationOutcome.IGNORED

    def __init__(
        self,
        notification: GitHubNotification,
        rule_id: str,
    ) -> None:
        self.notification = notification
        self.rule_id = rule_id

    def execute(self, dry_run: bool = False) -> None:
        """Log that the notification is being ignored."""
        logger.info(
            "Matched rule %r for notification %s: ignoring (no alert, stays unread)",
            self.rule_id,
            self.notification.id,
        )
        if dry_run:
            print(
                f"[DRY RUN] Would ignore (rule: {self.rule_id}): "
                f"{self.notification.subject.type} - {self.notification.subject.title} "
                f"({self.notification.repository.full_name}, reason: {self.notification.reason})"
            )
