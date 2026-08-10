"""Skip a notification (renotify interval not elapsed)."""

import logging

from ..github.models import GitHubNotification
from ..notification.models import NotificationOutcome

logger = logging.getLogger(__name__)

__all__ = ["SkipAction"]


class SkipAction:
    """Skip a notification (renotify interval not elapsed)."""

    outcome = NotificationOutcome.SKIPPED

    def __init__(
        self,
        notification: GitHubNotification,
        rule_id: str,
    ) -> None:
        self.notification = notification
        self.rule_id = rule_id

    def execute(self, dry_run: bool = False) -> None:
        """Log/print that the notification was skipped."""
        logger.debug(
            "Notification %s matched rule %r but renotify interval not elapsed",
            self.notification.id,
            self.rule_id,
        )
        if dry_run:
            print(
                f"[DRY RUN] Would skip (renotify interval not elapsed, rule: {self.rule_id}): "
                f"{self.notification.subject.type} - {self.notification.subject.title}"
            )
