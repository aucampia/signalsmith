"""Execute a stream of (outcome, action) tuples produced by `processor.create_actions`."""

import logging
from collections.abc import Iterable

from ..notification.models import NotificationOutcome
from ..stats import RunStats
from .base import Action

logger = logging.getLogger(__name__)

__all__ = ["execute_actions"]


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
