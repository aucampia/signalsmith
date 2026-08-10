"""Send a desktop notification."""

import logging

from desktop_notifier import Button

from ..config.models import Rule
from ..github.models import GitHubIssue, GitHubNotification, GitHubPullRequest
from ..notification.models import NotificationOutcome
from ..notifier import RenderedNotification, send_notification
from ..state.spool import SpoolManager
from .runtime import NotifyRuntime

logger = logging.getLogger(__name__)

__all__ = ["NotifyAction"]


class NotifyAction:
    """Send a desktop notification."""

    outcome = NotificationOutcome.NOTIFIED

    def __init__(
        self,
        notification: GitHubNotification,
        rendered: RenderedNotification,
        spool: SpoolManager,
        rule_id: str,
        *,
        rule: Rule | None = None,
        subject: GitHubIssue | GitHubPullRequest | None = None,
        provider_name: str = "",
        notify_runtime: NotifyRuntime | None = None,
    ) -> None:
        self.notification = notification
        self.rendered = rendered
        self.spool = spool
        self.rule_id = rule_id
        self.rule = rule
        self.subject = subject
        self.provider_name = provider_name
        self.notify_runtime = notify_runtime

    def _send(self, rendered: RenderedNotification) -> None:
        """Send via the interactive dispatcher if available, else fire-and-forget."""
        if self.notify_runtime is None:
            send_notification(rendered)
            return

        buttons: list[Button] = []
        if self.notify_runtime.actions_enabled:
            self.notify_runtime.dispatcher.wait_for_slot(
                self.notify_runtime.max_concurrent,
                timeout=self.notify_runtime.wait_timeout,
            )
            notification = self.notification
            runtime = self.notify_runtime

            def _on_dismiss() -> None:
                runtime.provider.mark_as_read(notification.id)

            buttons.append(Button(title="Dismiss", on_pressed=_on_dismiss))

            subject_url = notification.subject.url
            if subject_url is not None:

                def _on_ignore() -> None:
                    runtime.ignore_store.add(subject_url, notification)

                buttons.append(Button(title="Ignore", on_pressed=_on_ignore))

        self.notify_runtime.dispatcher.send(rendered, buttons=buttons)

    def execute(self, dry_run: bool = False) -> None:
        """Send the notification or print dry-run message."""
        logger.info(
            "Matched rule %r for notification %s: sending notification",
            self.rule_id,
            self.notification.id,
        )
        if dry_run:
            print(
                f"[DRY RUN] Would send notification (rule: {self.rule_id}): "
                f"{self.rendered.title} - {self.rendered.body}"
            )
            return

        self._send(self.rendered)
        try:
            self.spool.record_notify(
                provider=self.provider_name,
                notification=self.notification,
                subject=self.subject,
                subject_type=self.notification.subject.type
                if self.subject is not None
                else None,
                rule=self.rule,
                rule_id=self.rule_id,
                title=self.rendered.title,
                body=self.rendered.body,
            )
        except Exception:
            # A spool write must never suppress the desktop notification
            # that already fired above.
            logger.exception(
                "Failed to write spool entry for notification %s",
                self.notification.id,
            )
