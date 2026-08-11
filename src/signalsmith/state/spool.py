"""Durable spool of notified notifications.

Every notification that results in a `notify` action is written here as a
JSON file, keyed by provider + notification id, and kept until the
notification disappears from the provider's unread feed (see `reap`).
This also absorbs the renotify-suppression bookkeeping that `state.json`
used to provide: `last_notified_at` lives on the same entry, so there is
no separate state file to keep in sync with the spool.

Entries are never hard-deleted: `reap`/`clear` move the file to a trash
directory (by convention, under the cache dir so `signalsmith cache clean`
sweeps it) instead.
"""

import json
import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from xdg import xdg_data_home

from ..cache import resolve_cache_dir
from ..config.models import RULE_ADAPTER, Config, Rule
from ..github.models import GitHubIssue, GitHubNotification, GitHubPullRequest
from ..versioning import STATE_VERSION, ensure_store_version
from ._sanitize import _sanitize
from .models import SPOOL_ENTRY_ADAPTER, SpoolEntry, SpoolNotifyEvent

logger = logging.getLogger(__name__)

__all__: list[str] = []

_MAX_NOTIFY_EVENTS = 20


@dataclass
class _Loaded:
    path: Path
    entry: SpoolEntry


class SpoolManager:
    def __init__(self, spool_dir: Path, trash_dir: Path) -> None:
        self._spool_dir = spool_dir
        self._trash_dir = trash_dir
        self._spool_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[tuple[str, str], _Loaded] = {}
        self._load_all()

    @staticmethod
    def resolve_state_dir() -> Path:
        """Resolve the state root directory (holds the spool and its version marker).

        Not configurable, unlike `spool.dir`: it's the anchor for the state
        directory's version marker and for a second state store
        (`state.ignore_store.IgnoreStore`).
        """
        return xdg_data_home() / "signalsmith"

    @classmethod
    def ensure_state_version(cls, state_dir: Path | None = None) -> None:
        """Ensure the state root's version marker is compatible with this signalsmith."""
        ensure_store_version(
            state_dir if state_dir is not None else cls.resolve_state_dir(),
            STATE_VERSION,
            label="State",
            clean_command="signalsmith state clean",
        )

    @staticmethod
    def resolve_spool_dir(config: Config) -> Path:
        """Resolve the spool directory (explicit config > XDG data default)."""
        return config.spool.dir or (xdg_data_home() / "signalsmith" / "spool")

    @staticmethod
    def resolve_trash_dir() -> Path:
        """Resolve the spool trash directory.

        Deliberately not configurable and always under the cache dir
        (regardless of `spool.dir`), so `signalsmith cache clean` always sweeps it.
        """
        return resolve_cache_dir() / "trash" / "spool"

    def _load_all(self) -> None:
        for path in sorted(self._spool_dir.glob("*.json")):
            try:
                entry = SPOOL_ENTRY_ADAPTER.validate_json(path.read_text())
            except Exception:
                logger.warning(
                    "Failed to parse spool file %s; leaving it in place",
                    path,
                    exc_info=True,
                )
                continue
            self._index[entry.provider, entry.notification_id] = _Loaded(path, entry)

    def _path_for(self, provider: str, notification_id: str) -> Path:
        name = f"{_sanitize(provider)}-{_sanitize(notification_id)}.json"
        return self._spool_dir / name

    def should_notify(
        self, provider: str, notification_id: str, renotify_interval: int
    ) -> bool:
        loaded = self._index.get((provider, notification_id))
        if loaded is None:
            return True
        elapsed = (datetime.now(UTC) - loaded.entry.last_notified_at).total_seconds()
        return elapsed >= renotify_interval

    def record_notify(
        self,
        *,
        provider: str,
        notification: GitHubNotification,
        subject: GitHubIssue | GitHubPullRequest | None,
        subject_type: str | None,
        rule: Rule | None,
        rule_id: str,
        title: str,
        body: str,
    ) -> None:
        now = datetime.now(UTC)
        key = (provider, notification.id)
        loaded = self._index.get(key)
        subject_dict = (
            None if subject is None else json.loads(subject.model_dump_json())
        )
        rule_dict = (
            None if rule is None else RULE_ADAPTER.dump_python(rule, mode="json")
        )

        event = SpoolNotifyEvent(
            notified_at=now, rule_id=rule_id, title=title, body=body
        )

        if loaded is not None:
            events = [*loaded.entry.notify_events, event][-_MAX_NOTIFY_EVENTS:]
            entry = replace(
                loaded.entry,
                last_notified_at=now,
                notify_count=loaded.entry.notify_count + 1,
                rule_id=rule_id,
                rule=rule_dict,
                title=title,
                body=body,
                web_url=notification.subject.web_url,
                notification=notification,
                subject_type=subject_type or loaded.entry.subject_type,
                subject=subject_dict
                if subject_dict is not None
                else loaded.entry.subject,
                notify_events=events,
            )
            path = loaded.path
        else:
            entry = SpoolEntry(
                provider=provider,
                notification_id=notification.id,
                received_at=now,
                last_notified_at=now,
                notify_count=1,
                rule_id=rule_id,
                rule=rule_dict,
                title=title,
                body=body,
                web_url=notification.subject.web_url,
                notification=notification,
                subject_type=subject_type,
                subject=subject_dict,
                notify_events=[event],
            )
            path = self._path_for(provider, notification.id)

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(SPOOL_ENTRY_ADAPTER.dump_json(entry, indent=2))
        tmp_path.replace(path)

        self._index[key] = _Loaded(path, entry)

    def entries(self) -> Iterator[tuple[Path, SpoolEntry]]:
        for loaded in self._index.values():
            yield loaded.path, loaded.entry

    def _to_trash(self, path: Path) -> None:
        self._trash_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        dest = self._trash_dir / f"{path.stem}-{timestamp}{path.suffix}"
        # Extremely unlikely (same microsecond), but never silently clobber
        # an existing trashed entry.
        suffix = 1
        while dest.exists():
            dest = self._trash_dir / f"{path.stem}-{timestamp}-{suffix}{path.suffix}"
            suffix += 1
        shutil.move(str(path), str(dest))

    def reap(self, provider: str, live_ids: set[str]) -> int:
        removed = 0
        for key, loaded in list(self._index.items()):
            entry_provider, notification_id = key
            if entry_provider != provider or notification_id in live_ids:
                continue
            self._to_trash(loaded.path)
            del self._index[key]
            removed += 1
        return removed

    def clear(self) -> int:
        removed = 0
        for loaded in list(self._index.values()):
            self._to_trash(loaded.path)
            removed += 1
        self._index.clear()
        return removed
