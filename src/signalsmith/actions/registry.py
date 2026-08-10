"""The single table mapping each `ActionKind` to how it's built.

This is the extension point: adding a new configurable action kind means
adding a value to `config.models.ActionKind` (`config/models.py`), a field to `ActionDefinition`
and `RuleAction` (`config/models.py`), an action class, and one entry here.
Nothing else needs to know the kind exists - `factory.resolve_action_config`
resolves ref-vs-inline generically by iterating `ACTION_SPECS`, and
`processor.create_actions` reads `action.outcome` rather than switching on
the action's type.
"""

import logging
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
from ..templating import (
    build_context,
    references_subject,
    render_notice,
    render_notify,
)
from .base import Action, ActionKind
from .ignore import IgnoreAction
from .mark_as_read import MarkAsReadAction
from .notify import NotifyAction
from .runtime import NotifyRuntime
from .skip import SkipAction

logger = logging.getLogger(__name__)

__all__ = ["ACTION_SPECS", "ActionBuildContext", "ActionSpec"]

SubjectFetcher = Callable[[str, str, str], GitHubIssue | GitHubPullRequest]


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
    account: dict[str, Any]
    fetch_subject: SubjectFetcher | None = None


@dataclass(frozen=True, kw_only=True)
class ActionSpec:
    kind: ActionKind
    config_type: type
    build: Callable[[ActionBuildContext, Any], Action]


def _resolve_subject_for_templates(
    ctx: ActionBuildContext, action_config: NotifyActionConfig
) -> tuple[GitHubIssue | GitHubPullRequest | None, str | None]:
    """Fetch a subject on demand if a `notice`/`notify` template needs one.

    Returns `(subject, expected_failure)`. `subject` is `ctx.subject` unless a
    fetch happened here. `expected_failure`, when set, names why rendering a
    `subject`-referencing template is expected to fail - passed through to
    `templating.render` so that failure logs at WARNING (config is fine, this
    notification just has no subject to offer) rather than ERROR (ambiguous
    - could be a template typo). Uses `references_subject` rather than
    `template_names` directly so a template with a syntax error can't crash
    action construction here - it still surfaces as an ERROR later, when
    `render` itself tries to compile it.
    """
    if ctx.subject is not None:
        return ctx.subject, None

    sources = [ctx.config.notice.title, ctx.config.notice.body]
    if action_config.title is not None:
        sources.append(action_config.title)
    if action_config.body is not None:
        sources.append(action_config.body)
    if not any(references_subject(source) for source in sources):
        return None, None

    subject_url = ctx.notification.subject.url
    if subject_url is None:
        return None, "notification has no subject URL"

    if ctx.fetch_subject is None:
        return None, "no subject fetcher available"

    try:
        subject = ctx.fetch_subject(
            subject_url, ctx.notification.subject.type, ctx.notification.updated_at
        )
    except NotImplementedError:
        return (
            None,
            f"subject type {ctx.notification.subject.type!r} has no fetchable object",
        )
    except Exception as exc:
        logger.warning(
            "Failed to fetch subject for notification %s while formatting: %s",
            ctx.notification.id,
            exc,
        )
        return None, "subject fetch failed"
    return subject, None


def _build_notify(ctx: ActionBuildContext, action_config: NotifyActionConfig) -> Action:
    should_notify = ctx.force or ctx.spool.should_notify(
        ctx.provider.name, ctx.notification.id, ctx.config.renotify_interval
    )
    if not should_notify:
        return SkipAction(ctx.notification, ctx.rule_id)

    subject, expected_failure = _resolve_subject_for_templates(ctx, action_config)
    context = build_context(
        ctx.notification, subject, ctx.account, ctx.config.variables
    )
    notice = render_notice(
        ctx.config.notice, context, expected_failure=expected_failure
    )
    rendered = render_notify(
        action_config, notice, context, expected_failure=expected_failure
    )

    return NotifyAction(
        ctx.notification,
        rendered,
        ctx.spool,
        ctx.rule_id,
        rule=ctx.rule,
        subject=subject,
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
