"""One poll cycle: create+execute actions, then reap the spool."""

import logging
from typing import Any

from ..actions.notify import NotifyAction
from ..github.models import GitHubNotification
from ..notification.models import NotificationOutcome
from ..processor import create_actions
from ..stats import RunStats
from .context import AppContext

logger = logging.getLogger(__name__)

__all__ = ["process_cycle"]


def process_cycle(
    ctx: AppContext,
    *,
    force: bool = False,
    limit: int | None = None,
    dump_json: bool = False,
    dry_run: bool = False,
    refresh_notifications: bool = False,
    account: dict[str, Any] | None = None,
    notifications: list[GitHubNotification] | None = None,
) -> RunStats:
    """Process one poll cycle: create+execute actions, then reap the spool.

    Shared by `run` (called once) and `app.daemon.run_daemon` (called every
    loop iteration) so the two commands share the same per-cycle behavior
    rather than duplicating it.
    """
    if notifications is None:
        notifications = ctx.provider.get_notifications(
            limit=limit, refresh=refresh_notifications
        )

    stats = RunStats()
    actions = create_actions(
        ctx.config,
        ctx.provider,
        ctx.spool,
        force,
        limit,
        dump_json,
        stats=stats,
        refresh_notifications=refresh_notifications,
        account=account,
        notifications=notifications,
        ignore_store=ctx.ignore_store,
        notify_runtime=ctx.notify_runtime,
    )
    action_count = 0
    for notification, (outcome, action) in zip(notifications, actions, strict=True):
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
        if not dry_run:
            rendered_title: str | None = None
            rendered_body: str | None = None
            subject = None
            subject_type: str | None = None
            if isinstance(action, NotifyAction):
                rendered_title = action.rendered.title
                rendered_body = action.rendered.body
                if action.subject is not None:
                    subject = action.subject
                    subject_type = action.notification.subject.type
            history_outcome = (
                NotificationOutcome.NOTIFIED
                if outcome == NotificationOutcome.SKIPPED
                else outcome
            )
            ctx.history_store.record(
                provider=ctx.provider.name,
                notification=notification,
                outcome=history_outcome,
                rule_id=getattr(action, "rule_id", ""),
                rendered_title=rendered_title,
                rendered_body=rendered_body,
                subject=subject,
                subject_type=subject_type,
            )

    logger.info("Executed %d actions total", action_count)

    if not dry_run and limit is None:
        removed = ctx.spool.reap(ctx.provider.name, {n.id for n in notifications})
        logger.info("Reaped %d spool entries no longer in the unread feed", removed)
    elif dry_run:
        print("[DRY RUN] Would reap spool entries no longer in the unread feed")
    return stats
