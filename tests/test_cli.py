import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from signalsmith import cli as cli_module
from signalsmith.cli import cli
from signalsmith.config.models import Config, DefaultAction
from signalsmith.state.spool import SpoolManager

runner = CliRunner()


class _StopLoopError(Exception):
    """Raised by the mocked `time.sleep` to escape `daemon`'s `while True` after one cycle."""


def _minimal_config() -> Config:
    return Config(default_action=DefaultAction.IGNORE, rules=[])


@pytest.fixture(autouse=True)
def patch_common(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MagicMock:
    monkeypatch.setattr(cli_module, "_load_config", lambda *a, **k: _minimal_config())
    monkeypatch.setattr(cli_module, "get_github_token", lambda: "token")

    provider = MagicMock()
    provider.name = "github"
    provider.get_notifications.return_value = []
    provider.get_authenticated_user.return_value = "me"
    provider.poll_interval = None
    monkeypatch.setattr(cli_module, "_open_github_client", lambda *a, **k: provider)

    monkeypatch.setattr(
        cli_module,
        "_open_spool",
        lambda *a, **k: SpoolManager(tmp_path / "spool", tmp_path / "trash"),
    )
    monkeypatch.setattr(cli_module, "resolve_ignore_dir", lambda: tmp_path / "ignored")
    monkeypatch.setattr(time, "sleep", MagicMock(side_effect=_StopLoopError))

    return provider


def test_daemon_non_interactive_never_constructs_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_dispatcher_cls = MagicMock()
    monkeypatch.setattr(cli_module, "NotificationDispatcher", mock_dispatcher_cls)

    result = runner.invoke(cli, ["daemon", "--non-interactive"])

    assert isinstance(result.exception, _StopLoopError)
    mock_dispatcher_cls.assert_not_called()


def test_daemon_interactive_by_default_constructs_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_dispatcher_cls = MagicMock()
    monkeypatch.setattr(cli_module, "NotificationDispatcher", mock_dispatcher_cls)

    result = runner.invoke(cli, ["daemon"])

    assert isinstance(result.exception, _StopLoopError)
    mock_dispatcher_cls.assert_called_once_with(app_name="signalsmith")


def test_daemon_falls_back_when_dispatcher_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "NotificationDispatcher",
        MagicMock(side_effect=RuntimeError("no display")),
    )

    # Must not propagate the construction failure - falls back to non-interactive.
    result = runner.invoke(cli, ["daemon"])

    assert isinstance(result.exception, _StopLoopError)
