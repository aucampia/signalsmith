from dataclasses import field
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from ..github.models import GitHubNotification
from ..notification.models import NotificationOutcome

__all__: list[str] = []


@dataclass(kw_only=True)
class SpoolNotifyEvent:
    """One notify occurrence recorded in a spool entry's history."""

    notified_at: datetime
    rule_id: str
    title: str
    body: str


@dataclass(kw_only=True)
class SpoolEntry:
    """Durable on-disk record of a notified notification.

    Written by `NotifyAction.execute` (see `spool.py`), kept until the
    notification disappears from the provider's unread feed. Also carries
    `last_notified_at`/`notify_count`, which drive renotify suppression -
    the role `state.json` used to serve.
    """

    provider: str
    notification_id: str
    received_at: datetime
    last_notified_at: datetime
    notify_count: int = 1
    rule_id: str
    # Raw JSON, not `config.models.Rule`: the spool is a durable record of
    # what happened, not a config replica, and a `Rule` transitively embeds
    # every action-kind config (see `NotifyActionConfig`) - tying spool
    # schema compatibility to config schema compatibility, forcing a
    # STATE_VERSION bump on every unrelated config change. Dumped via
    # `signalsmith.config.models.RULE_ADAPTER` at write time (`spool.py`),
    # never re-validated as a `Rule` on read.
    rule: dict[str, Any] | None = None
    title: str
    body: str
    web_url: str | None = None
    notification: GitHubNotification
    subject_type: str | None = None
    # Raw JSON, not GitHubIssue | GitHubPullRequest: both models use
    # `extra: "allow"` and share every required field, so the union is
    # ambiguous on read-back. A dict + subject_type round-trips exactly.
    subject: dict[str, Any] | None = None
    notify_events: list[SpoolNotifyEvent] = field(default_factory=list)


SPOOL_ENTRY_ADAPTER: TypeAdapter[SpoolEntry] = TypeAdapter(SpoolEntry)


@dataclass(kw_only=True)
class IgnoredEntry:
    """Durable on-disk record of a permanently-ignored subject.

    Written when the user presses the "Ignore" button on an interactive
    notification (see `IgnoreStore.add`). Distinct from the transient
    `ignore` rule action: this is consulted on every future run regardless
    of what rules would otherwise decide, until manually removed.
    """

    subject_url: str
    added_at: datetime
    title: str
    repository: str
    subject_type: str | None = None


IGNORED_ENTRY_ADAPTER: TypeAdapter[IgnoredEntry] = TypeAdapter(IgnoredEntry)


@dataclass(kw_only=True)
class HistoryEntry:
    """Durable on-disk record of a notification and its outcome.

    Written for every notification that passes through the pipeline (every
    outcome: notified, ignored, skipped, filtered, etc.), not just the ones
    that trigger a desktop alert. `notification` is always present (it is
    the raw response from the API list call). `rendered_title`/`rendered_body`
    and `subject`/`subject_type` are populated for `NOTIFIED` outcomes (where a
    desktop notification was generated and the Issue/PR may have been fetched
    for template rendering), and are carried forward from a previous entry when
    a later cycle overwrites the same notification with a non-notified outcome.
    """

    provider: str
    notification_id: str
    recorded_at: datetime
    outcome: NotificationOutcome
    rule_id: str
    notification: GitHubNotification
    rendered_title: str | None = None
    rendered_body: str | None = None
    subject: dict[str, Any] | None = None
    subject_type: str | None = None


HISTORY_ENTRY_ADAPTER: TypeAdapter[HistoryEntry] = TypeAdapter(HistoryEntry)
