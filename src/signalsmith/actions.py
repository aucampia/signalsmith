"""Action implementations for notification processing."""

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from desktop_notifier import Button

from .config.models import Config, NotifyActionConfig, Rule
from .github.models import GitHubIssue, GitHubNotification, GitHubPullRequest
from .notification.models import NotificationOutcome
from .notifier import RenderedNotification, render_notification, send_notification
from .notify_dispatcher import NotificationDispatcher
from .protocols import NotificationProvider
from .state.ignore_store import IgnoreStore
from .state.spool import SpoolManager

logger = logging.getLogger(__name__)

__all__ = [
    "Action",
    "IgnoreAction",
    "MarkAsReadAction",
    "NotifyAction",
    "NotifyRuntime",
    "RunStats",
    "create_action_for_rule",
    "execute_actions",
]


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
    actions_enabled: bool  # gates the Dismiss/Ignore buttons only
    max_concurrent: int
    wait_timeout: int


@dataclass
class RunStats:
    """Aggregate counters for a single run, populated during processing."""

    found: int = 0
    outcomes: Counter[NotificationOutcome] = field(default_factory=Counter)
    by_org: Counter[str] = field(default_factory=Counter)
    by_repo: Counter[str] = field(default_factory=Counter)
    by_reason: Counter[str] = field(default_factory=Counter)
    by_creator: Counter[str] = field(default_factory=Counter)

    @property
    def notified(self) -> int:
        return self.outcomes[NotificationOutcome.NOTIFIED]

    @property
    def marked_as_read(self) -> int:
        return self.outcomes[NotificationOutcome.MARKED_AS_READ]

    @property
    def ignored(self) -> int:
        return self.outcomes[NotificationOutcome.IGNORED]

    @property
    def skipped(self) -> int:
        return self.outcomes[NotificationOutcome.SKIPPED]

    def summary(self) -> str:
        outcome_parts = " ".join(
            f"{outcome.value}={self.outcomes[outcome]}"
            for outcome in NotificationOutcome
        )
        return f"found={self.found} {outcome_parts}"

    def breakdown(self, top_repos: int = 10, top_creators: int = 10) -> str:
        lines = ["By org:"]
        for org, count in self.by_org.most_common():
            lines.append(f"  {count:5d}  {org}")

        lines.append("")
        lines.append(f"Top {top_repos} repos:")
        for repo, count in self.by_repo.most_common(top_repos):
            lines.append(f"  {count:5d}  {repo}")

        lines.append("")
        lines.append("By reason:")
        for reason, count in self.by_reason.most_common():
            lines.append(f"  {count:5d}  {reason}")

        if self.by_creator:
            lines.append("")
            lines.append(f"Top {top_creators} PR/issue creators:")
            for creator, count in self.by_creator.most_common(top_creators):
                lines.append(f"  {count:5d}  {creator}")

        return "\n".join(lines)


class Action(Protocol):
    """Protocol for actions that can be executed on notifications."""

    def execute(self, dry_run: bool = False) -> None:
        """Execute the action.

        Args:
            dry_run: If True, print what would happen instead of executing
        """
        ...


class NotifyAction:
    """Send a desktop notification."""

    def __init__(
        self,
        notification: GitHubNotification,
        config: NotifyActionConfig,
        spool: SpoolManager,
        rule_id: str,
        *,
        rule: Rule | None = None,
        subject: GitHubIssue | GitHubPullRequest | None = None,
        provider_name: str = "",
        notify_runtime: NotifyRuntime | None = None,
    ) -> None:
        self.notification = notification
        self.config = config
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
                f"{self.notification.subject.type} - {self.notification.subject.title} "
                f"({self.notification.repository.full_name}, reason: {self.notification.reason})"
            )
            return

        rendered = render_notification(self.config, self.notification)
        self._send(rendered)
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
                title=rendered.title,
                message=rendered.message,
            )
        except Exception:
            # A spool write must never suppress the desktop notification
            # that already fired above.
            logger.exception(
                "Failed to write spool entry for notification %s",
                self.notification.id,
            )


class IgnoreAction:
    """Ignore a notification (no desktop alert, stays unread on backend)."""

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


class MarkAsReadAction:
    """Mark a notification as read."""

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


class SkipAction:
    """Skip a notification (renotify interval not elapsed)."""

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


def create_action_for_rule(
    notification: GitHubNotification,
    rule: Rule,
    provider: NotificationProvider,
    spool: SpoolManager,
    config: Config,
    force: bool = False,
    subject: GitHubIssue | GitHubPullRequest | None = None,
    *,
    notify_runtime: NotifyRuntime | None = None,
) -> Action:
    """Create the appropriate action for a notification based on the matched rule.

    Args:
        notification: The notification to create an action for
        rule: The matched rule
        provider: Notification provider for marking as read
        spool: Spool manager for renotify suppression and durable records
        config: Application configuration (for renotify_interval and action definitions)
        force: If True, ignore renotify interval for notify actions
        subject: The subject fetched while evaluating this rule, if any
        notify_runtime: Interactive-notification context from `daemon`, if any

    Returns:
        The appropriate action to execute
    """
    # Resolve action reference if present
    if rule.action.ref:
        if rule.action.ref not in config.actions:
            raise ValueError(
                f"Rule {rule.id!r} references undefined action {rule.action.ref!r}"
            )
        action_def = config.actions[rule.action.ref]
        notify_config = action_def.notify
        mark_as_read_config = action_def.mark_as_read
        ignore_config = action_def.ignore
    else:
        notify_config = rule.action.notify
        mark_as_read_config = rule.action.mark_as_read
        ignore_config = rule.action.ignore

    # Handle notify action
    if notify_config:
        should_notify = force or spool.should_notify(
            provider.name, notification.id, config.renotify_interval
        )
        if should_notify:
            return NotifyAction(
                notification,
                notify_config,
                spool,
                rule.id,
                rule=rule,
                subject=subject,
                provider_name=provider.name,
                notify_runtime=notify_runtime,
            )
        else:
            return SkipAction(notification, rule.id)

    # Handle mark_as_read action
    elif mark_as_read_config:
        return MarkAsReadAction(notification, provider, rule.id)

    # Handle ignore action
    elif ignore_config:
        return IgnoreAction(notification, rule.id)

    # This shouldn't happen due to model validation, but handle it gracefully
    raise ValueError(f"Rule {rule.id} has no valid action")


def execute_actions(
    actions: Iterable[tuple[NotificationOutcome, Action | None]],
    dry_run: bool = False,
    stats: RunStats | None = None,
) -> None:
    """Execute an iterable of (outcome, action) tuples.

    Args:
        actions: Iterable of (outcome, action) tuples
        dry_run: If True, execute in dry-run mode
        stats: Optional counters to update with the outcome of each action
    """
    action_count = 0
    for outcome, action in actions:
        if stats is not None:
            stats.outcomes[outcome] += 1

        if action is not None:
            action_count += 1
            logger.debug(
                "Executing action %d: %s (dry_run=%s)",
                action_count,
                action.__class__.__name__,
                dry_run,
            )
            action.execute(dry_run=dry_run)

    logger.info("Executed %d actions total", action_count)
