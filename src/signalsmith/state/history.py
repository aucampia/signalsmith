"""Durable history of every notification outcome.

Every notification that flows through the pipeline is recorded here as a JSON
file, keyed by (provider, notification_id). Entries are overwritten when the
same notification is processed again in a later poll cycle, so only the most
recent outcome for each notification is retained. Rendered title/body/subject
fields from a `notified` outcome are carried forward on overwrite when missing.
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..github.models import GitHubIssue, GitHubNotification, GitHubPullRequest
from ..notification.models import NotificationOutcome
from ._sanitize import _sanitize
from .models import HISTORY_ENTRY_ADAPTER, HistoryEntry
from .spool import SpoolManager

logger = logging.getLogger(__name__)

__all__: list[str] = []


@dataclass
class _Loaded:
    path: Path
    entry: HistoryEntry


class HistoryStore:
    def __init__(self, history_dir: Path) -> None:
        self._history_dir = history_dir
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[tuple[str, str], _Loaded] = {}
        self._load_all()

    @staticmethod
    def resolve_dir() -> Path:
        """Resolve the history directory under the same state root as the spool."""
        return SpoolManager.resolve_state_dir() / "history"

    def _load_all(self) -> None:
        for path in sorted(self._history_dir.glob("*.json")):
            try:
                entry = HISTORY_ENTRY_ADAPTER.validate_json(path.read_text())
            except Exception:
                logger.warning(
                    "Failed to parse history file %s; leaving it in place",
                    path,
                    exc_info=True,
                )
                continue
            self._index[entry.provider, entry.notification_id] = _Loaded(path, entry)

    def _path_for(self, provider: str, notification_id: str) -> Path:
        name = f"{_sanitize(provider)}-{_sanitize(notification_id)}.json"
        return self._history_dir / name

    def record(
        self,
        *,
        provider: str,
        notification: GitHubNotification,
        outcome: NotificationOutcome,
        rule_id: str,
        rendered_title: str | None = None,
        rendered_body: str | None = None,
        subject: GitHubIssue | GitHubPullRequest | None = None,
        subject_type: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        key = (provider, notification.id)

        existing = self._index.get(key)
        if rendered_title is None and existing is not None:
            rendered_title = existing.entry.rendered_title
        if rendered_body is None and existing is not None:
            rendered_body = existing.entry.rendered_body
        if subject is None and existing is not None:
            subject_from_prev = existing.entry.subject
            if subject_from_prev is not None:
                subject = (
                    GitHubPullRequest.model_validate(subject_from_prev)
                    if existing.entry.subject_type == "PullRequest"
                    else GitHubIssue.model_validate(subject_from_prev)
                )
        if subject_type is None and existing is not None:
            subject_type = existing.entry.subject_type
        subject_dict = (
            None if subject is None else json.loads(subject.model_dump_json())
        )

        entry = HistoryEntry(
            provider=provider,
            notification_id=notification.id,
            recorded_at=now,
            outcome=outcome,
            rule_id=rule_id,
            notification=notification,
            rendered_title=rendered_title,
            rendered_body=rendered_body,
            subject=subject_dict,
            subject_type=subject_type,
        )
        path = self._path_for(provider, notification.id)

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(HISTORY_ENTRY_ADAPTER.dump_json(entry, indent=2))
        tmp_path.replace(path)

        self._index[key] = _Loaded(path, entry)

    def entries(
        self,
        *,
        limit: int | None = None,
        action: str | None = None,
    ) -> Iterable[tuple[Path, HistoryEntry]]:
        """Yield entries sorted by `recorded_at` descending.

        Args:
            limit: Max entries to return (None = no limit).
            action: Filter by `NotificationOutcome` name (e.g. "notified").
        """
        outcome_filter = NotificationOutcome(action) if action is not None else None
        sorted_entries = sorted(
            self._index.values(),
            key=lambda loaded: loaded.entry.recorded_at,
            reverse=True,
        )
        count = 0
        for loaded in sorted_entries:
            if outcome_filter is not None and loaded.entry.outcome != outcome_filter:
                continue
            yield loaded.path, loaded.entry
            count += 1
            if limit is not None and count >= limit:
                break

    def clear(self) -> int:
        removed = len(self._index)
        for loaded in self._index.values():
            loaded.path.unlink(missing_ok=True)
        self._index.clear()
        return removed
