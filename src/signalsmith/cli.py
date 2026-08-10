#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Annotated

import typer

from .app import build_app_context, open_notify_runtime, process_cycle, run_daemon
from .cache import clean_cache, resolve_cache_dir
from .config.models import Config
from .config.testing import run_test_files
from .errors import SignalsmithError
from .logging_config import dump_logging_config, setup_logging
from .processor import build_account_context
from .state.ignore_store import IgnoreStore
from .state.models import IGNORED_ENTRY_ADAPTER, SPOOL_ENTRY_ADAPTER
from .state.spool import SpoolManager

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


def _open_spool(config: Config, *, check_version: bool = True) -> SpoolManager:
    """Open the spool.

    `check_version=False` is used by the commands that fix a state version
    mismatch (`state clean`, `spool clean`) - they must not be blocked by
    the very problem they exist to resolve.
    """
    if check_version:
        SpoolManager.ensure_state_version()
    return SpoolManager(
        SpoolManager.resolve_spool_dir(config), SpoolManager.resolve_trash_dir()
    )


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

    ctx = build_app_context(cache_only=cache_only)

    logger.info(
        "Starting signalsmith run (cache_only=%s, dry_run=%s)", cache_only, dry_run
    )
    account = build_account_context(ctx.provider)
    logger.info("account: %s", json.dumps(account, indent=2))

    notifications = ctx.provider.get_notifications(
        limit=limit, refresh=refresh_notifications
    )

    if dry_run:
        print(f"[DRY RUN] Found {len(notifications)} total notifications")
        unread_count = sum(1 for n in notifications if n.unread)
        print(f"[DRY RUN] {unread_count} unread notifications to process")

    # ctx.notify_runtime is always None here: `run` exits right after this
    # returns, so it can never stay alive to catch a click/dismiss/button
    # press - see NotificationDispatcher's docstring. `daemon` is the only
    # command that ever opens one.
    stats = process_cycle(
        ctx,
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
    config = Config.load(config_path)
    tdir = Config.resolve_test_dir(tests_dir)

    if not tdir.exists():
        print(f"No test directory found at {tdir}")
        raise typer.Exit(1)

    report = run_test_files(config, tdir, name_filter=name_filter)

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

    ctx = build_app_context()
    interval = poll_interval if poll_interval is not None else ctx.config.poll_interval

    # Interactivity (click-to-open, Dismiss/Ignore buttons) only makes sense
    # here: it needs a dispatcher that stays alive to catch a later click,
    # which only a long-running process like this one can do - `run` never
    # opens one. Opened once, for the whole daemon lifetime, so its D-Bus
    # connection/loop persists across poll cycles.
    if not non_interactive:
        ctx.notify_runtime = open_notify_runtime(
            ctx.config, ctx.provider, ctx.ignore_store
        )

    logger.info("Starting daemon mode (poll_interval=%d seconds)", interval)
    account = build_account_context(ctx.provider)
    logger.info("account: %s", json.dumps(account, indent=2))

    run_daemon(ctx, interval=interval, account=account)


@cache_cli.command("clean")
def cache_clean(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Remove the local notification/subject cache directory."""
    setup_logging(verbose)
    cache_dir = resolve_cache_dir()

    if clean_cache(cache_dir):
        print(f"Removed cache directory: {cache_dir}")
    else:
        print(f"No cache directory found at {cache_dir}")


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
    config = Config.load()
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
    config = Config.load()
    spool = _open_spool(config, check_version=False)

    removed = spool.clear()
    trash_dir = SpoolManager.resolve_trash_dir()
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
    config = Config.load()
    spool = _open_spool(config, check_version=False)

    removed = spool.clear()
    state_dir = SpoolManager.resolve_state_dir()
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
    store = IgnoreStore(IgnoreStore.resolve_dir())

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
    store = IgnoreStore(IgnoreStore.resolve_dir())
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
    store = IgnoreStore(IgnoreStore.resolve_dir())
    removed = store.clear()
    print(f"Removed {removed} entries from the ignore store")


def main() -> None:
    try:
        cli()
    except SignalsmithError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    finally:
        # Dump logging config for debugging
        if os.environ.get("SIGNALSMITH_DEBUG_LOGGING"):
            dump_logging_config()


if __name__ == "__main__":
    main()
