import logging
from pathlib import Path

import pytest

from signalsmith.config.models import Config, IgnoreActionConfig, Rule, RuleAction
from signalsmith.versioning import VersionError

_MINIMAL_RULES = """
rules:
  - id: noop
    expression: 'true'
    action:
      ignore: {}
"""


def _write_config(path: Path, body: str) -> Path:
    config_path = path / "config.yaml"
    config_path.write_text(body)
    return config_path


def test_config_load_without_version_key_is_refused(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _MINIMAL_RULES)
    with pytest.raises(VersionError):
        Config.load(config_path)


def test_config_load_wrong_major_is_refused(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "version: '1.0'\n" + _MINIMAL_RULES)
    with pytest.raises(VersionError) as exc_info:
        Config.load(config_path)
    assert "Update the config file" in str(exc_info.value)


@pytest.mark.parametrize(
    ("version", "expect_warning"),
    [("2.0", False), ("2.9", True)],
    ids=["matching", "newer-minor"],
)
def test_config_load_compatible_version_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, version: str, expect_warning: bool
) -> None:
    config_path = _write_config(tmp_path, f"version: '{version}'\n" + _MINIMAL_RULES)
    with caplog.at_level(logging.WARNING):
        config = Config.load(config_path)
    assert config.rules[0].id == "noop"
    assert str(config.version) == version
    assert any("newer" in record.message for record in caplog.records) == expect_warning


def test_config_default_version_is_current() -> None:
    """Constructing Config() directly (as most tests do) needs no version key."""
    config = Config(
        rules=[
            Rule(
                id="noop",
                expression="true",
                action=RuleAction(ignore=IgnoreActionConfig()),
            )
        ]
    )
    assert str(config.version) == "2.0"


def _clear_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "XDG_CONFIG_HOME",
        "SIGNALSMITH_CONFIG_DIR",
        "SIGNALSMITH_CONFIG",
        "SIGNALSMITH_TEST_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_resolve_config_dir_defaults_to_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert Config.resolve_config_dir() == tmp_path / "signalsmith"


def test_resolve_config_dir_uses_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SIGNALSMITH_CONFIG_DIR", str(tmp_path / "custom"))
    assert Config.resolve_config_dir() == tmp_path / "custom"


def test_resolve_config_path_uses_config_dir_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("SIGNALSMITH_CONFIG_DIR", str(tmp_path))
    assert Config.resolve_config_path() == tmp_path / "config.yaml"


def test_resolve_config_path_env_wins_over_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("SIGNALSMITH_CONFIG_DIR", str(tmp_path / "dir"))
    monkeypatch.setenv(
        "SIGNALSMITH_CONFIG", str(tmp_path / "elsewhere" / "config.yaml")
    )
    assert Config.resolve_config_path() == tmp_path / "elsewhere" / "config.yaml"


def test_resolve_config_path_explicit_arg_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("SIGNALSMITH_CONFIG", str(tmp_path / "env" / "config.yaml"))
    explicit = tmp_path / "explicit" / "config.yaml"
    assert Config.resolve_config_path(explicit) == explicit


def test_resolve_test_dir_uses_config_dir_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("SIGNALSMITH_CONFIG_DIR", str(tmp_path))
    assert Config.resolve_test_dir() == tmp_path / "tests"


def test_resolve_test_dir_ignores_signalsmith_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SIGNALSMITH_CONFIG pointing elsewhere must not drag the test dir with it."""
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv(
        "SIGNALSMITH_CONFIG", str(tmp_path / "elsewhere" / "config.yaml")
    )
    assert Config.resolve_test_dir() == tmp_path / "xdg" / "signalsmith" / "tests"


def test_resolve_test_dir_env_wins_over_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("SIGNALSMITH_CONFIG_DIR", str(tmp_path / "dir"))
    monkeypatch.setenv("SIGNALSMITH_TEST_DIR", str(tmp_path / "custom-tests"))
    assert Config.resolve_test_dir() == tmp_path / "custom-tests"


def test_resolve_test_dir_explicit_arg_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("SIGNALSMITH_TEST_DIR", str(tmp_path / "env-tests"))
    explicit = tmp_path / "explicit-tests"
    assert Config.resolve_test_dir(explicit) == explicit
