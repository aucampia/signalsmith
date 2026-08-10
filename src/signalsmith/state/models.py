from dataclasses import field
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from ..config.models import Rule
from ..github.models import GitHubNotification

__all__: list[str] = []


@dataclass(kw_only=True)
class SpoolNotifyEvent:
    """One notify occurrence recorded in a spool entry's history."""

    notified_at: datetime
    rule_id: str
    title: str
    message: str


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
    rule: Rule | None = None
    title: str
    message: str
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
