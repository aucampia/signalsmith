from pathlib import Path
from typing import Any

import pytest

from signalsmith.actions import RunStats, execute_actions
from signalsmith.config.models import (
    Config,
    DefaultAction,
    IgnoreActionConfig,
    MarkAsReadActionConfig,
    NotifyActionConfig,
    Rule,
    RuleAction,
)
from signalsmith.github.models import (
    GitHubIssue,
    GitHubNotification,
    GitHubPullRequest,
    GitHubRepository,
    GitHubSubject,
    GitHubUser,
)
from signalsmith.notification.models import NotificationOutcome
from signalsmith.processor import create_actions
from signalsmith.state.ignore_store import IgnoreStore
from signalsmith.state.spool import SpoolManager


class MockProvider:
    """Mock notification provider for testing."""

    name = "github"

    def __init__(
        self,
        notifications: list[GitHubNotification],
        subjects: dict[str, GitHubIssue | GitHubPullRequest] | None = None,
    ) -> None:
        self._notifications = notifications
        self._subjects = subjects or {}
        self.marked_as_read: list[str] = []
        self.subjects_fetched: list[tuple[str, str, str]] = []

    def get_notifications(
        self, limit: int | None = None, refresh: bool = False
    ) -> list[GitHubNotification]:
        """Return mock notifications."""
        result = self._notifications
        if limit is not None:
            result = result[:limit]
        return result

    def mark_as_read(self, notification_id: str) -> None:
        """Track which notifications were marked as read."""
        self.marked_as_read.append(notification_id)

    def get_authenticated_user(self) -> str:
        """Return a fixed mock login."""
        return "testuser"

    def get_subject(
        self, url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        """Return mock subject and track that it was fetched."""
        self.subjects_fetched.append((url, type, updated_at))
        if url not in self._subjects:
            raise ValueError(f"Mock subject not found for URL: {url}")
        return self._subjects[url]


@pytest.fixture
def sample_notification() -> GitHubNotification:
    """Create a sample notification for testing."""
    return GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        last_read_at=None,
        subject=GitHubSubject(
            title="Test Issue",
            url="https://api.github.com/repos/owner/repo/issues/1",
            latest_comment_url="https://api.github.com/repos/owner/repo/issues/comments/1",
            type="Issue",
        ),
        repository=GitHubRepository(
            id=1,
            name="repo",
            full_name="owner/repo",
            private=False,
        ),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )


@pytest.fixture
def spool_manager(tmp_path: Path) -> SpoolManager:
    """Create a SpoolManager backed by a temporary directory."""
    return SpoolManager(tmp_path / "spool", tmp_path / "trash")


@pytest.fixture
def ignore_store(tmp_path: Path) -> IgnoreStore:
    """Create an IgnoreStore backed by a temporary directory."""
    return IgnoreStore(tmp_path / "ignored")


@pytest.mark.parametrize(
    ("default_action", "expected_output"),
    [
        (DefaultAction.NOTIFY, "Would send notification"),
        (DefaultAction.IGNORE, "Would ignore"),
    ],
)
def test_process_notifications_default_action(
    default_action: DefaultAction,
    expected_output: str,
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
    capsys: Any,
) -> None:
    """Test that unmatched notifications fall back to the configured default_action."""
    config = Config(
        default_action=default_action,
        rules=[
            Rule(
                id="pr_only",
                expression='notification.subject.type == "PullRequest"',
                action=RuleAction(
                    notify=NotifyActionConfig(title="PR", message="New PR")
                ),
            )
        ],
    )

    provider = MockProvider([sample_notification])

    # Dry run to check what happens
    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=True)

    captured = capsys.readouterr()
    assert expected_output in captured.out
    assert "__default__" in captured.out
    # Should NOT be marked as read
    assert "123" not in provider.marked_as_read


def test_process_notifications_sends_notification_for_match(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
    capsys: Any,
) -> None:
    """Test that matching notifications trigger the notify action."""
    config = Config(
        rules=[
            Rule(
                id="issue_mention",
                expression='notification.subject.type == "Issue" && notification.reason == "mention"',
                action=RuleAction(
                    notify=NotifyActionConfig(
                        title="Issue Mention", message="You were mentioned"
                    )
                ),
            )
        ]
    )

    provider = MockProvider([sample_notification])

    # Dry run so we don't actually send notifications
    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=True)

    captured = capsys.readouterr()
    assert "Would send notification" in captured.out
    assert "issue_mention" in captured.out


def test_process_notifications_respects_mark_as_read_action(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """Test that mark_as_read action works correctly."""
    config = Config(
        rules=[
            Rule(
                id="mark_issues",
                expression='notification.subject.type == "Issue"',
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            )
        ]
    )

    provider = MockProvider([sample_notification])

    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=False)

    # Notification should be marked as read
    assert "123" in provider.marked_as_read


