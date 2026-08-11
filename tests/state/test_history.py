from pathlib import Path

import pytest

from signalsmith.github.models import (
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
)
from signalsmith.notification.models import NotificationOutcome
from signalsmith.state.history import HistoryStore


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


def test_record_writes_entry(tmp_path: Path, notification: GitHubNotification) -> None:
    store = HistoryStore(tmp_path / "history")

    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="some_rule",
        rendered_title="Rendered Title",
        rendered_body="Rendered Body",
    )

    entries = list(store.entries())
    assert len(entries) == 1
    _, entry = entries[0]
    assert entry.provider == "github"
    assert entry.notification_id == "123"
    assert entry.outcome == NotificationOutcome.NOTIFIED
    assert entry.rule_id == "some_rule"
    assert entry.rendered_title == "Rendered Title"
    assert entry.rendered_body == "Rendered Body"
    assert entry.notification.id == "123"
    assert entry.subject is None
    assert entry.subject_type is None


def test_record_filtered_outcome(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = HistoryStore(tmp_path / "history")

    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.FILTERED_ORG,
        rule_id="",
    )

    entries = list(store.entries())
    assert len(entries) == 1
    _, entry = entries[0]
    assert entry.outcome == NotificationOutcome.FILTERED_ORG
    assert entry.rule_id == ""
    assert entry.rendered_title is None
    assert entry.rendered_body is None
    assert entry.subject is None


def test_re_record_preserves_rendered_fields_when_overwriting_without(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    """Rendered fields are carried forward when overwriting without them."""
    store = HistoryStore(tmp_path / "history")

    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="first_rule",
        rendered_title="Hello",
        rendered_body="World",
    )
    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.SKIPPED,
        rule_id="second_rule",
    )

    entries = list(store.entries())
    assert len(entries) == 1
    _, entry = entries[0]
    assert entry.outcome == NotificationOutcome.SKIPPED
    assert entry.rule_id == "second_rule"
    assert entry.rendered_title == "Hello"
    assert entry.rendered_body == "World"


def test_re_record_overwrites_rendered_fields_when_provided(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    """New rendered fields replace old ones when provided."""
    store = HistoryStore(tmp_path / "history")

    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="first_rule",
        rendered_title="Old Title",
        rendered_body="Old Body",
    )
    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="second_rule",
        rendered_title="New Title",
        rendered_body=None,
    )

    entries = list(store.entries())
    assert len(entries) == 1
    _, entry = entries[0]
    assert entry.rendered_title == "New Title"
    assert entry.rendered_body == "Old Body"


def test_re_record_preserves_subject_when_overwriting_without(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    """Subject info is carried forward when overwriting without them."""
    from signalsmith.github.models import GitHubIssue

    store = HistoryStore(tmp_path / "history")

    subject = GitHubIssue(
        id=1,
        number=1,
        title="Test",
        state="open",
        user={"login": "testuser", "id": 1, "type": "User"},  # type: ignore[arg-type]
        labels=[],
        assignees=[],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="rule",
        rendered_title="Title",
        rendered_body="Body",
        subject=subject,
        subject_type="Issue",
    )
    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.SKIPPED,
        rule_id="rule",
    )

    _, entry = next(iter(store.entries()))
    assert entry.rendered_title == "Title"
    assert entry.rendered_body == "Body"
    assert entry.subject is not None
    assert entry.subject["title"] == "Test"
    assert entry.subject_type == "Issue"


def test_re_record_overwrites_same_notification_id(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = HistoryStore(tmp_path / "history")

    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="first_rule",
    )
    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.SKIPPED,
        rule_id="second_rule",
    )

    entries = list(store.entries())
    assert len(entries) == 1
    _, entry = entries[0]
    assert entry.outcome == NotificationOutcome.SKIPPED
    assert entry.rule_id == "second_rule"


