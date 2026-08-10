"""Guards the `ActionKind` extension point.

Adding a new configurable action kind (see `zxxi-TODO.md`: unsubscribe,
assign-to-self, add-as-reviewer) means adding a value to `ActionKind`, a
field to `ActionDefinition`/`RuleAction`, and an `ACTION_SPECS` entry. This
file is parametrized over `ActionKind` so forgetting any one of those three
fails a test here, rather than only surfacing later as a runtime
`AttributeError`/`ValueError` deep in `resolve_action_config`.
"""

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from conftest import MockProvider

from signalsmith.actions.base import Action
from signalsmith.actions.registry import ACTION_SPECS, ActionBuildContext
from signalsmith.config.models import (
    ActionDefinition,
    ActionKind,
    Config,
    IgnoreActionConfig,
    MarkAsReadActionConfig,
    NotifyActionConfig,
    RuleAction,
)
from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.state.spool import SpoolManager

# One sample config instance per kind, used to build an `Action` below.
# Adding a value to `ActionKind` without adding an entry here fails
# `test_every_kind_has_a_sample_action_that_builds_with_an_outcome` with a
# clear `KeyError`, rather than a puzzling failure elsewhere.
_SAMPLE_CONFIGS: dict[ActionKind, Any] = {
    ActionKind.NOTIFY: NotifyActionConfig(title="t", body="m"),
    ActionKind.MARK_AS_READ: MarkAsReadActionConfig(),
    ActionKind.IGNORE: IgnoreActionConfig(),
}


@pytest.fixture
def notification() -> GitHubNotification:
    return GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test Issue", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )


@pytest.mark.parametrize("kind", list(ActionKind))
def test_every_kind_has_an_action_spec(kind: ActionKind) -> None:
    assert kind in ACTION_SPECS
    assert ACTION_SPECS[kind].kind == kind


@pytest.mark.parametrize("kind", list(ActionKind))
def test_every_kind_has_a_field_on_action_definition_and_rule_action(
    kind: ActionKind,
) -> None:
    definition_fields = {f.name for f in dataclasses.fields(ActionDefinition)}
    rule_action_fields = {f.name for f in dataclasses.fields(RuleAction)}
    assert kind.value in definition_fields
    assert kind.value in rule_action_fields


@pytest.mark.parametrize("kind", list(ActionKind))
def test_every_kind_has_a_sample_action_that_builds_with_an_outcome(
    kind: ActionKind, notification: GitHubNotification, tmp_path: Path
) -> None:
    spool = SpoolManager(tmp_path / "spool", tmp_path / "trash")
    ctx = ActionBuildContext(
        notification=notification,
        rule_id="my_rule",
        rule=None,
        provider=MockProvider([]),
        spool=spool,
        config=Config(rules=[]),
        force=False,
        subject=None,
        notify_runtime=None,
        account={},
    )

    action: Action = ACTION_SPECS[kind].build(ctx, _SAMPLE_CONFIGS[kind])

    assert action.outcome is not None
