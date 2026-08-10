"""Resolve a rule's configured action and build the `Action` to execute."""

from typing import Any

from ..config.models import Config, Rule, RuleAction
from ..github.models import GitHubIssue, GitHubNotification, GitHubPullRequest
from ..protocols import NotificationProvider
from ..state.spool import SpoolManager
from .base import Action, ActionKind
from .registry import ACTION_SPECS, ActionBuildContext, SubjectFetcher
from .runtime import NotifyRuntime

__all__ = ["create_action_for_rule", "resolve_action_config"]


def resolve_action_config(
    action: RuleAction, config: Config, *, rule_id: str | None = None
) -> tuple[ActionKind, Any]:
    """Resolve a rule's action (inline or `ref`) to its kind and config.

    The single place that resolves ref-vs-inline: it walks `ACTION_SPECS`
    rather than checking `notify`/`mark_as_read`/`ignore` by name, so a new
    entry in `ACTION_SPECS` is picked up automatically.
    """
    if action.ref:
        if action.ref not in config.actions:
            prefix = f"Rule {rule_id!r} " if rule_id else "Rule action "
            raise ValueError(f"{prefix}references undefined action {action.ref!r}")
        source: Any = config.actions[action.ref]
    else:
        source = action

    for kind in ACTION_SPECS:
        value = getattr(source, kind.value)
        if value is not None:
            return kind, value

    raise ValueError("Rule action has no recognized action kind")  # pragma: no cover


def create_action_for_rule(
    notification: GitHubNotification,
    action: RuleAction,
    rule_id: str,
    provider: NotificationProvider,
    spool: SpoolManager,
    config: Config,
    force: bool = False,
    subject: GitHubIssue | GitHubPullRequest | None = None,
    *,
    rule: Rule | None = None,
    notify_runtime: NotifyRuntime | None = None,
    account: dict[str, Any] | None = None,
    fetch_subject: SubjectFetcher | None = None,
) -> Action:
    """Create the appropriate action for a notification based on `action`.

    Args:
        notification: The notification to create an action for
        action: The rule action to resolve (inline or `ref`)
        rule_id: The matched rule's id, or `"__default__"` for the
            default-action fallback
        provider: Notification provider for marking as read
        spool: Spool manager for renotify suppression and durable records
        config: Application configuration (for renotify_interval and action definitions)
        force: If True, ignore renotify interval for notify actions
        subject: The subject fetched while evaluating this rule, if any
        rule: The matched rule, or None for the default-action fallback
        notify_runtime: Interactive-notification context from `daemon`, if any
        account: `account` variable for `notice`/`notify` templates
        fetch_subject: Fetches a subject on demand for a `notify` action's
            templates when `subject` wasn't already fetched during rule
            matching (unused by other action kinds)

    Returns:
        The appropriate action to execute
    """
    kind, action_config = resolve_action_config(action, config, rule_id=rule_id)
    ctx = ActionBuildContext(
        notification=notification,
        rule_id=rule_id,
        rule=rule,
        provider=provider,
        spool=spool,
        config=config,
        force=force,
        subject=subject,
        notify_runtime=notify_runtime,
        account=account or {},
        fetch_subject=fetch_subject,
    )
    return ACTION_SPECS[kind].build(ctx, action_config)