def test_entries_sorted_by_recorded_at_desc(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = HistoryStore(tmp_path / "history")

    notif1 = GitHubNotification(
        id="1",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="One", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/1",
        subscription_url="https://api.github.com/notifications/threads/1/subscription",
    )
    notif2 = GitHubNotification(
        id="2",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Two", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/2",
        subscription_url="https://api.github.com/notifications/threads/2/subscription",
    )

    store.record(
        provider="github",
        notification=notif1,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="r1",
    )
    store.record(
        provider="github",
        notification=notif2,
        outcome=NotificationOutcome.IGNORED,
        rule_id="r2",
    )

    entries = list(store.entries())
    assert len(entries) == 2
    assert entries[0][1].notification_id == "2"  # most recent first
    assert entries[1][1].notification_id == "1"


def test_entries_respects_limit(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = HistoryStore(tmp_path / "history")
    for i in range(5):
        notif = GitHubNotification(
            id=str(i),
            reason="mention",
            unread=True,
            updated_at="2026-06-17T00:00:00Z",
            subject=GitHubSubject(title=f"Item {i}", type="Issue"),
            repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
            url=f"https://api.github.com/notifications/threads/{i}",
            subscription_url=f"https://api.github.com/notifications/threads/{i}/subscription",
        )
        store.record(
            provider="github",
            notification=notif,
            outcome=NotificationOutcome.IGNORED,
            rule_id=f"r{i}",
        )

    entries = list(store.entries(limit=3))
    assert len(entries) == 3


def test_entries_filters_by_action(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = HistoryStore(tmp_path / "history")

    notif1 = GitHubNotification(
        id="1",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="One", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/1",
        subscription_url="https://api.github.com/notifications/threads/1/subscription",
    )
    notif2 = GitHubNotification(
        id="2",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Two", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/2",
        subscription_url="https://api.github.com/notifications/threads/2/subscription",
    )

    store.record(
        provider="github",
        notification=notif1,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="r1",
    )
    store.record(
        provider="github",
        notification=notif2,
        outcome=NotificationOutcome.IGNORED,
        rule_id="r2",
    )

    notified = list(store.entries(action="notified"))
    assert len(notified) == 1
    assert notified[0][1].outcome == NotificationOutcome.NOTIFIED

    ignored = list(store.entries(action="ignored"))
    assert len(ignored) == 1
    assert ignored[0][1].outcome == NotificationOutcome.IGNORED

    all_entries = list(store.entries())
    assert len(all_entries) == 2


def test_reload_from_disk_preserves_entries(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    history_dir = tmp_path / "history"
    first = HistoryStore(history_dir)
    first.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="test_rule",
    )

    second = HistoryStore(history_dir)
    entries = list(second.entries())
    assert len(entries) == 1
    _, entry = entries[0]
    assert entry.outcome == NotificationOutcome.NOTIFIED
    assert entry.rule_id == "test_rule"


def test_corrupt_history_file_is_skipped_and_left_in_place(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    bad_file = history_dir / "bad.json"
    bad_file.write_text("not valid json")

    store = HistoryStore(history_dir)

    assert list(store.entries()) == []
    assert bad_file.exists()


def test_clear_removes_all_entries(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = HistoryStore(tmp_path / "history")
    store.record(
        provider="github",
        notification=notification,
        outcome=NotificationOutcome.NOTIFIED,
        rule_id="r",
    )

    removed = store.clear()

    assert removed == 1
    assert list(store.entries()) == []


def test_resolve_history_dir_is_under_state_dir() -> None:
    from signalsmith.state.spool import SpoolManager

    assert HistoryStore.resolve_dir() == SpoolManager.resolve_state_dir() / "history"


def test_entries_default_no_limit_returns_all(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    store = HistoryStore(tmp_path / "history")
    for i in range(25):
        notif = GitHubNotification(
            id=str(i),
            reason="mention",
            unread=True,
            updated_at="2026-06-17T00:00:00Z",
            subject=GitHubSubject(title=f"Item {i}", type="Issue"),
            repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
            url=f"https://api.github.com/notifications/threads/{i}",
            subscription_url=f"https://api.github.com/notifications/threads/{i}/subscription",
        )
        store.record(
            provider="github",
            notification=notif,
            outcome=NotificationOutcome.IGNORED,
            rule_id=f"r{i}",
        )

    entries = list(store.entries())
    assert len(entries) == 25
