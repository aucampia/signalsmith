"""Application context: config + provider + durable stores, wired up once
per CLI invocation.

`build_app_context` replaces the CLI's old `_load_config`/`_open_github_client`/
`_open_spool` wrappers. Those existed only to translate `VersionError` into
`typer.Exit`; now that `VersionError` is a `SignalsmithError`
(`versioning.py`), it can simply propagate to the single boundary in
`cli.main`, so this is a straight-line build with no exception handling of
its own.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from ..actions import NotifyRuntime
from ..cache import resolve_cache_dir
from ..config.models import Config
from ..github.client import GitHubClient
from ..notify_dispatcher import NotificationDispatcher
from ..protocols import NotificationProvider
from ..state.history import HistoryStore
from ..state.ignore_store import IgnoreStore
from ..state.spool import SpoolManager
from .auth import resolve_github_token

logger = logging.getLogger(__name__)

__all__ = ["AppContext", "build_app_context", "open_notify_runtime"]


@dataclass
class AppContext:
    """Everything a poll cycle needs: config, provider, and durable stores.

    Built once per CLI invocation by `build_app_context`. `notify_runtime`
    defaults to `None` and is only ever set by `daemon` (see
    `open_notify_runtime`) - `run` exits right after one cycle, so it can
    never stay alive to catch a click/dismiss/button press.
    """

    config: Config
    provider: NotificationProvider
    spool: SpoolManager
    history_store: HistoryStore
    ignore_store: IgnoreStore
    notify_runtime: NotifyRuntime | None = None


def build_app_context(
    config_file: Path | None = None,
    *,
    cache_only: bool = False,
    check_state_version: bool = True,
) -> AppContext:
    """Load the config and open the GitHub client, spool, and ignore store.

    Args:
        config_file: Explicit config path; resolved the usual way if None.
        cache_only: Serve notifications purely from the GitHub client's cache.
        check_state_version: Skip the state-directory version check - used by
            the commands that exist to *fix* a version mismatch (`state
            clean`, `spool clean`), which must not be blocked by the very
            problem they resolve.

    Raises:
        VersionError: on a config, state, or cache version mismatch.
        AuthError: if no GitHub token can be found.
    """
    config = Config.load(config_file)
    token = resolve_github_token()
    provider = GitHubClient(token, resolve_cache_dir(), cache_only=cache_only)
    if check_state_version:
        SpoolManager.ensure_state_version()
    spool = SpoolManager(
        SpoolManager.resolve_spool_dir(config), SpoolManager.resolve_trash_dir()
    )
    history_store = HistoryStore(HistoryStore.resolve_dir())
    ignore_store = IgnoreStore(IgnoreStore.resolve_dir())
    return AppContext(
        config=config,
        provider=provider,
        spool=spool,
        history_store=history_store,
        ignore_store=ignore_store,
    )


def open_notify_runtime(
    config: Config, provider: NotificationProvider, ignore_store: IgnoreStore
) -> NotifyRuntime | None:
    """Construct the interactive-notification runtime for `daemon`.

    Only `daemon` calls this: `NotificationDispatcher` needs to stay alive
    to catch a later click/dismiss/button press, which only a long-running
    process can do - `run` never builds one. Falls back to `None` (plain,
    fire-and-forget sends) if the dispatcher fails to start, e.g. no D-Bus.
    """
    try:
        dispatcher = NotificationDispatcher(app_name="signalsmith")
    except Exception:
        logger.warning(
            "Could not start the interactive notifier; falling back to "
            "plain notifications for this run",
            exc_info=True,
        )
        return None

    return NotifyRuntime(
        dispatcher=dispatcher,
        provider=provider,
        ignore_store=ignore_store,
        actions_enabled=config.notify_actions.enabled,
        max_concurrent=config.notify_actions.max_concurrent,
        wait_timeout=config.notify_actions.wait_timeout,
    )