def test_process_notifications_respects_limit(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """Test that limit parameter is passed to provider."""
    # Create 5 notifications
    notifications = [
        GitHubNotification(
            id=str(i),
            reason="mention",
            unread=True,
            updated_at="2026-06-17T00:00:00Z",
            last_read_at=None,
            subject=sample_notification.subject,
            repository=sample_notification.repository,
            url=f"https://api.github.com/notifications/threads/{i}",
            subscription_url=f"https://api.github.com/notifications/threads/{i}/subscription",
        )
        for i in range(5)
    ]

    config = Config(
        rules=[
            Rule(
                id="mark_all",
                expression="true",
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            )
        ]
    )

    provider = MockProvider(notifications)

    # Process with limit of 2
    actions = create_actions(
        config, provider, spool_manager, limit=2, ignore_store=ignore_store
    )
    execute_actions(actions, dry_run=False)

    # Only 2 should be processed
    assert len(provider.marked_as_read) == 2
    assert provider.marked_as_read == ["0", "1"]


def test_process_notifications_dry_run_doesnt_mark_as_read(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """Test that dry run doesn't actually mark notifications as read."""
    config = Config(
        rules=[
            Rule(
                id="mark_all",
                expression="true",
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            )
        ]
    )

    provider = MockProvider([sample_notification])

    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=True)

    # Nothing should be marked as read in dry run
    assert len(provider.marked_as_read) == 0


def test_process_notifications_populates_run_stats(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """Test that RunStats tallies found/notified/ignored/marked_as_read correctly."""
    notifications = [
        GitHubNotification(
            id=str(i),
            reason=reason,
            unread=True,
            updated_at="2026-06-17T00:00:00Z",
            last_read_at=None,
            subject=sample_notification.subject,
            repository=sample_notification.repository,
            url=f"https://api.github.com/notifications/threads/{i}",
            subscription_url=f"https://api.github.com/notifications/threads/{i}/subscription",
        )
        for i, reason in enumerate(["notify_me", "ignore_me", "mark_me", "unmatched"])
    ]

    config = Config(
        default_action=DefaultAction.IGNORE,
        rules=[
            Rule(
                id="notify_rule",
                expression='notification.reason == "notify_me"',
                action=RuleAction(
                    notify=NotifyActionConfig(title="Notify", message="Notify")
                ),
            ),
            Rule(
                id="ignore_rule",
                expression='notification.reason == "ignore_me"',
                action=RuleAction(ignore=IgnoreActionConfig()),
            ),
            Rule(
                id="mark_rule",
                expression='notification.reason == "mark_me"',
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            ),
        ],
    )

    provider = MockProvider(notifications)
    stats = RunStats()

    actions = create_actions(
        config, provider, spool_manager, stats=stats, ignore_store=ignore_store
    )
    execute_actions(actions, dry_run=True, stats=stats)

    assert stats.found == 4
    assert stats.notified == 1
    assert stats.marked_as_read == 1
    # "ignore_me" (explicit rule) + "unmatched" (default_action=ignore)
    assert stats.ignored == 2
    assert stats.skipped == 0
    # Verify all outcomes are accounted for
    assert sum(stats.outcomes.values()) == stats.found


def test_process_notifications_account_username_from_provider(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """Rules can reference account.github.username, sourced from the provider."""
    assert sample_notification.subject.url is not None
    config = Config(
        rules=[
            Rule(
                id="assigned_to_me",
                expression='notification.subject.type == "Issue"',
                subject_expression="subject.assignees.exists(a, a.login == account.github.username)",
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            )
        ]
    )

    provider = MockProvider(
        [sample_notification],
        subjects={
            sample_notification.subject.url: GitHubIssue(
                id=1,
                number=1,
                title="Test Issue",
                state="open",
                user=GitHubUser(login="someone-else", id=2, type="User"),
                assignees=[GitHubUser(login="testuser", id=1, type="User")],
                created_at="2026-06-17T00:00:00Z",
                updated_at="2026-06-17T00:00:00Z",
            )
        },
    )

    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=False)

    assert "123" in provider.marked_as_read


def test_process_notifications_config_variables_available_in_expression(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """Rules can reference variables.*, sourced from config.variables."""
    config = Config(
        variables={"spam_repos": ["owner/repo"]},
        rules=[
            Rule(
                id="spam_repo_mark_as_read",
                expression="notification.repository.full_name in variables.spam_repos",
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            )
        ],
    )

    provider = MockProvider([sample_notification])

    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=False)

    assert "123" in provider.marked_as_read


def test_process_notifications_ignore_action(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
    capsys: Any,
) -> None:
    """Test that ignore action doesn't mark as read or notify."""
    config = Config(
        rules=[
            Rule(
                id="ignore_issues",
                expression='notification.subject.type == "Issue"',
                action=RuleAction(ignore=IgnoreActionConfig()),
            )
        ]
    )

    provider = MockProvider([sample_notification])

    # Dry run
    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=True)

    captured = capsys.readouterr()
    # Should see ignore action
    assert "Would ignore" in captured.out
    assert "ignore_issues" in captured.out

    # Reset and run without dry_run
    provider.marked_as_read = []
    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=False)

    # Should NOT be marked as read
    assert "123" not in provider.marked_as_read


