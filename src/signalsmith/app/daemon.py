"""The `daemon` command's poll loop."""

import logging
import time
from collections.abc import Callable
from typing import Any, NoReturn

from .context import AppContext
from .cycle import process_cycle

logger = logging.getLogger(__name__)

__all__ = ["run_daemon"]


def run_daemon(
    ctx: AppContext,
    *,
    interval: int,
    account: dict[str, Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> NoReturn:
    """Run continuously: fetch, process one cycle, sleep, repeat.

    A cycle's own exceptions are logged and swallowed so one bad poll never
    kills the daemon. `sleep` is injectable so tests can escape the loop (or
    assert on the sleep duration) instead of patching `time.sleep` globally -
    see `tests/app/test_daemon.py`.
    """
    while True:
        try:
            notifications = ctx.provider.get_notifications()
            stats = process_cycle(ctx, account=account, notifications=notifications)
            logger.info("Stats: %s", stats.summary())
            logger.info("Breakdown:\n%s", stats.breakdown())
        except Exception:
            logger.exception("Error processing notifications")

        sleep_for = interval
        if (
            ctx.provider.poll_interval is not None
            and ctx.provider.poll_interval > interval
        ):
            logger.info(
                "GitHub requested a minimum poll interval of %d seconds "
                "(configured interval is %d); honoring the larger value",
                ctx.provider.poll_interval,
                interval,
            )
            sleep_for = ctx.provider.poll_interval

        logger.info("Sleeping for %d seconds before checking again", sleep_for)
        sleep(sleep_for)
