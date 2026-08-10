"""Durable store of permanently-ignored subjects.

Written to by the "Ignore" button on an interactive notification (see
`actions.NotifyAction`, `notify_dispatcher.NotificationDispatcher`) and
consulted on every run (`processor.create_actions`) regardless of what a
rule would otherwise decide. Distinct from the spool: entries here are
permanent until manually removed, so - unlike the spool - there's no
`reap`/trash lifecycle, just hard deletes via `remove`/`clear`.
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..github.models import GitHubNotification
from .models import IGNORED_ENTRY_ADAPTER, IgnoredEntry
from .spool import resolve_state_dir

logger = logging.getLogger(__name__)

__all__: list[str] = []

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")


def resolve_ignore_dir() -> Path:
    """Resolve the permanent-ignore directory.

    Lives under the same state root as the spool, so it shares
    `STATE_VERSION`'s marker - no separate version kind needed for a new
    subdirectory under an already-versioned root.
    """
    return resolve_state_dir() / "ignored"


def _sanitize(value: str) -> str:
    return _SANITIZE_RE.sub("_", value)


@dataclass
class _Loaded:
    path: Path
    entry: IgnoredEntry


class IgnoreStore:
    def __init__(self, ignore_dir: Path) -> None:
        self._ignore_dir = ignore_dir
        self._ignore_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, _Loaded] = {}
        self._load_all()

    def _load_all(self) -> None:
        for path in sorted(self._ignore_dir.glob("*.json")):
            try:
                entry = IGNORED_ENTRY_ADAPTER.validate_json(path.read_text())
            except Exception:
                logger.warning(
                    "Failed to parse ignore-store file %s; leaving it in place",
                    path,
                    exc_info=True,
                )
                continue
            self._index[entry.subject_url] = _Loaded(path, entry)

    def _path_for(self, subject_url: str) -> Path:
        return self._ignore_dir / f"{_sanitize(subject_url)}.json"

    def is_ignored(self, subject_url: str) -> bool:
        return subject_url in self._index

    def add(self, subject_url: str, notification: GitHubNotification) -> None:
        entry = IgnoredEntry(
            subject_url=subject_url,
            added_at=datetime.now(UTC),
            title=notification.subject.title,
            repository=notification.repository.full_name,
            subject_type=notification.subject.type,
        )
        path = self._path_for(subject_url)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(IGNORED_ENTRY_ADAPTER.dump_json(entry, indent=2))
        tmp_path.replace(path)
        self._index[subject_url] = _Loaded(path, entry)

    def remove(self, subject_url: str) -> bool:
        loaded = self._index.pop(subject_url, None)
        if loaded is None:
            return False
        loaded.path.unlink(missing_ok=True)
        return True

    def clear(self) -> int:
        removed = len(self._index)
        for loaded in self._index.values():
            loaded.path.unlink(missing_ok=True)
        self._index.clear()
        return removed

    def entries(self) -> Iterator[tuple[Path, IgnoredEntry]]:
        for loaded in self._index.values():
            yield loaded.path, loaded.entry
