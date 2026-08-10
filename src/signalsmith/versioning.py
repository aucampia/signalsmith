"""Schema versioning for the config, test, state, and cache files.

Each of those four data kinds carries its own independent MAJOR.MINOR
version (unrelated to the package version in pyproject.toml). Major is the
compatibility boundary: any major mismatch is incompatible in either
direction; a newer minor with the same major is compatible but warned
about. See AGENTS.md for the bump policy and per-kind remedies.

No migration code lives here or anywhere else: an incompatible version is
either refused (config/test files) or cleared by the user via a `clean`
command (state/cache) - never repaired in place.
"""

from __future__ import annotations

import functools
import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field, TypeAdapter, model_serializer, model_validator
from pydantic.dataclasses import dataclass

from .errors import SignalsmithError

logger = logging.getLogger(__name__)

__all__: list[str] = []

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


class Compatibility(StrEnum):
    OK = "ok"
    NEWER_MINOR = "newer_minor"
    INCOMPATIBLE = "incompatible"


@functools.total_ordering
@dataclass(config=ConfigDict(frozen=True), kw_only=True)
class SchemaVersion:
    """A MAJOR.MINOR schema version, serialized as e.g. "1.0"."""

    major: int = Field(ge=0)
    minor: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            match = _VERSION_RE.match(value)
            if not match:
                raise ValueError(
                    f"Invalid schema version {value!r}; expected MAJOR.MINOR (e.g. '1.0')"
                )
            return {"major": int(match.group(1)), "minor": int(match.group(2))}
        return value

    @model_serializer
    def _serialize(self) -> str:
        return str(self)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    def __lt__(self, other: SchemaVersion) -> bool:
        return (self.major, self.minor) < (other.major, other.minor)

    def compatibility(self, expected: SchemaVersion) -> Compatibility:
        """Classify self (the found version) against expected (this signalsmith version)."""
        if self.major != expected.major:
            return Compatibility.INCOMPATIBLE
        if self.minor > expected.minor:
            return Compatibility.NEWER_MINOR
        return Compatibility.OK


SCHEMA_VERSION_ADAPTER: TypeAdapter[SchemaVersion] = TypeAdapter(SchemaVersion)

CONFIG_VERSION = SchemaVersion(major=1, minor=1)
TEST_VERSION = SchemaVersion(major=1, minor=0)
STATE_VERSION = SchemaVersion(major=1, minor=0)
CACHE_VERSION = SchemaVersion(major=2, minor=0)

MISSING_VERSION = SchemaVersion(major=0, minor=0)


class VersionError(SignalsmithError):
    """An on-disk/config schema version is incompatible with this signalsmith version."""

    def __init__(
        self,
        *,
        label: str,
        found: SchemaVersion,
        expected: SchemaVersion,
        remedy: str,
    ) -> None:
        self.label = label
        self.found = found
        self.expected = expected
        self.remedy = remedy
        super().__init__(
            f"{label} version {found} is incompatible with the version this "
            f"signalsmith expects ({expected}). {remedy}"
        )


def check_file_version(
    raw: dict[str, Any],
    expected: SchemaVersion,
    *,
    label: str,
    remedy: str,
) -> None:
    """Check a parsed config/test-file's `version` key against `expected`.

    Raises VersionError on a major mismatch; logs and continues on a newer
    minor; a missing `version` key is treated as version 0.0.
    """
    raw_version = raw.get("version")
    found = (
        MISSING_VERSION
        if raw_version is None
        else SCHEMA_VERSION_ADAPTER.validate_python(raw_version)
    )
    match found.compatibility(expected):
        case Compatibility.INCOMPATIBLE:
            raise VersionError(
                label=label, found=found, expected=expected, remedy=remedy
            )
        case Compatibility.NEWER_MINOR:
            logger.warning(
                "%s version %s is newer than this signalsmith version supports (%s); "
                "continuing, but some newer fields may be ignored",
                label,
                found,
                expected,
            )
        case Compatibility.OK:
            pass


# ---------------------------------------------------------------------------
# Directory version markers (state, cache)
# ---------------------------------------------------------------------------

_MARKER_FILENAME = "version.json"


@dataclass(kw_only=True)
class VersionMarker:
    version: SchemaVersion


VERSION_MARKER_ADAPTER: TypeAdapter[VersionMarker] = TypeAdapter(VersionMarker)


def _marker_path(directory: Path) -> Path:
    return directory / _MARKER_FILENAME


def read_marker(directory: Path) -> SchemaVersion | None:
    """Read a directory's version marker; None if absent or unparsable."""
    marker_path = _marker_path(directory)
    if not marker_path.exists():
        return None
    try:
        return VERSION_MARKER_ADAPTER.validate_json(marker_path.read_text()).version
    except Exception:
        logger.warning(
            "Failed to parse version marker %s; treating as missing",
            marker_path,
            exc_info=True,
        )
        return None


def write_marker(directory: Path, version: SchemaVersion) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    marker = VersionMarker(version=version)
    _marker_path(directory).write_bytes(
        VERSION_MARKER_ADAPTER.dump_json(marker, indent=2)
    )


def _is_empty(directory: Path) -> bool:
    return not any(path.is_file() for path in directory.rglob("*"))


def ensure_store_version(
    directory: Path,
    expected: SchemaVersion,
    *,
    label: str,
    clean_command: str,
) -> None:
    """Ensure a state/cache directory's version marker is compatible.

    A missing or empty directory is a first run: the marker is written and
    no check is performed. Otherwise an incompatible version raises
    VersionError telling the user to run `clean_command`; a newer minor
    warns and continues.
    """
    if not directory.exists() or _is_empty(directory):
        write_marker(directory, expected)
        return

    found = read_marker(directory)
    if found is None:
        found = MISSING_VERSION

    match found.compatibility(expected):
        case Compatibility.INCOMPATIBLE:
            raise VersionError(
                label=label,
                found=found,
                expected=expected,
                remedy=f"Run `{clean_command}` and re-run.",
            )
        case Compatibility.NEWER_MINOR:
            logger.warning(
                "%s directory %s has version %s, newer than this signalsmith version "
                "supports (%s); continuing, but writing to it may lose data "
                "added by the newer version",
                label,
                directory,
                found,
                expected,
            )
        case Compatibility.OK:
            pass
