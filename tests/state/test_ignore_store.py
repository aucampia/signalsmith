from pathlib import Path

import pytest

from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.state.ignore_store import IgnoreStore, resolve_ignore_dir


@pytest.fixture
def notification() -> GitHubNotification:
    return GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test Issue",
            url="https://api.github.com/repos/owner/repo/issues/1",
            type="Issue",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )


def test_is_ignored_false_when_empty(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "ignored")
    assert store.is_ignored("https://api.github.com/repos/owner/repo/issues/1") is False


def test_add_makes_is_ignored_true(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = IgnoreStore(tmp_path / "ignored")
    assert notification.subject.url is not None

    store.add(notification.subject.url, notification)

    assert store.is_ignored(notification.subject.url) is True


def test_add_writes_file_to_disk(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    ignore_dir = tmp_path / "ignored"
    store = IgnoreStore(ignore_dir)
    assert notification.subject.url is not None

    store.add(notification.subject.url, notification)

    assert list(ignore_dir.glob("*.json"))


def test_add_populates_entry_fields(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = IgnoreStore(tmp_path / "ignored")
    assert notification.subject.url is not None

    store.add(notification.subject.url, notification)

    ((_, entry),) = list(store.entries())
    assert entry.subject_url == notification.subject.url
    assert entry.title == "Test Issue"
    assert entry.repository == "owner/repo"
    assert entry.subject_type == "Issue"


def test_reload_from_disk_preserves_entries(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    ignore_dir = tmp_path / "ignored"
    assert notification.subject.url is not None
    first = IgnoreStore(ignore_dir)
    first.add(notification.subject.url, notification)

    second = IgnoreStore(ignore_dir)
    assert second.is_ignored(notification.subject.url) is True


def test_remove_existing_returns_true_and_unignores(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = IgnoreStore(tmp_path / "ignored")
    assert notification.subject.url is not None
    store.add(notification.subject.url, notification)

    removed = store.remove(notification.subject.url)

    assert removed is True
    assert store.is_ignored(notification.subject.url) is False


def test_remove_missing_returns_false(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "ignored")
    assert store.remove("https://api.github.com/repos/owner/repo/issues/999") is False


def test_clear_removes_all_entries(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = IgnoreStore(tmp_path / "ignored")
    assert notification.subject.url is not None
    store.add(notification.subject.url, notification)

    removed = store.clear()

    assert removed == 1
    assert list(store.entries()) == []


def test_corrupt_ignore_file_is_skipped_and_left_in_place(tmp_path: Path) -> None:
    ignore_dir = tmp_path / "ignored"
    ignore_dir.mkdir()
    bad_file = ignore_dir / "bad.json"
    bad_file.write_text("not valid json")

    store = IgnoreStore(ignore_dir)

    assert list(store.entries()) == []
    assert bad_file.exists()


def test_resolve_ignore_dir_is_under_state_dir() -> None:
    from signalsmith.state.spool import resolve_state_dir

    assert resolve_ignore_dir() == resolve_state_dir() / "ignored"
