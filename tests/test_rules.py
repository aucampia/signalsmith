import pytest

from signalsmith.config.models import (
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
from signalsmith.rules import RuleMatcher


def test_rule_matcher_matches_issue_mention() -> None:
    rules = [
        Rule(
            id="issue_mention",
            expression='notification.subject.type == "Issue" and notification.reason == "mention"',
            action=RuleAction(
                notify=NotifyActionConfig(
                    title="Issue Mention",
                    body="You were mentioned",
                )
            ),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test Issue", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )

    matched = matcher.find_matching_rule(notification)
    assert matched is not None
    assert matched.id == "issue_mention"


def test_rule_matcher_no_match() -> None:
    rules = [
        Rule(
            id="issue_mention",
            expression='notification.subject.type == "Issue" and notification.reason == "mention"',
            action=RuleAction(
                notify=NotifyActionConfig(
                    title="Issue Mention",
                    body="You were mentioned",
                )
            ),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="123",
        reason="assign",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test PR", type="PullRequest"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )

    matched = matcher.find_matching_rule(notification)
    assert matched is None


def test_rule_matcher_first_match_wins() -> None:
    rules = [
        Rule(
            id="first",
            expression='notification.reason == "mention"',
            action=RuleAction(
                notify=NotifyActionConfig(
                    title="First",
                    body="First match",
                )
            ),
        ),
        Rule(
            id="second",
            expression='notification.reason == "mention"',
            action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )

    matched = matcher.find_matching_rule(notification)
    assert matched is not None
    assert matched.id == "first"


def test_two_stage_filtering_with_subject_match() -> None:
    """Test that expression with subject access matches."""
    rules = [
        Rule(
            id="pr_assigned_to_me",
            expression='notification.subject.type == "PullRequest" and ("testuser" in subject.assignees|map(attribute="login"))',
            action=RuleAction(
                notify=NotifyActionConfig(title="PR Assigned", body="You were assigned")
            ),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="456",
        reason="assign",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test PR",
            type="PullRequest",
            url="https://api.github.com/repos/owner/repo/pulls/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/456",
        subscription_url="https://api.github.com/notifications/threads/456/subscription",
    )

    # Mock subject fetcher
    mock_pr = GitHubPullRequest(
        id=1,
        number=1,
        title="Test PR",
        state="open",
        user=GitHubUser(login="otheruser", id=2, type="User"),
        assignees=[GitHubUser(login="testuser", id=1, type="User")],
        created_at="2026-06-17T00:00:00Z",
        updated_at="2026-06-17T00:00:00Z",
    )

    def fetch_subject(
        url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        assert url == "https://api.github.com/repos/owner/repo/pulls/1"
        assert type == "PullRequest"
        return mock_pr

    matched = matcher.find_matching_rule(notification, fetch_subject)
    assert matched is not None
    assert matched.id == "pr_assigned_to_me"


def test_two_stage_filtering_subject_no_match() -> None:
    """Test that rule doesn't match if subject check fails."""
    rules = [
        Rule(
            id="pr_assigned_to_me",
            expression='notification.subject.type == "PullRequest" and ("testuser" in subject.assignees|map(attribute="login"))',
            action=RuleAction(
                notify=NotifyActionConfig(title="PR Assigned", body="You were assigned")
            ),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="456",
        reason="assign",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test PR",
            type="PullRequest",
            url="https://api.github.com/repos/owner/repo/pulls/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/456",
        subscription_url="https://api.github.com/notifications/threads/456/subscription",
    )

    # Mock subject fetcher - PR assigned to someone else
    mock_pr = GitHubPullRequest(
        id=1,
        number=1,
        title="Test PR",
        state="open",
        user=GitHubUser(login="otheruser", id=2, type="User"),
        assignees=[GitHubUser(login="someoneelse", id=3, type="User")],
        created_at="2026-06-17T00:00:00Z",
        updated_at="2026-06-17T00:00:00Z",
    )

    def fetch_subject(
        url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        return mock_pr

    matched = matcher.find_matching_rule(notification, fetch_subject)
    assert matched is None


def test_context_variable_available_in_expression() -> None:
    """Extra context (e.g. account.github.username) must be usable in `expression`."""
    rules = [
        Rule(
            id="reason_matches_username",
            expression="notification.reason == account.github.username",
            action=RuleAction(notify=NotifyActionConfig(title="Match", body="Match")),
        ),
    ]
    matcher = RuleMatcher(
        rules, account={"github": {"username": "mention"}}, variables={}
    )

    notification = GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(title="Test Issue", type="Issue"),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )

    matched = matcher.find_matching_rule(notification)
    assert matched is not None
    assert matched.id == "reason_matches_username"


def test_context_variable_available_in_subject_expression() -> None:
    """Account context must be usable in expressions with subject access."""
    rules = [
        Rule(
            id="pr_assigned_to_account",
            expression="notification.subject.type == \"PullRequest\" and (account.github.username in subject.assignees|map(attribute='login'))",
            action=RuleAction(
                notify=NotifyActionConfig(title="PR Assigned", body="You were assigned")
            ),
        ),
    ]
    matcher = RuleMatcher(
        rules, account={"github": {"username": "testuser"}}, variables={}
    )

    notification = GitHubNotification(
        id="456",
        reason="assign",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test PR",
            type="PullRequest",
            url="https://api.github.com/repos/owner/repo/pulls/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/456",
        subscription_url="https://api.github.com/notifications/threads/456/subscription",
    )

    mock_pr = GitHubPullRequest(
        id=1,
        number=1,
        title="Test PR",
        state="open",
        user=GitHubUser(login="otheruser", id=2, type="User"),
        assignees=[GitHubUser(login="testuser", id=1, type="User")],
        created_at="2026-06-17T00:00:00Z",
        updated_at="2026-06-17T00:00:00Z",
    )

    def fetch_subject(
        url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        return mock_pr

    matched = matcher.find_matching_rule(notification, fetch_subject)
    assert matched is not None
    assert matched.id == "pr_assigned_to_account"


def test_subject_not_fetched_when_cheap_guard_fails() -> None:
    """Test that subject is not fetched when cheap guard short-circuits."""
    rules = [
        Rule(
            id="pr_only",
            expression='notification.subject.type == "PullRequest" and ("testuser" in subject.assignees|map(attribute="login"))',
            action=RuleAction(
                notify=NotifyActionConfig(title="PR", body="PR notification")
            ),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    # Issue notification (not PR)
    notification = GitHubNotification(
        id="789",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test Issue",
            type="Issue",
            url="https://api.github.com/repos/owner/repo/issues/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/789",
        subscription_url="https://api.github.com/notifications/threads/789/subscription",
    )

    # Subject fetcher should not be called
    fetcher_called = False

    def fetch_subject(
        url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        nonlocal fetcher_called
        fetcher_called = True
        raise AssertionError("Subject fetcher should not be called")

    matched = matcher.find_matching_rule(notification, fetch_subject)
    assert matched is None
    assert not fetcher_called


def test_rule_matcher_reaches_repository_org_and_subject_web_url() -> None:
    """`build_context` injects `repository.org`/`subject.web_url` - a rule
    expression can now reach both, unlike the old CEL activation."""
    rules = [
        Rule(
            id="org_and_url",
            expression=(
                'notification.repository.org == "owner" and '
                "notification.subject.web_url is not none"
            ),
            action=RuleAction(notify=NotifyActionConfig(title="Match", body="Match")),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test Issue",
            type="Issue",
            url="https://api.github.com/repos/owner/repo/issues/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/123",
        subscription_url="https://api.github.com/notifications/threads/123/subscription",
    )

    matched = matcher.find_matching_rule(notification)
    assert matched is not None
    assert matched.id == "org_and_url"


def test_subject_fetched_once_across_rules() -> None:
    """Multiple subject-touching rules should only fetch the subject once."""
    rules = [
        Rule(
            id="rule1",
            expression='notification.subject.type == "PullRequest" and subject.state == "open"',
            action=RuleAction(notify=NotifyActionConfig(title="R1", body="R1")),
        ),
        Rule(
            id="rule2",
            expression='notification.subject.type == "PullRequest" and subject.draft',
            action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
        ),
        Rule(
            id="rule3",
            expression='notification.subject.type == "PullRequest" and subject.merged',
            action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="1",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test PR",
            type="PullRequest",
            url="https://api.github.com/repos/owner/repo/pulls/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/1",
        subscription_url="https://api.github.com/notifications/threads/1/subscription",
    )

    fetch_count = 0

    def fetch_subject(
        url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        nonlocal fetch_count
        fetch_count += 1
        return GitHubPullRequest(
            id=1,
            number=1,
            title="Test PR",
            state="open",
            user=GitHubUser(login="someone", id=1, type="User"),
            created_at="2026-06-17T00:00:00Z",
            updated_at="2026-06-17T00:00:00Z",
        )

    matched = matcher.find_matching_rule(notification, fetch_subject)
    assert matched is not None
    assert matched.id == "rule1"
    assert fetch_count == 1


@pytest.mark.parametrize(
    "exception_type",
    [NotImplementedError, RuntimeError],
    ids=["NotImplementedError", "RuntimeError"],
)
def test_subject_fetch_failure_raises_runtime_error(
    exception_type: type[Exception],
) -> None:
    """Subject fetch failures should raise RuntimeError with rule/notification context."""
    rules = [
        Rule(
            id="failing_rule",
            expression='notification.subject.type == "PullRequest" and subject.merged',
            action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="fail123",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test PR",
            type="PullRequest",
            url="https://api.github.com/repos/owner/repo/pulls/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/fail123",
        subscription_url="https://api.github.com/notifications/threads/fail123/subscription",
    )

    def fetch_subject(
        url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        raise exception_type("Fetch failed")

    with pytest.raises(RuntimeError) as exc_info:
        matcher.find_matching_rule(notification, fetch_subject)
    assert "failing_rule" in str(exc_info.value)
    assert "fail123" in str(exc_info.value)
    assert "notification.subject.type" in str(exc_info.value)


def test_expression_touching_subject_without_fetcher_raises() -> None:
    """Expression referencing subject without a fetcher should raise RuntimeError."""
    rules = [
        Rule(
            id="needs_subject",
            expression='notification.subject.type == "Issue" and subject.state == "open"',
            action=RuleAction(notify=NotifyActionConfig(title="T", body="B")),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="no_fetcher",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test Issue",
            type="Issue",
            url="https://api.github.com/repos/owner/repo/issues/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/no_fetcher",
        subscription_url="https://api.github.com/notifications/threads/no_fetcher/subscription",
    )

    with pytest.raises(RuntimeError) as exc_info:
        matcher.find_matching_rule(notification, subject_fetcher=None)
    assert "needs_subject" in str(exc_info.value)
    assert "no subject fetcher was provided" in str(exc_info.value)


def test_missing_subject_field_raises_rather_than_no_match() -> None:
    """Accessing a missing subject field should raise, not silently fail to match."""
    rules = [
        Rule(
            id="merged_check",
            expression='notification.subject.type == "Issue" and subject.merged',
            action=RuleAction(mark_as_read=MarkAsReadActionConfig()),
        ),
    ]
    matcher = RuleMatcher(rules, account={}, variables={})

    notification = GitHubNotification(
        id="missing_field",
        reason="mention",
        unread=True,
        updated_at="2026-06-17T00:00:00Z",
        subject=GitHubSubject(
            title="Test Issue",
            type="Issue",
            url="https://api.github.com/repos/owner/repo/issues/1",
        ),
        repository=GitHubRepository(id=1, name="repo", full_name="owner/repo"),
        url="https://api.github.com/notifications/threads/missing_field",
        subscription_url="https://api.github.com/notifications/threads/missing_field/subscription",
    )

    def fetch_subject(
        url: str, type: str, updated_at: str
    ) -> GitHubIssue | GitHubPullRequest:
        return GitHubIssue(
            id=1,
            number=1,
            title="Test Issue",
            state="open",
            user=GitHubUser(login="someone", id=1, type="User"),
            created_at="2026-06-17T00:00:00Z",
            updated_at="2026-06-17T00:00:00Z",
        )

    with pytest.raises(RuntimeError) as exc_info:
        matcher.find_matching_rule(notification, fetch_subject)
    assert "merged_check" in str(exc_info.value)
    assert "Jinja expression error" in str(exc_info.value)
