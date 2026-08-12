"""Fixtures/doubles shared across the test suite."""

import pytest

from signalsmith.github.models import GitHubIssue, GitHubNotification, GitHubPullRequest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Forbid class-based tests - see AGENTS.md testing conventions."""
    offenders = sorted(
        {
            f"{item.cls.__module__}.{item.cls.__qualname__}"
            for item in items
            if isinstance(item, pytest.Function) and item.cls is not None
        }
    )
    if offenders:
        raise pytest.UsageError(
            "Test classes are forbidden, use standalone functions instead: "
            + ", ".join(offenders)
        )


class MockProvider:
    """In-memory `NotificationProvider` double for testing."""

    name = "github"

    def __init__(
        self,
        notifications: list[GitHubNotification],
        subjects: dict[str, GitHubIssue | GitHubPullRequest] | None = None,
        *,
        poll_interval: int | None = None,
    ) -> None:
        self._notifications = notifications
        self._subjects = subjects or {}
        self.poll_interval = poll_interval
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
