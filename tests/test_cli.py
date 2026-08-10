"""CLI-level tests: flag parsing and wiring only.

Everything about *how* a cycle or the daemon loop runs is tested directly
against `signalsmith.app` (`tests/app/`) without going through Typer at all;
these tests only check that the CLI commands parse flags correctly and call
into `app`/`processor` with the right arguments.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from signalsmith import cli as cli_module
from signalsmith.app.context import AppContext
from signalsmith.cli import cli
from signalsmith.config.models import Config, DefaultAction

runner = CliRunner()


class _StopLoopError(Exception):
    """Raised by the mocked `run_daemon` so `daemon` returns right after
    calling it, without actually looping - the loop itself is tested in
    `tests/app/test_daemon.py`."""


@pytest.fixture
def app_ctx() -> AppContext:
    provider = MagicMock()
    provider.name = "github"
    provider.get_notifications.return_value = []
    provider.get_authenticated_user.return_value = "me"
    provider.poll_interval = None
    return AppContext(
        config=Config(default_action=DefaultAction.IGNORE, rules=[]),
        provider=provider,
        spool=MagicMock(),
        ignore_store=MagicMock(),
    )


@pytest.fixture(autouse=True)
def patch_common(monkeypatch: pytest.MonkeyPatch, app_ctx: AppContext) -> None:
    monkeypatch.setattr(cli_module, "build_app_context", lambda *a, **k: app_ctx)
    monkeypatch.setattr(cli_module, "run_daemon", MagicMock(side_effect=_StopLoopError))


def test_daemon_non_interactive_never_opens_notify_runtime(
    monkeypatch: pytest.MonkeyPatch, app_ctx: AppContext
) -> None:
    open_runtime = MagicMock()
    monkeypatch.setattr(cli_module, "open_notify_runtime", open_runtime)

    result = runner.invoke(cli, ["daemon", "--non-interactive"])

    assert isinstance(result.exception, _StopLoopError)
    open_runtime.assert_not_called()
    assert app_ctx.notify_runtime is None


def test_daemon_interactive_by_default_opens_notify_runtime(
    monkeypatch: pytest.MonkeyPatch, app_ctx: AppContext
) -> None:
    runtime = MagicMock()
    open_runtime = MagicMock(return_value=runtime)
    monkeypatch.setattr(cli_module, "open_notify_runtime", open_runtime)

    result = runner.invoke(cli, ["daemon"])

    assert isinstance(result.exception, _StopLoopError)
    open_runtime.assert_called_once_with(
        app_ctx.config, app_ctx.provider, app_ctx.ignore_store
    )
    assert app_ctx.notify_runtime is runtime


def test_daemon_poll_interval_flag_overrides_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "open_notify_runtime", MagicMock())

    result = runner.invoke(
        cli, ["daemon", "--non-interactive", "--poll-interval", "42"]
    )

    assert isinstance(result.exception, _StopLoopError)
    _, kwargs = cli_module.run_daemon.call_args  # type: ignore[attr-defined]
    assert kwargs["interval"] == 42


def test_run_invokes_process_cycle_and_prints_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = MagicMock()
    stats.summary.return_value = "found=0"
    stats.breakdown.return_value = ""
    process_cycle = MagicMock(return_value=stats)
    monkeypatch.setattr(cli_module, "process_cycle", process_cycle)

    result = runner.invoke(cli, ["run"])

    assert result.exit_code == 0
    process_cycle.assert_called_once()
    assert "found=0" in result.output


def test_run_rejects_cache_only_and_refresh_notifications_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "process_cycle", MagicMock())

    result = runner.invoke(cli, ["run", "--cache-only", "--refresh-notifications"])

    assert result.exit_code == 1


def test_cache_clean_reports_when_nothing_to_remove(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_cache_dir", lambda: tmp_path / "cache")

    result = runner.invoke(cli, ["cache", "clean"])

    assert result.exit_code == 0
    assert "No cache directory found" in result.output
