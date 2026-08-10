import logging
from pathlib import Path

import pytest

from signalsmith.config.models import IgnoreActionConfig, Rule, RuleAction
from signalsmith.github.models import (
    GitHubIssue,
    GitHubNotification,
    GitHubRepository,
    GitHubSubject,
    GitHubUser,
)
from signalsmith.state.spool import SpoolManager, ensure_state_version
from signalsmith.versioning import (
    SchemaVersion,
    VersionError,
    read_marker,
    write_marker,
)


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


@pytest.fixture
def subject() -> GitHubIssue:
    return GitHubIssue(
        id=1,
        number=1,
        title="Test Issue",
        state="open",
        user=GitHubUser(login="someone", id=1, type="User"),
        created_at="2026-06-17T00:00:00Z",
        updated_at="2026-06-17T00:00:00Z",
    )


@pytest.fixture
def rule() -> Rule:
    return Rule(
        id="my_rule",
        expression="true",
        action=RuleAction(ignore=IgnoreActionConfig()),
    )


def make_spool(tmp_path: Path) -> SpoolManager:
    return SpoolManager(tmp_path / "spool", tmp_path / "trash")


def test_record_notify_writes_provider_id_filename(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool_dir = tmp_path / "spool"
    spool = SpoolManager(spool_dir, tmp_path / "trash")

    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    assert (spool_dir / "github-123.json").exists()


def test_record_notify_populates_entry_fields(
    tmp_path: Path, notification: GitHubNotification, rule: Rule
) -> None:
    spool = make_spool(tmp_path)

    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=rule,
        rule_id=rule.id,
        title="My Title",
        message="My Message",
    )

    ((_, entry),) = list(spool.entries())
    assert entry.provider == "github"
    assert entry.notification_id == "123"
    assert entry.rule_id == "my_rule"
    assert entry.rule is not None and entry.rule.id == "my_rule"
    assert entry.title == "My Title"
    assert entry.message == "My Message"
    assert entry.subject is None
    assert entry.notification.id == "123"
    assert entry.received_at == entry.last_notified_at
    assert entry.notify_count == 1


def test_record_notify_with_subject(
    tmp_path: Path, notification: GitHubNotification, subject: GitHubIssue
) -> None:
    spool = make_spool(tmp_path)

    spool.record_notify(
        provider="github",
        notification=notification,
        subject=subject,
        subject_type="Issue",
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    ((_, entry),) = list(spool.entries())
    assert entry.subject_type == "Issue"
    assert entry.subject is not None
    assert entry.subject["title"] == "Test Issue"


def test_second_notify_preserves_received_at_and_bumps_count(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool = make_spool(tmp_path)

    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title 1",
        message="Message 1",
    )
    ((_, first),) = list(spool.entries())

    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title 2",
        message="Message 2",
    )
    ((_, second),) = list(spool.entries())

    assert second.received_at == first.received_at
    assert second.notify_count == 2
    assert len(second.notify_events) == 2
    assert second.title == "Title 2"


