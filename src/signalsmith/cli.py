#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from xdg import xdg_cache_home

from .actions import NotifyRuntime, RunStats, execute_actions
from .config.models import Config
from .config.testing import run_test_files
from .github.client import GitHubClient
from .github.models import GitHubNotification
from .notify_dispatcher import NotificationDispatcher
from .processor import build_account_context, create_actions
from .protocols import NotificationProvider
from .state.ignore_store import IgnoreStore, resolve_ignore_dir
from .state.models import IGNORED_ENTRY_ADAPTER, SPOOL_ENTRY_ADAPTER
from .state.spool import (
    SpoolManager,
    ensure_state_version,
    resolve_spool_dir,
    resolve_spool_trash_dir,
    resolve_state_dir,
)
from .versioning import VersionError

logger = logging.getLogger(__name__)

cli = typer.Typer(pretty_exceptions_enable=False)
cache_cli = typer.Typer(pretty_exceptions_enable=False, help="Manage the local cache.")
cli.add_typer(cache_cli, name="cache")
spool_cli = typer.Typer(
    pretty_exceptions_enable=False, help="Manage the notification spool."
)
cli.add_typer(spool_cli, name="spool")
state_cli = typer.Typer(pretty_exceptions_enable=False, help="Manage local state.")
cli.add_typer(state_cli, name="state")
ignore_cli = typer.Typer(
    pretty_exceptions_enable=False, help="Manage the permanent-ignore store."
)
cli.add_typer(ignore_cli, name="ignore")


def _load_config(config_file: Path | None = None) -> Config:
    """Load the config file, exiting with a clear message on a version mismatch."""
    try:
        return Config.load(config_file)
    except VersionError as exc:
        logger.error(str(exc))
        raise typer.Exit(1) from exc


def _open_github_client(
    token: str, cache_dir: Path, *, cache_only: bool = False
) -> GitHubClient:
    """Open the GitHub client, exiting with a clear message on a cache version mismatch."""
    try:
        return GitHubClient(token, cache_dir, cache_only=cache_only)
    except VersionError as exc:
        logger.error(str(exc))
        raise typer.Exit(1) from exc


def _open_spool(config: Config, *, check_version: bool = True) -> SpoolManager:
    """Open the spool, exiting with a clear message on a state version mismatch.

    `check_version=False` is used by the commands that fix a version
    mismatch (`state clean`, `spool clean`) - they must not be blocked by
    the very problem they exist to resolve.
    """
    if check_version:
        try:
            ensure_state_version(resolve_state_dir())
        except VersionError as exc:
            logger.error(str(exc))
            raise typer.Exit(1) from exc
    return SpoolManager(resolve_spool_dir(config), resolve_spool_trash_dir())


def _process_one_cycle(
    config: Config,
    provider: NotificationProvider,
    spool: SpoolManager,
    ignore_store: IgnoreStore,
    notify_runtime: NotifyRuntime | None,
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

    Shared by `run` (called once, `notify_runtime=None` always) and `daemon`
    (called every loop iteration) so the two commands share the same
    per-cycle behavior rather than duplicating it.
    """
    stats = RunStats()
    actions = create_actions(
        config,
        provider,
        spool,
        force,
        limit,
        dump_json,
        stats=stats,
        refresh_notifications=refresh_notifications,
        account=account,
        notifications=notifications,
        ignore_store=ignore_store,
        notify_runtime=notify_runtime,
    )
    execute_actions(actions, dry_run=dry_run, stats=stats)
    if not dry_run and limit is None:
        removed = spool.reap(provider.name, {n.id for n in (notifications or [])})
        logger.info("Reaped %d spool entries no longer in the unread feed", removed)
    elif dry_run:
        print("[DRY RUN] Would reap spool entries no longer in the unread feed")
    return stats


class _SuppressCelPyFilter(logging.Filter):
    """Filter out noisy celpy logging - only show WARNING and above."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Check if log comes from celpy module (by pathname or logger name)
        is_celpy = "celpy" in record.pathname or any(
            record.name.startswith(prefix)
            for prefix in (
                "celpy",
                "Environment",
                "NameContainer",
                "evaluation",
                "Evaluator",
                "InterpretedRunner",
                "celtypes",
            )
        )

        if is_celpy:
            # Only allow WARNING and above from celpy
            return record.levelno >= logging.WARNING

        # Allow all other logs
        return True


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else os.environ.get("PYTHON_LOGGING_LEVEL", "INFO")

    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        datefmt="%Y-%m-%dT%H:%M:%S",
        format=(
            "%(asctime)s.%(msecs)03d %(process)d %(thread)d %(levelno)03d:%(levelname)-8s "
            "%(name)-12s %(module)s:%(lineno)s:%(funcName)s %(message)s"
        ),
    )

    # Add filter to ALL handlers to block celpy noise
    celpy_filter = _SuppressCelPyFilter()
    for handler in logging.root.handlers:
        handler.addFilter(celpy_filter)

    # httpx logs one INFO line per HTTP request; too noisy for normal runs.
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)


