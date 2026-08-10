"""Validate the example config files shipped in src/signalsmith/.

These files are referenced from README.md/CONTRIBUTING.md as copy-paste starting
points, so they must both parse as valid Config objects and render their notify
templates without leaving unresolved ${...} placeholders.
"""

import importlib.resources
from pathlib import Path

import pytest

from signalsmith.config.models import Config
from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.notifier import format_template

EXAMPLE_CONFIG_FILENAMES = [
    "example-config.yaml",
    "example-config-with-actions.yaml",
    "config-example-orgs.yaml",
]

_SAMPLE_NOTIFICATION = GitHubNotification(
    id="1",
    reason="mention",
    unread=True,
    updated_at="2026-06-17T00:00:00Z",
    subject=GitHubSubject(title="Sample title", type="Issue"),
    repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
    url="https://api.github.com/notifications/threads/1",
    subscription_url="https://api.github.com/notifications/threads/1/subscription",
)


def _example_config_path(filename: str) -> Path:
    return Path(str(importlib.resources.files("signalsmith") / filename))


@pytest.mark.parametrize("filename", EXAMPLE_CONFIG_FILENAMES)
def test_example_config_loads(filename: str) -> None:
    """Every example config must parse as a valid signalsmith Config."""
    config = Config.load(_example_config_path(filename))
    assert config.rules


@pytest.mark.parametrize("filename", EXAMPLE_CONFIG_FILENAMES)
def test_example_config_notify_templates_resolve(filename: str) -> None:
    """notify title/message templates must not reference unknown variables.

    format_template() leaves unresolved ${...} placeholders untouched (see
    notifier.py), so any leftover "${" after formatting against a real
    notification means the template used a variable notifier doesn't support.
    """
    config = Config.load(_example_config_path(filename))

    notify_configs = [a.notify for a in config.actions.values() if a.notify]
    notify_configs += [r.action.notify for r in config.rules if r.action.notify]

    for notify_config in notify_configs:
        title = format_template(notify_config.title, _SAMPLE_NOTIFICATION)
        message = format_template(notify_config.message, _SAMPLE_NOTIFICATION)
        assert "${" not in title, f"Unresolved variable in title: {title!r}"
        assert "${" not in message, f"Unresolved variable in message: {message!r}"
