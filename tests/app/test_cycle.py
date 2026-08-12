from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import MockProvider

from signalsmith.app.context import AppContext
from signalsmith.app.cycle import process_cycle
from signalsmith.config.models import Config, DefaultAction
from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.state.history import HistoryStore
from signalsmith.state.ignore_store import IgnoreStore
from signalsmith.state.spool import SpoolManager


def _notification(notification_id: str = "1") -> GitHubNotification:
    return GitHubNotification(
        id=notification_id,
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url=f"https://api.github.com/notifications/threads/{notification_id}",
        subscription_url=f"https://api.github.com/notifications/threads/{notification_id}/subscription",
    )


def _make_ctx(tmp_path: Path, notifications: list[GitHubNotification]) -> AppContext:
    config = Config(default_action=DefaultAction.IGNORE, rules=[])
    provider = MockProvider(notifications)
    spool = SpoolManager(tmp_path / "spool", tmp_path / "trash")
    history_store = HistoryStore(tmp_path / "history")
    ignore_store = IgnoreStore(tmp_path / "ignored")
    return AppContext(
        config=config,
        provider=provider,
        spool=spool,
        history_store=history_store,
        ignore_store=ignore_store,
    )


def test_process_cycle_reaps_by_default(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, [_notification()])
    ctx.spool.reap = MagicMock(wraps=ctx.spool.reap)  # type: ignore[method-assign]

    process_cycle(ctx, notifications=ctx.provider.get_notifications())

    ctx.spool.reap.assert_called_with("github", {"1"})


def test_process_cycle_skips_reap_when_dry_run(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, [_notification()])
    ctx.spool.reap = MagicMock(wraps=ctx.spool.reap)  # type: ignore[method-assign]

    process_cycle(ctx, dry_run=True, notifications=ctx.provider.get_notifications())

    ctx.spool.reap.assert_not_called()


def test_process_cycle_skips_reap_when_limit_given(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, [_notification()])
    ctx.spool.reap = MagicMock(wraps=ctx.spool.reap)  # type: ignore[method-assign]

    process_cycle(ctx, limit=1, notifications=ctx.provider.get_notifications())

    ctx.spool.reap.assert_not_called()


def test_process_cycle_dry_run_prints_would_reap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _make_ctx(tmp_path, [_notification()])

    process_cycle(ctx, dry_run=True, notifications=ctx.provider.get_notifications())

    assert "Would reap" in capsys.readouterr().out


def test_process_cycle_returns_stats_with_found_count(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, [_notification()])

    stats = process_cycle(ctx, notifications=ctx.provider.get_notifications())

    assert stats.found == 1


def test_process_cycle_reaps_using_fetched_notifications_when_omitted(
    tmp_path: Path,
) -> None:
    """Regression test: omitting `notifications` must not reap against an
    empty set (which would wipe every spool entry) - it should fetch from
    the provider and reap using that list, same as when passed explicitly."""
    ctx = _make_ctx(tmp_path, [_notification()])
    ctx.spool.reap = MagicMock(wraps=ctx.spool.reap)  # type: ignore[method-assign]

    process_cycle(ctx)

    ctx.spool.reap.assert_called_once_with("github", {"1"})