def get_github_token() -> str:
    """Get GitHub token from environment or gh CLI."""
    # Try environment variables first
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    # Fall back to gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        token = result.stdout.strip()
        if token:
            logger.debug("Using token from gh CLI")
            return token
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        logger.debug("Could not get token from gh CLI: %s", exc)

    logger.error(
        "GitHub token not found. Set GITHUB_TOKEN/GH_TOKEN environment variable "
        "or authenticate with: gh auth login"
    )
    raise typer.Exit(1)


@cli.command()
def run(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    cache_only: Annotated[
        bool,
        typer.Option(
            "--cache-only",
            help="Use cached notification list only; no API call for notifications. Subjects (Issues/PRs) are still fetched from API as needed.",
        ),
    ] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    dump_json: Annotated[
        bool,
        typer.Option(
            "--dump-json",
            help="Dump notifications and subjects as formatted JSON to stdout in addition to normal processing. Useful for examining fields when writing filter expressions.",
        ),
    ] = False,
    refresh_notifications: Annotated[
        bool,
        typer.Option(
            "--refresh-notifications",
            help="Bypass the notification list cache and refetch from the API even if unchanged (subjects are cached separately and unaffected).",
        ),
    ] = False,
) -> None:
    """Run one poll cycle and exit."""
    setup_logging(verbose)

    if cache_only and refresh_notifications:
        logger.error("--cache-only and --refresh-notifications are mutually exclusive")
        raise typer.Exit(1)

    config = _load_config()
    token = get_github_token()
    cache_dir = xdg_cache_home() / "signalsmith"

    provider = _open_github_client(token, cache_dir, cache_only=cache_only)
    spool = _open_spool(config)
    ignore_store = IgnoreStore(resolve_ignore_dir())

    logger.info(
        "Starting signalsmith run (cache_only=%s, dry_run=%s)", cache_only, dry_run
    )
    account = build_account_context(provider)
    logger.info("account: %s", json.dumps(account, indent=2))

    notifications = provider.get_notifications(
        limit=limit, refresh=refresh_notifications
    )

    if dry_run:
        print(f"[DRY RUN] Found {len(notifications)} total notifications")
        unread_count = sum(1 for n in notifications if n.unread)
        print(f"[DRY RUN] {unread_count} unread notifications to process")

    # notify_runtime is always None here: `run` exits right after this
    # returns, so it can never stay alive to catch a click/dismiss/button
    # press - see NotificationDispatcher's docstring. `daemon` is the only
    # command that ever constructs one.
    stats = _process_one_cycle(
        config,
        provider,
        spool,
        ignore_store,
        None,
        force=force,
        limit=limit,
        dump_json=dump_json,
        dry_run=dry_run,
        refresh_notifications=refresh_notifications,
        account=account,
        notifications=notifications,
    )

    logger.info("Completed processing")
    print(f"Stats: {stats.summary()}")
    print()
    print(stats.breakdown())


@cli.command("test")
def test_config(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    tests_dir: Annotated[
        Path | None,
        typer.Option(
            "--tests-dir",
            help="Directory of test files (default: SIGNALSMITH_TEST_DIR or <config dir>/tests).",
        ),
    ] = None,
    name_filter: Annotated[
        str | None,
        typer.Option("-k", help="Only run cases whose name contains this substring."),
    ] = None,
) -> None:
    """Run offline rule tests against the config (no GitHub API calls)."""
    setup_logging(verbose)

    config_path = Config.resolve_config_path()
    config = _load_config(config_path)
    tdir = Config.resolve_test_dir(tests_dir)

    if not tdir.exists():
        print(f"No test directory found at {tdir}")
        raise typer.Exit(1)

    try:
        report = run_test_files(config, tdir, name_filter=name_filter)
    except VersionError as exc:
        logger.error(str(exc))
        raise typer.Exit(1) from exc

    if not report.results:
        print(f"No test cases found in {tdir}")
        raise typer.Exit(1)

    for result in report.results:
        param_suffix = (
            f" (parameter={result.parameter!r})" if result.parameter is not None else ""
        )
        label = f"{result.file}: {result.case_name}{param_suffix}"
        if result.passed:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}")
            if result.error:
                print(f"      error: {result.error}")
            else:
                print(
                    f"      expected rule={result.expected_rule!r} action={result.expected_action!r}"
                )
                print(
                    f"      actual   rule={result.actual_rule!r} action={result.actual_action!r}"
                )

    print()
    print(f"Summary: {report.passed} passed, {report.failed} failed")

    if report.failed:
        raise typer.Exit(1)


