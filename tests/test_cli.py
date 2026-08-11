"""CLI-level tests: flag parsing and wiring only.

Everything about *how* a cycle or the daemon loop runs is tested directly
against `signalsmith.app` (`tests/app/`) without going through Typer at all;
these tests only check that the CLI commands parse flags correctly and call
into `app`/`processor` with the right arguments.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from signalsmith import cli as cli_module
from signalsmith.app.context import AppContext
from signalsmith.cli import cli
from signalsmith.config.models import Config, DefaultAction
from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.notification.models import NotificationOutcome
from signalsmith.state.models import HistoryEntry

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
        history_store=MagicMock(),
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


# ---------------------------------------------------------------------------
# history list
# ---------------------------------------------------------------------------


def _make_notification() -> GitHubNotification:
    return GitHubNotification(
        id="456",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="PR: fix thing",
            url="https://api.github.com/repos/a/b/pulls/2",
            type="PullRequest",
        ),
        repository=GitHubRepository(id=2, name="b", full_name="a/b"),
        url="https://api.github.com/notifications/threads/456",
        subscription_url="https://api.github.com/notifications/threads/456/sub",
    )


def _make_entry(
    provider: str = "github",
    notification_id: str = "456",
    outcome: NotificationOutcome = NotificationOutcome.NOTIFIED,
    rule_id: str = "r1",
    rendered_title: str | None = "T",
    rendered_body: str | None = "B",
) -> HistoryEntry:
    return HistoryEntry(
        provider=provider,
        notification_id=notification_id,
        recorded_at=datetime(2026, 8, 11, tzinfo=UTC),
        outcome=outcome,
        rule_id=rule_id,
        notification=_make_notification(),
        rendered_title=rendered_title,
        rendered_body=rendered_body,
    )


class TestHistoryList:
    @pytest.fixture(autouse=True)
    def _patch_history_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mock_store = MagicMock()
        self.mock_store_class = MagicMock(return_value=self.mock_store)
        monkeypatch.setattr(cli_module, "HistoryStore", self.mock_store_class)

    def test_empty(self) -> None:
        self.mock_store.entries.return_value = []

        result = runner.invoke(cli, ["history", "list"])

        assert result.exit_code == 0
        assert "History is empty" in result.output

    def test_default_format(self) -> None:
        self.mock_store.entries.return_value = [
            (Path("/none/github-456.json"), _make_entry()),
        ]

        result = runner.invoke(cli, ["history", "list"])

        assert result.exit_code == 0
        assert "github-456" in result.output
        assert "notified" in result.output
        assert "r1" in result.output
        assert "PR: fix thing" in result.output

    def test_json_format(self) -> None:
        self.mock_store.entries.return_value = [
            (Path("/none/github-456.json"), _make_entry()),
        ]

        result = runner.invoke(cli, ["history", "list", "--json"])

        assert result.exit_code == 0
        assert '"provider": "github"' in result.output
        assert '"notification_id": "456"' in result.output
        assert '"outcome": "notified"' in result.output

    def test_action_filter_passed(self) -> None:
        self.mock_store.entries.return_value = []

        result = runner.invoke(cli, ["history", "list", "--action", "ignored"])

        assert result.exit_code == 0
        self.mock_store.entries.assert_called_once_with(limit=20, action="ignored")

    def test_limit_passed(self) -> None:
        self.mock_store.entries.return_value = []

        result = runner.invoke(cli, ["history", "list", "--limit", "5"])

        assert result.exit_code == 0
        self.mock_store.entries.assert_called_once_with(limit=5, action=None)

    def test_invalid_action(self) -> None:
        result = runner.invoke(cli, ["history", "list", "--action", "bogus"])

        assert result.exit_code != 0
        assert "Invalid outcome" in result.output

    def test_limit_zero(self) -> None:
        result = runner.invoke(cli, ["history", "list", "--limit", "0"])

        assert result.exit_code != 0

    def test_limit_negative(self) -> None:
        result = runner.invoke(cli, ["history", "list", "--limit", "-1"])

        assert result.exit_code != 0
