"""Action implementations for notification processing.

`processor.create_actions` resolves each matched rule's action to an
`Action` via `create_action_for_rule`, then `execute_actions` runs the
resulting stream. `ActionKind` + `registry.ACTION_SPECS` is the single
extension point for adding a new configurable action kind - see `base.py`.
"""

from .base import Action, ActionKind
from .execute import execute_actions
from .factory import create_action_for_rule, resolve_action_config
from .ignore import IgnoreAction
from .mark_as_read import MarkAsReadAction
from .notify import NotifyAction
from .registry import ACTION_SPECS, ActionBuildContext, ActionSpec
from .runtime import NotifyRuntime
from .skip import SkipAction

__all__ = [
    "ACTION_SPECS",
    "Action",
    "ActionBuildContext",
    "ActionKind",
    "ActionSpec",
    "IgnoreAction",
    "MarkAsReadAction",
    "NotifyAction",
    "NotifyRuntime",
    "SkipAction",
    "create_action_for_rule",
    "execute_actions",
    "resolve_action_config",
]