@cli.command()
def daemon(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    poll_interval: Annotated[int | None, typer.Option("--poll-interval")] = None,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help=(
                "Send notifications fire-and-forget with no click-to-open, "
                "dismiss, or ignore support - the same code path `run` uses."
            ),
        ),
    ] = False,
) -> None:
    """Run continuously, polling on a configurable interval."""
    setup_logging(verbose)

    config = _load_config()
    interval = poll_interval if poll_interval is not None else config.poll_interval
    token = get_github_token()
    cache_dir = xdg_cache_home() / "signalsmith"

    provider = _open_github_client(token, cache_dir)
    spool = _open_spool(config)
    ignore_store = IgnoreStore(resolve_ignore_dir())

    # Interactivity (click-to-open, Dismiss/Ignore buttons) only makes sense
    # here: `NotificationDispatcher` needs to stay alive to catch a later
    # click, which only a long-running process like this one can do -
    # `run` never builds one. Constructed once, for the whole daemon
    # lifetime, so its D-Bus connection/loop persists across poll cycles.
    dispatcher: NotificationDispatcher | None = None
    if not non_interactive:
        try:
            dispatcher = NotificationDispatcher(app_name="signalsmith")
        except Exception:
            logger.warning(
                "Could not start the interactive notifier; falling back to "
                "plain notifications for this run",
                exc_info=True,
            )

    notify_runtime = (
        None
        if dispatcher is None
        else NotifyRuntime(
            dispatcher=dispatcher,
            provider=provider,
            ignore_store=ignore_store,
            actions_enabled=config.notify_actions.enabled,
            max_concurrent=config.notify_actions.max_concurrent,
            wait_timeout=config.notify_actions.wait_timeout,
        )
    )

    logger.info("Starting daemon mode (poll_interval=%d seconds)", interval)
    account = build_account_context(provider)
    logger.info("account: %s", json.dumps(account, indent=2))

    while True:
        try:
            notifications = provider.get_notifications()
            stats = _process_one_cycle(
                config,
                provider,
                spool,
                ignore_store,
                notify_runtime,
                account=account,
                notifications=notifications,
            )
            logger.info("Stats: %s", stats.summary())
            logger.info("Breakdown:\n%s", stats.breakdown())
        except Exception:
            logger.exception("Error processing notifications")

        sleep_for = interval
        if provider.poll_interval is not None and provider.poll_interval > interval:
            logger.info(
                "GitHub requested a minimum poll interval of %d seconds "
                "(configured interval is %d); honoring the larger value",
                provider.poll_interval,
                interval,
            )
            sleep_for = provider.poll_interval

        logger.info("Sleeping for %d seconds before checking again", sleep_for)
        time.sleep(sleep_for)


