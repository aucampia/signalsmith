"""One poll cycle: create+execute actions, then reap the spool."""

import logging
from typing import Any

from ..actions import execute_actions
from ..github.models import GitHubNotification
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
    execute_actions(actions, dry_run=dry_run, stats=stats)
    if not dry_run and limit is None:
        removed = ctx.spool.reap(ctx.provider.name, {n.id for n in notifications})
        logger.info("Reaped %d spool entries no longer in the unread feed", removed)
    elif dry_run:
        print("[DRY RUN] Would reap spool entries no longer in the unread feed")
    return stats
