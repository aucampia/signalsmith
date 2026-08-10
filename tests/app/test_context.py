from pathlib import Path
from unittest.mock import MagicMock

import pytest

from signalsmith.app.context import AppContext, build_app_context, open_notify_runtime
from signalsmith.config.models import Config, DefaultAction, NotifyActionsConfig


def _minimal_config(**overrides: object) -> Config:
    return Config(default_action=DefaultAction.IGNORE, rules=[], **overrides)  # type: ignore[arg-type]


def _patch_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: Config
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    monkeypatch.setattr(
        "signalsmith.app.context.Config", MagicMock(load=MagicMock(return_value=config))
    )
    monkeypatch.setattr("signalsmith.app.context.resolve_github_token", lambda: "tok")

    provider = MagicMock()
    github_client_cls = MagicMock(return_value=provider)
    monkeypatch.setattr("signalsmith.app.context.GitHubClient", github_client_cls)

    spool_instance = MagicMock()
    spool_manager_cls = MagicMock(return_value=spool_instance)
    spool_manager_cls.resolve_spool_dir.return_value = tmp_path / "spool"
    spool_manager_cls.resolve_trash_dir.return_value = tmp_path / "trash"
    monkeypatch.setattr("signalsmith.app.context.SpoolManager", spool_manager_cls)

    ignore_instance = MagicMock()
    ignore_store_cls = MagicMock(return_value=ignore_instance)
    ignore_store_cls.resolve_dir.return_value = tmp_path / "ignored"
    monkeypatch.setattr("signalsmith.app.context.IgnoreStore", ignore_store_cls)

    return github_client_cls, spool_manager_cls, ignore_store_cls, provider


def test_build_app_context_wires_config_provider_spool_ignore_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _minimal_config()
    _, spool_manager_cls, _, provider = _patch_construction(
        monkeypatch, tmp_path, config
    )

    ctx = build_app_context()

    assert isinstance(ctx, AppContext)
    assert ctx.config is config
    assert ctx.provider is provider
    assert ctx.notify_runtime is None
    spool_manager_cls.ensure_state_version.assert_called_once()


def test_build_app_context_passes_cache_only_to_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _minimal_config()
    github_client_cls, _, _, _ = _patch_construction(monkeypatch, tmp_path, config)

    build_app_context(cache_only=True)

    _, kwargs = github_client_cls.call_args
    assert kwargs["cache_only"] is True


def test_build_app_context_skips_state_version_check_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _minimal_config()
    _, spool_manager_cls, _, _ = _patch_construction(monkeypatch, tmp_path, config)

    build_app_context(check_state_version=False)

    spool_manager_cls.ensure_state_version.assert_not_called()


def test_open_notify_runtime_returns_none_when_dispatcher_fails_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "signalsmith.app.context.NotificationDispatcher",
        MagicMock(side_effect=RuntimeError("no dbus")),
    )
    config = _minimal_config(notify_actions=NotifyActionsConfig(enabled=True))

    result = open_notify_runtime(config, MagicMock(), MagicMock())

    assert result is None


def test_open_notify_runtime_builds_runtime_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = MagicMock()
    monkeypatch.setattr(
        "signalsmith.app.context.NotificationDispatcher",
        MagicMock(return_value=dispatcher),
    )
    config = _minimal_config(
        notify_actions=NotifyActionsConfig(
            enabled=True, max_concurrent=3, wait_timeout=7
        )
    )
    provider = MagicMock()
    ignore_store = MagicMock()

    runtime = open_notify_runtime(config, provider, ignore_store)

    assert runtime is not None
    assert runtime.dispatcher is dispatcher
    assert runtime.provider is provider
    assert runtime.ignore_store is ignore_store
    assert runtime.actions_enabled is True
    assert runtime.max_concurrent == 3
    assert runtime.wait_timeout == 7
