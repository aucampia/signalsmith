"""The local notification/subject cache directory.

Owned separately from `state/` (the spool and permanent-ignore store):
everything under here - the notification-list cache, per-subject cache,
notification archive, and spool trash (`state.spool.SpoolManager.resolve_trash_dir`)
- is disposable. `clean_cache` just removes the whole tree; unlike `state
clean`, there's no version-marker recovery story, since a missing cache
directory is always just treated as empty (see `github.client.GitHubClient`
and `versioning.ensure_store_version`).
"""

import shutil
from pathlib import Path

from xdg import xdg_cache_home

__all__ = ["clean_cache", "resolve_cache_dir"]


def resolve_cache_dir() -> Path:
    """Resolve the cache root directory (not configurable, unlike spool.dir)."""
    return xdg_cache_home() / "signalsmith"


def clean_cache(cache_dir: Path | None = None) -> bool:
    """Remove the cache directory. Returns False if it didn't exist."""
    cache_dir = cache_dir if cache_dir is not None else resolve_cache_dir()
    if not cache_dir.exists():
        return False
    shutil.rmtree(cache_dir)
    return True
