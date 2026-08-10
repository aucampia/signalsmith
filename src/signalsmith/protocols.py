"""Protocols for signalsmith components."""

from typing import Protocol

from .github.models import GitHubIssue, GitHubNotification, GitHubPullRequest


class NotificationProvider(Protocol):
    """Protocol for fetching and managing notifications."""

    name: str
    """Short identifier for this provider, used e.g. in spool filenames."""

    def get_notifications(
        self, limit: int | None = None, refresh: bool = False
    ) -> list[GitHubNotification]:
        """Fetch notifications from the provider."""
        ...

    def mark_as_read(self, notification_id: str) -> None:
        """Mark a notification as read."""
        ...

    def get_authenticated_user(self) -> str:
        """Fetch the authenticated user's login."""
        ...

    def get_subject(
        self,
        subject_url: str,
        subject_type: str,
        notification_updated_at: str,
    ) -> GitHubIssue | GitHubPullRequest:
        """Fetch full subject object from the provider."""
        ...
