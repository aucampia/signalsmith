import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from signalsmith.versioning import (
    SCHEMA_VERSION_ADAPTER,
    Compatibility,
    SchemaVersion,
    VersionError,
    check_file_version,
    ensure_store_version,
    read_marker,
    write_marker,
)

# ---------------------------------------------------------------------------
# SchemaVersion
# ---------------------------------------------------------------------------


def test_schema_version_parses_from_string() -> None:
    version = SCHEMA_VERSION_ADAPTER.validate_python("1.0")
    assert version.major == 1
    assert version.minor == 0


def test_schema_version_str_round_trips() -> None:
    assert str(SchemaVersion(major=1, minor=7)) == "1.7"


def test_schema_version_json_round_trips() -> None:
    version = SCHEMA_VERSION_ADAPTER.validate_python("2.3")
    assert SCHEMA_VERSION_ADAPTER.dump_json(version) == b'"2.3"'


@pytest.mark.parametrize("text", ["1", "1.0.0", "x.y", "", "1.", ".0", "1.-1"])
def test_schema_version_rejects_malformed_input(text: str) -> None:
    with pytest.raises(ValidationError):
        SCHEMA_VERSION_ADAPTER.validate_python(text)


def test_schema_version_orders_by_major_then_minor() -> None:
    assert SchemaVersion(major=1, minor=0) < SchemaVersion(major=1, minor=1)
    assert SchemaVersion(major=1, minor=9) < SchemaVersion(major=2, minor=0)
    assert not (SchemaVersion(major=1, minor=1) < SchemaVersion(major=1, minor=1))


# ---------------------------------------------------------------------------
# SchemaVersion.compatibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("found", "expected", "result"),
    [
        ("1.0", "1.0", Compatibility.OK),
        ("1.5", "1.0", Compatibility.NEWER_MINOR),
        ("1.0", "1.5", Compatibility.OK),
        ("0.0", "1.0", Compatibility.INCOMPATIBLE),
        ("2.0", "1.0", Compatibility.INCOMPATIBLE),
    ],
)
def test_compatibility_matrix(found: str, expected: str, result: Compatibility) -> None:
    assert (
        SCHEMA_VERSION_ADAPTER.validate_python(found).compatibility(
            SCHEMA_VERSION_ADAPTER.validate_python(expected)
        )
        == result
    )


# ---------------------------------------------------------------------------
# check_file_version
# ---------------------------------------------------------------------------


def test_check_file_version_missing_key_is_incompatible() -> None:
    with pytest.raises(VersionError):
        check_file_version(
            {}, SchemaVersion(major=1, minor=0), label="Config", remedy="update it"
        )


def test_check_file_version_matching_ok() -> None:
    check_file_version(
        {"version": "1.0"},
        SchemaVersion(major=1, minor=0),
        label="Config",
        remedy="update it",
    )


def test_check_file_version_newer_minor_warns_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    check_file_version(
        {"version": "1.5"},
        SchemaVersion(major=1, minor=0),
        label="Config",
        remedy="update it",
    )
    assert any("newer" in record.message for record in caplog.records)


def test_check_file_version_incompatible_major_raises() -> None:
    with pytest.raises(VersionError) as exc_info:
        check_file_version(
            {"version": "2.0"},
            SchemaVersion(major=1, minor=0),
            label="Config",
            remedy="update it by hand",
        )
    assert "update it by hand" in str(exc_info.value)


# ---------------------------------------------------------------------------
# marker read/write
# ---------------------------------------------------------------------------


def test_write_then_read_marker_round_trips(tmp_path: Path) -> None:
    version = SchemaVersion(major=1, minor=0)
    write_marker(tmp_path, version)
    assert read_marker(tmp_path) == version


def test_read_marker_missing_returns_none(tmp_path: Path) -> None:
    assert read_marker(tmp_path) is None


def test_read_marker_corrupt_returns_none(tmp_path: Path) -> None:
    (tmp_path / "version.json").write_text("not json")
    assert read_marker(tmp_path) is None


# ---------------------------------------------------------------------------
# ensure_store_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pre_create", [False, True], ids=["absent-dir", "empty-dir"])
def test_ensure_store_version_writes_marker_for_absent_or_empty_dir(
    tmp_path: Path, pre_create: bool
) -> None:
    store_dir = tmp_path / "store"
    if pre_create:
        store_dir.mkdir()
    ensure_store_version(
        store_dir,
        SchemaVersion(major=1, minor=0),
        label="Cache",
        clean_command="signalsmith cache clean",
    )
    assert read_marker(store_dir) == SchemaVersion(major=1, minor=0)


@pytest.mark.parametrize(
    "write_corrupt_marker", [False, True], ids=["missing-marker", "corrupt-marker"]
)
def test_ensure_store_version_raises_when_marker_missing_or_corrupt(
    tmp_path: Path, write_corrupt_marker: bool
) -> None:
    (tmp_path / "notifications.json").write_text("{}")
    if write_corrupt_marker:
        (tmp_path / "version.json").write_text("not json")
    with pytest.raises(VersionError):
        ensure_store_version(
            tmp_path,
            SchemaVersion(major=1, minor=0),
            label="Cache",
            clean_command="signalsmith cache clean",
        )


@pytest.mark.parametrize(
    ("marker_minor", "expect_warning"),
    [(0, False), (5, True)],
    ids=["matching", "newer-minor"],
)
def test_ensure_store_version_compatible_marker_passes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    marker_minor: int,
    expect_warning: bool,
) -> None:
    (tmp_path / "notifications.json").write_text("{}")
    write_marker(tmp_path, SchemaVersion(major=1, minor=marker_minor))
    with caplog.at_level(logging.WARNING):
        ensure_store_version(
            tmp_path,
            SchemaVersion(major=1, minor=0),
            label="Cache",
            clean_command="signalsmith cache clean",
        )
    assert any("newer" in record.message for record in caplog.records) == expect_warning


def test_ensure_store_version_incompatible_major_raises_with_remedy(
    tmp_path: Path,
) -> None:
    (tmp_path / "notifications.json").write_text("{}")
    write_marker(tmp_path, SchemaVersion(major=2, minor=0))
    with pytest.raises(VersionError) as exc_info:
        ensure_store_version(
            tmp_path,
            SchemaVersion(major=1, minor=0),
            label="Cache",
            clean_command="signalsmith cache clean",
        )
    assert "signalsmith cache clean" in str(exc_info.value)
