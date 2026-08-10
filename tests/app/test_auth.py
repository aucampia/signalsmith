from unittest.mock import MagicMock

import pytest

from signalsmith.app.auth import resolve_github_token
from signalsmith.errors import AuthError


def test_github_token_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert resolve_github_token() == "from-env"


def test_gh_token_env_used_when_github_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "from-gh-token-env")

    assert resolve_github_token() == "from-gh-token-env"


def test_falls_back_to_gh_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        "signalsmith.app.auth.subprocess.run",
        MagicMock(return_value=MagicMock(stdout="from-gh-cli\n")),
    )

    assert resolve_github_token() == "from-gh-cli"


def test_raises_auth_error_when_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        "signalsmith.app.auth.subprocess.run",
        MagicMock(side_effect=FileNotFoundError()),
    )

    with pytest.raises(AuthError):
        resolve_github_token()