def test_notify_events_capped_but_count_keeps_climbing(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool = make_spool(tmp_path)

    for i in range(25):
        spool.record_notify(
            provider="github",
            notification=notification,
            subject=None,
            subject_type=None,
            rule=None,
            rule_id="__default__",
            title=f"Title {i}",
            message=f"Message {i}",
        )

    ((_, entry),) = list(spool.entries())
    assert entry.notify_count == 25
    assert len(entry.notify_events) == 20
    assert entry.notify_events[-1].title == "Title 24"


def test_should_notify_true_when_no_entry(tmp_path: Path) -> None:
    spool = make_spool(tmp_path)
    assert spool.should_notify("github", "123", 3600) is True


def test_should_notify_false_within_renotify_interval(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool = make_spool(tmp_path)
    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    assert spool.should_notify("github", "123", 3600) is False


def test_should_notify_true_when_interval_elapsed(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool = make_spool(tmp_path)
    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    # renotify_interval of 0 seconds: always due immediately
    assert spool.should_notify("github", "123", 0) is True


def test_reload_from_disk_preserves_suppression(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool_dir = tmp_path / "spool"
    trash_dir = tmp_path / "trash"
    first = SpoolManager(spool_dir, trash_dir)
    first.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    second = SpoolManager(spool_dir, trash_dir)
    assert second.should_notify("github", "123", 3600) is False


def test_reap_moves_absent_entries_to_trash(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool_dir = tmp_path / "spool"
    trash_dir = tmp_path / "trash"
    spool = SpoolManager(spool_dir, trash_dir)
    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    removed = spool.reap("github", live_ids=set())

    assert removed == 1
    assert not (spool_dir / "github-123.json").exists()
    assert list(spool.entries()) == []
    trashed = list(trash_dir.glob("github-123-*.json"))
    assert len(trashed) == 1
    assert '"notification_id": "123"' in trashed[0].read_text()


def test_reap_keeps_live_entries(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool = make_spool(tmp_path)
    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    removed = spool.reap("github", live_ids={"123"})

    assert removed == 0
    assert len(list(spool.entries())) == 1


def test_reap_ignores_other_providers(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool = make_spool(tmp_path)
    spool.record_notify(
        provider="jira",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    removed = spool.reap("github", live_ids=set())

    assert removed == 0
    assert len(list(spool.entries())) == 1


def test_repeated_reap_of_same_id_does_not_collide_in_trash(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool_dir = tmp_path / "spool"
    trash_dir = tmp_path / "trash"
    spool = SpoolManager(spool_dir, trash_dir)

    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="First",
        message="Message",
    )
    spool.reap("github", live_ids=set())

    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Second",
        message="Message",
    )
    spool.reap("github", live_ids=set())

    trashed = sorted(trash_dir.glob("github-123-*.json"))
    assert len(trashed) == 2
    assert "First" in trashed[0].read_text()
    assert "Second" in trashed[1].read_text()


def test_clear_moves_everything_to_trash(
    tmp_path: Path, notification: GitHubNotification
) -> None:
    spool_dir = tmp_path / "spool"
    trash_dir = tmp_path / "trash"
    spool = SpoolManager(spool_dir, trash_dir)
    spool.record_notify(
        provider="github",
        notification=notification,
        subject=None,
        subject_type=None,
        rule=None,
        rule_id="__default__",
        title="Title",
        message="Message",
    )

    removed = spool.clear()

    assert removed == 1
    assert list(spool.entries()) == []
    assert list(spool_dir.glob("*.json")) == []
    assert len(list(trash_dir.glob("*.json"))) == 1


def test_corrupt_spool_file_is_skipped_and_left_in_place(tmp_path: Path) -> None:
    spool_dir = tmp_path / "spool"
    spool_dir.mkdir()
    bad_file = spool_dir / "github-999.json"
    bad_file.write_text("not valid json")

    spool = SpoolManager(spool_dir, tmp_path / "trash")

    assert list(spool.entries()) == []
    assert bad_file.exists()

    # A corrupt file must not block reap/clear from working on good entries.
    removed = spool.reap("github", live_ids=set())
    assert removed == 0
    assert bad_file.exists()


# ---------------------------------------------------------------------------
# State version marker
# ---------------------------------------------------------------------------


def test_ensure_state_version_fresh_dir_writes_marker(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    ensure_state_version(state_dir)
    assert read_marker(state_dir) == SchemaVersion(major=1, minor=0)


def test_ensure_state_version_populated_dir_without_marker_raises(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    spool_dir = state_dir / "spool"
    spool_dir.mkdir(parents=True)
    (spool_dir / "github-1.json").write_text("{}")
    with pytest.raises(VersionError) as exc_info:
        ensure_state_version(state_dir)
    assert "signalsmith state clean" in str(exc_info.value)


@pytest.mark.parametrize(
    ("marker_minor", "expect_warning"),
    [(0, False), (5, True)],
    ids=["matching", "newer-minor"],
)
def test_ensure_state_version_compatible_marker_passes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    marker_minor: int,
    expect_warning: bool,
) -> None:
    state_dir = tmp_path / "state"
    spool_dir = state_dir / "spool"
    spool_dir.mkdir(parents=True)
    (spool_dir / "github-1.json").write_text("{}")
    write_marker(state_dir, SchemaVersion(major=1, minor=marker_minor))
    with caplog.at_level(logging.WARNING):
        ensure_state_version(state_dir)
    assert any("newer" in record.message for record in caplog.records) == expect_warning