def test_stats_outcomes_sum_equals_found(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """Verify that sum of all outcomes always equals found, even with filtered notifications."""
    from signalsmith.config.models import Masks, OrgMasks

    # Create mix of scenarios: filtered, matched, default action, etc.
    notifications = [
        # Will be filtered by org mask
        GitHubNotification(
            id="1",
            reason="mention",
            unread=True,
            updated_at="2026-06-17T00:00:00Z",
            last_read_at=None,
            subject=sample_notification.subject,
            repository=GitHubRepository(
                id=999,
                name="filtered-repo",
                full_name="filtered-org/filtered-repo",
                private=False,
            ),
            url="https://api.github.com/notifications/threads/1",
            subscription_url="https://api.github.com/notifications/threads/1/subscription",
        ),
        # Already read
        GitHubNotification(
            id="2",
            reason="mention",
            unread=False,
            updated_at="2026-06-17T00:00:00Z",
            last_read_at="2026-06-17T01:00:00Z",
            subject=sample_notification.subject,
            repository=sample_notification.repository,
            url="https://api.github.com/notifications/threads/2",
            subscription_url="https://api.github.com/notifications/threads/2/subscription",
        ),
        # Matches a rule
        GitHubNotification(
            id="3",
            reason="matched",
            unread=True,
            updated_at="2026-06-17T00:00:00Z",
            last_read_at=None,
            subject=sample_notification.subject,
            repository=sample_notification.repository,
            url="https://api.github.com/notifications/threads/3",
            subscription_url="https://api.github.com/notifications/threads/3/subscription",
        ),
        # Falls to default action
        GitHubNotification(
            id="4",
            reason="unmatched",
            unread=True,
            updated_at="2026-06-17T00:00:00Z",
            last_read_at=None,
            subject=sample_notification.subject,
            repository=sample_notification.repository,
            url="https://api.github.com/notifications/threads/4",
            subscription_url="https://api.github.com/notifications/threads/4/subscription",
        ),
    ]

    config = Config(
        default_action=DefaultAction.IGNORE,
        masks=Masks(orgs=OrgMasks(include=["owner"])),
        rules=[
            Rule(
                id="match_rule",
                expression='notification.reason == "matched"',
                action=RuleAction(
                    notify=NotifyActionConfig(title="Match", message="Matched")
                ),
            ),
        ],
    )

    provider = MockProvider(notifications)
    stats = RunStats()

    actions = create_actions(
        config, provider, spool_manager, stats=stats, ignore_store=ignore_store
    )
    execute_actions(actions, dry_run=True, stats=stats)

    # Critical invariant: sum of outcomes == found
    assert sum(stats.outcomes.values()) == stats.found
    assert stats.found == 4


def test_permanently_ignored_subject_is_skipped_before_rule_matching(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """A subject in the permanent-ignore store is skipped regardless of rules,
    and never triggers a subject fetch - even for a rule with a
    subject_expression that would otherwise match."""
    assert sample_notification.subject.url is not None
    ignore_store.add(sample_notification.subject.url, sample_notification)

    config = Config(
        rules=[
            Rule(
                id="would_match",
                expression="true",
                subject_expression="true",
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            )
        ]
    )
    provider = MockProvider(
        [sample_notification],
        subjects={
            sample_notification.subject.url: GitHubIssue(
                id=1,
                number=1,
                title="Test Issue",
                state="open",
                user=GitHubUser(login="someone", id=1, type="User"),
                created_at="2026-06-17T00:00:00Z",
                updated_at="2026-06-17T00:00:00Z",
            )
        },
    )
    stats = RunStats()

    actions = create_actions(
        config, provider, spool_manager, stats=stats, ignore_store=ignore_store
    )
    execute_actions(actions, dry_run=False, stats=stats)

    assert stats.outcomes[NotificationOutcome.PERMANENTLY_IGNORED] == 1
    assert provider.subjects_fetched == []
    assert provider.marked_as_read == []


def test_non_ignored_subject_is_processed_normally(
    sample_notification: GitHubNotification,
    spool_manager: SpoolManager,
    ignore_store: IgnoreStore,
) -> None:
    """The permanent-ignore check works fine when nothing is ignored (the
    `run`-shaped call: no interactivity, just the always-active read check)."""
    config = Config(
        rules=[
            Rule(
                id="mark_all",
                expression="true",
                action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
            )
        ]
    )
    provider = MockProvider([sample_notification])

    actions = create_actions(config, provider, spool_manager, ignore_store=ignore_store)
    execute_actions(actions, dry_run=False)

    assert "123" in provider.marked_as_read