@cache_cli.command("clean")
def cache_clean(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Remove the local notification/subject cache directory."""
    setup_logging(verbose)
    cache_dir = xdg_cache_home() / "signalsmith"

    if not cache_dir.exists():
        print(f"No cache directory found at {cache_dir}")
        return

    shutil.rmtree(cache_dir)
    print(f"Removed cache directory: {cache_dir}")


@spool_cli.command("list")
def spool_list(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Dump full spool entries as JSON instead."),
    ] = False,
) -> None:
    """List notifications currently held in the spool."""
    setup_logging(verbose)
    config = _load_config()
    spool = _open_spool(config)

    entries = sorted(spool.entries(), key=lambda pe: pe[1].received_at)

    if not entries:
        print("Spool is empty")
        return

    if as_json:
        print(
            json.dumps(
                [
                    SPOOL_ENTRY_ADAPTER.dump_python(entry, mode="json")
                    for _, entry in entries
                ],
                indent=2,
            )
        )
        return

    for _, entry in entries:
        count_suffix = f"  x{entry.notify_count}" if entry.notify_count > 1 else ""
        print(
            f"{entry.provider}-{entry.notification_id}  "
            f"{entry.received_at.isoformat()}  {entry.rule_id}{count_suffix}"
        )
        print(
            f"    {entry.notification.subject.type}: {entry.notification.subject.title} "
            f"({entry.notification.repository.full_name}, reason: {entry.notification.reason})"
        )
        if entry.notification.subject.web_url is not None:
            print(f"    {entry.notification.subject.web_url}")


@spool_cli.command("clean")
def spool_clean(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Move all spool entries to trash, resetting renotify suppression."""
    setup_logging(verbose)
    config = _load_config()
    spool = _open_spool(config, check_version=False)

    removed = spool.clear()
    trash_dir = resolve_spool_trash_dir()
    print(f"Moved {removed} spool entries to {trash_dir}")
    if removed:
        print(
            "Note: renotify suppression is tracked in the spool, so signalsmith will "
            "re-notify on everything still unread next run."
        )


@state_cli.command("clean")
def state_clean(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Move all spool entries to trash and remove the state directory.

    Use this to recover from a state version mismatch: unlike `spool
    clean`, this also removes the state root itself (including its version
    marker), so the next run starts fresh and re-stamps it with the
    current state version.
    """
    setup_logging(verbose)
    config = _load_config()
    spool = _open_spool(config, check_version=False)

    removed = spool.clear()
    state_dir = resolve_state_dir()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    print(f"Moved {removed} spool entries to trash and removed {state_dir}")
    if removed:
        print(
            "Note: renotify suppression is tracked in the spool, so signalsmith will "
            "re-notify on everything still unread next run."
        )


@ignore_cli.command("list")
def ignore_list(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Dump full ignore entries as JSON instead."),
    ] = False,
) -> None:
    """List subjects currently held in the permanent-ignore store."""
    setup_logging(verbose)
    store = IgnoreStore(resolve_ignore_dir())

    entries = sorted(store.entries(), key=lambda pe: pe[1].added_at)

    if not entries:
        print("Ignore store is empty")
        return

    if as_json:
        print(
            json.dumps(
                [
                    IGNORED_ENTRY_ADAPTER.dump_python(entry, mode="json")
                    for _, entry in entries
                ],
                indent=2,
            )
        )
        return

    for _, entry in entries:
        print(f"{entry.subject_url}  {entry.added_at.isoformat()}")
        print(f"    {entry.subject_type}: {entry.title} ({entry.repository})")


@ignore_cli.command("remove")
def ignore_remove(
    subject_url: Annotated[
        str, typer.Argument(help="Subject URL, as shown by `ignore list`.")
    ],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Remove a single subject from the permanent-ignore store."""
    setup_logging(verbose)
    store = IgnoreStore(resolve_ignore_dir())
    if store.remove(subject_url):
        print(f"Removed {subject_url} from the ignore store")
    else:
        print(f"No ignore entry found for {subject_url}")
        raise typer.Exit(1)


@ignore_cli.command("clear")
def ignore_clear(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Remove every entry from the permanent-ignore store."""
    setup_logging(verbose)
    store = IgnoreStore(resolve_ignore_dir())
    removed = store.clear()
    print(f"Removed {removed} entries from the ignore store")


def _dump_logging_config() -> None:
    """Dump current logging configuration for debugging."""

    config: dict[str, Any] = {
        "root_logger": {
            "level": logging.getLevelName(logging.root.level),
            "handlers": [type(h).__name__ for h in logging.root.handlers],
            "filters": [type(f).__name__ for f in logging.root.filters],
        },
        "loggers": {},
    }

    # Get all registered loggers
    for name in sorted(logging.Logger.manager.loggerDict.keys()):
        logger_obj = logging.Logger.manager.loggerDict[name]
        if isinstance(logger_obj, logging.Logger):
            config["loggers"][name] = {
                "level": logging.getLevelName(logger_obj.level),
                "propagate": logger_obj.propagate,
                "handlers": [type(h).__name__ for h in logger_obj.handlers],
                "filters": [type(f).__name__ for f in logger_obj.filters],
            }
        else:
            config["loggers"][name] = {"placeholder": True}

    print("\n=== LOGGING CONFIG ===", file=sys.stderr)
    print(yaml.dump(config, default_flow_style=False), file=sys.stderr)
    print("======================\n", file=sys.stderr)


def main() -> None:
    try:
        cli()
    finally:
        # Dump logging config for debugging
        if os.environ.get("SIGNALSMITH_DEBUG_LOGGING"):
            _dump_logging_config()


if __name__ == "__main__":
    main()
