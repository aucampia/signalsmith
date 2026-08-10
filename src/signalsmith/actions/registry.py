"""The single table mapping each `ActionKind` to how it's built.

This is the extension point: adding a new configurable action kind means
adding a value to `config.models.ActionKind` (`config/models.py`), a field to `ActionDefinition`
and `RuleAction` (`config/models.py`), an action class, and one entry here.
Nothing else needs to know the kind exists - `factory.resolve_action_config`
resolves ref-vs-inline generically by iterating `ACTION_SPECS`, and
`processor.create_actions` reads `action.outcome` rather than switching on
the action's type.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config.models import (
    Config,
    IgnoreActionConfig,
    MarkAsReadActionConfig,
    NotifyActionConfig,
    Rule,
)
from ..github.models import GitHubIssue, GitHubNotification, GitHubPullRequest
from ..protocols import NotificationProvider
from ..state.spool import SpoolManager
from .base import Action, ActionKind
from .ignore import IgnoreAction
from .mark_as_read import MarkAsReadAction
from .notify import NotifyAction
from .runtime import NotifyRuntime
from .skip import SkipAction

__all__ = ["ACTION_SPECS", "ActionBuildContext", "ActionSpec"]


@dataclass(frozen=True, kw_only=True)
class ActionBuildContext:
    """Everything an `ActionSpec.build` might need, regardless of kind."""

    notification: GitHubNotification
    rule_id: str
    rule: Rule | None
    provider: NotificationProvider
    spool: SpoolManager
    config: Config
    force: bool
    subject: GitHubIssue | GitHubPullRequest | None
    notify_runtime: NotifyRuntime | None


@dataclass(frozen=True, kw_only=True)
class ActionSpec:
    kind: ActionKind
    config_type: type
    build: Callable[[ActionBuildContext, Any], Action]


def _build_notify(ctx: ActionBuildContext, action_config: NotifyActionConfig) -> Action:
    should_notify = ctx.force or ctx.spool.should_notify(
        ctx.provider.name, ctx.notification.id, ctx.config.renotify_interval
    )
    if not should_notify:
        return SkipAction(ctx.notification, ctx.rule_id)
    return NotifyAction(
        ctx.notification,
        action_config,
        ctx.spool,
        ctx.rule_id,
        rule=ctx.rule,
        subject=ctx.subject,
        provider_name=ctx.provider.name,
        notify_runtime=ctx.notify_runtime,
    )


def _build_mark_as_read(
    ctx: ActionBuildContext, action_config: MarkAsReadActionConfig
) -> Action:
    return MarkAsReadAction(ctx.notification, ctx.provider, ctx.rule_id)


def _build_ignore(ctx: ActionBuildContext, action_config: IgnoreActionConfig) -> Action:
    return IgnoreAction(ctx.notification, ctx.rule_id)


ACTION_SPECS: dict[ActionKind, ActionSpec] = {
    ActionKind.NOTIFY: ActionSpec(
        kind=ActionKind.NOTIFY, config_type=NotifyActionConfig, build=_build_notify
    ),
    ActionKind.MARK_AS_READ: ActionSpec(
        kind=ActionKind.MARK_AS_READ,
        config_type=MarkAsReadActionConfig,
        build=_build_mark_as_read,
    ),
    ActionKind.IGNORE: ActionSpec(
        kind=ActionKind.IGNORE, config_type=IgnoreActionConfig, build=_build_ignore
    ),
}
