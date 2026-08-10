import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import MockProvider

from signalsmith.app.context import AppContext
from signalsmith.app.daemon import run_daemon
from signalsmith.config.models import Config, DefaultAction
from signalsmith.state.ignore_store import IgnoreStore
from signalsmith.state.spool import SpoolManager


class _StopLoopError(Exception):
    """Raised by the fake `sleep` to escape `run_daemon`'s `while True` after one cycle."""


def _make_ctx(tmp_path: Path, poll_interval: int | None = None) -> AppContext:
    config = Config(default_action=DefaultAction.IGNORE, rules=[])
    provider = MockProvider([], poll_interval=poll_interval)
    spool = SpoolManager(tmp_path / "spool", tmp_path / "trash")
    ignore_store = IgnoreStore(tmp_path / "ignored")
    return AppContext(
        config=config, provider=provider, spool=spool, ignore_store=ignore_store
    )


def _run_one_iteration(ctx: AppContext, **kwargs: object) -> MagicMock:
    fake_sleep = MagicMock(side_effect=_StopLoopError)
    with pytest.raises(_StopLoopError):
        run_daemon(ctx, sleep=fake_sleep, **kwargs)  # type: ignore[arg-type]
    return fake_sleep


def test_sleeps_for_configured_interval_when_no_provider_poll_interval(
    tmp_path: Path,
) -> None:
    ctx = _make_ctx(tmp_path, poll_interval=None)
    fake_sleep = _run_one_iteration(ctx, interval=60)
    fake_sleep.assert_called_once_with(60)


def test_sleeps_for_configured_interval_when_provider_poll_interval_smaller(
    tmp_path: Path,
) -> None:
    ctx = _make_ctx(tmp_path, poll_interval=30)
    fake_sleep = _run_one_iteration(ctx, interval=60)
    fake_sleep.assert_called_once_with(60)


def test_sleeps_for_provider_poll_interval_when_larger(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, poll_interval=120)
    fake_sleep = _run_one_iteration(ctx, interval=60)
    fake_sleep.assert_called_once_with(120)


def test_exception_in_cycle_is_logged_and_loop_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = _make_ctx(tmp_path)
    ctx.provider.get_notifications = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )

    with caplog.at_level(logging.ERROR):
        fake_sleep = _run_one_iteration(ctx, interval=60)

    assert any(
        "Error processing notifications" in record.message for record in caplog.records
    )
    # The loop reached `sleep` despite the exception, proving it was
    # swallowed rather than propagating out of `run_daemon`.
    fake_sleep.assert_called_once_with(60)
