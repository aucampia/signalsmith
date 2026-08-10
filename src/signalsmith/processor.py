"""Notification processing logic."""

import logging
from collections.abc import Iterable
from typing import Any

from .actions import Action, NotifyRuntime, create_action_for_rule
from .cel_rules import RuleMatcher
from .config.models import (
    Config,
    DefaultAction,
    IgnoreActionConfig,
    NotifyActionConfig,
    RuleAction,
)
from .github.models import (
    GITHUB_NOTIFICATION_ADAPTER,
    GitHubIssue,
    GitHubNotification,
    GitHubPullRequest,
)
from .notification.models import NotificationOutcome
from .protocols import NotificationProvider
from .state.ignore_store import IgnoreStore
from .state.spool import SpoolManager
from .stats import RunStats

logger = logging.getLogger(__name__)

__all__ = ["build_account_context", "create_actions"]

DEFAULT_RULE_ID = "__default__"


def build_account_context(provider: NotificationProvider) -> dict[str, Any]:
    """Build the `account` variable exposed to filter expressions."""
    return {"github": {"username": provider.get_authenticated_user()}}


def default_rule_action(config: Config, notification: GitHubNotification) -> RuleAction:
    """Build the `RuleAction` a notification falls back to when no rule matches.

    Synthesized rather than a special case: routing it through the same
    `create_action_for_rule` as a matched rule means the notify/skip
    decision (renotify interval) is made in exactly one place.
    """
    if config.default_action == DefaultAction.NOTIFY:
        return RuleAction(
            notify=NotifyActionConfig(
                title=f"{notification.subject.type}: {notification.subject.title}",
                message=f"{notification.repository.full_name} ({notification.reason})",
            )
        )
    return RuleAction(ignore=IgnoreActionConfig())


def create_actions(
    config: Config,
    provider: NotificationProvider,
    spool: SpoolManager,
    force: bool = False,
    limit: int | None = None,
    dump_json: bool = False,
    stats: RunStats | None = None,
    refresh_notifications: bool = False,
    account: dict[str, Any] | None = None,
    notifications: list[GitHubNotification] | None = None,
    *,
    ignore_store: IgnoreStore,
    notify_runtime: NotifyRuntime | None = None,
) -> Iterable[tuple[NotificationOutcome, Action | None]]:
    """Create actions for notifications according to configured rules.

    Args:
        config: Application configuration with filter rules
        provider: Notification provider (e.g., GitHubClient)
        spool: Spool manager for renotify suppression and durable records
        force: If True, ignore renotify interval
        limit: Maximum number of notifications to process
        dump_json: If True, dump notification JSON for debugging
        stats: Optional counters to populate with the number found
        refresh_notifications: If True, bypass the notification list cache
        account: `account` variable for filter expressions; built from
            `provider` if not given
        notifications: Pre-fetched notifications to process; fetched from
            `provider` if not given (avoids a redundant fetch when the
            caller already has the list, e.g. for a dry-run summary)
        ignore_store: Permanent-ignore store, consulted regardless of
            `notify_runtime` - both `run` and `daemon` always pass one
        notify_runtime: Interactive-notification context from `daemon`, if any

    Yields:
        Tuples of (outcome, action) where action may be None for filtered notifications
    """
    if notifications is None:
        notifications = provider.get_notifications(
            limit=limit, refresh=refresh_notifications
        )
    logger.info("Fetched %d notifications from provider", len(notifications))

    unread_count = sum(1 for n in notifications if n.unread)
    logger.info("Found %d unread notifications to process", unread_count)

    if stats is not None:
        stats.found = len(notifications)
        for n in notifications:
            stats.by_org[n.repository.org] += 1
            stats.by_repo[n.repository.full_name] += 1
            stats.by_reason[n.reason] += 1

    if account is None:
        account = build_account_context(provider)
    rule_matcher = RuleMatcher(
        config.rules, context={"account": account, "variables": config.variables}
    )

    for notification in notifications:
        # Apply organization masks
        if (
            config.masks.orgs.include
            and notification.repository.org not in config.masks.orgs.include
        ):
            logger.debug(
                "Skipping notification %s: org %s not in include list %s",
                notification.id,
                notification.repository.org,
                config.masks.orgs.include,
            )
            yield (NotificationOutcome.FILTERED_ORG, None)
            continue
        if (
            config.masks.orgs.exclude
            and notification.repository.org in config.masks.orgs.exclude
        ):
            logger.debug(
                "Skipping notification %s: org %s in exclude list %s",
                notification.id,
                notification.repository.org,
                config.masks.orgs.exclude,
            )
            yield (NotificationOutcome.FILTERED_ORG, None)
            continue

        logger.debug(
            "Processing notification %s: %s - %s (unread=%s)",
            notification.id,
            notification.subject.type,
            notification.subject.title,
            notification.unread,
        )

        if not notification.unread:
            logger.debug(
                "Skipping notification %s (already read)", notification.debug_info
            )
            yield (NotificationOutcome.FILTERED_ALREADY_READ, None)
            continue

        if notification.subject.url is not None and ignore_store.is_ignored(
            notification.subject.url
        ):
            logger.debug(
                "Skipping notification %s (permanently ignored subject %s)",
                notification.id,
                notification.subject.url,
            )
            yield (NotificationOutcome.PERMANENTLY_IGNORED, None)
            continue

        if dump_json:
            notification_json = GITHUB_NOTIFICATION_ADAPTER.dump_json(
                notification, indent=2
            ).decode()
            print(f"Notification JSON for {notification.id}:\n{notification_json}")

        # Create subject fetcher callback
        creator_recorded = False
        fetched_subject: GitHubIssue | GitHubPullRequest | None = None

        def fetch_subject(
            url: str, type: str, updated_at: str
        ) -> GitHubIssue | GitHubPullRequest:
            nonlocal creator_recorded, fetched_subject
            subject = provider.get_subject(url, type, updated_at)
            fetched_subject = subject
            if dump_json:
                print(
                    f"Fetched subject JSON for {type} at {url}:\n{subject.model_dump_json(indent=2)}"
                )
            if stats is not None and not creator_recorded:
                stats.by_creator[subject.user.login] += 1
                creator_recorded = True
            return subject

        logger.info("Received notification %s", notification.debug_info)
        try:
            rule = rule_matcher.find_matching_rule(notification, fetch_subject)
        except Exception:
            logger.exception(
                "Error finding matching rule for notification %s",
                notification.debug_info,
            )
            yield (NotificationOutcome.FILTERED_ERROR, None)
            continue

        rule_action = (
            rule.action
            if rule is not None
            else default_rule_action(config, notification)
        )
        action = create_action_for_rule(
            notification,
            rule_action,
            rule.id if rule is not None else DEFAULT_RULE_ID,
            provider,
            spool,
            config,
            force,
            subject=fetched_subject,
            rule=rule,
            notify_runtime=notify_runtime,
        )

        logger.debug(
            "Created action for notification %s: %s",
            notification.debug_info,
            action.__class__.__name__,
        )
        yield (action.outcome, action)
