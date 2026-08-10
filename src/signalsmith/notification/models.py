from enum import StrEnum

__all__: list[str] = []


class NotificationOutcome(StrEnum):
    """Outcome for a processed notification."""

    NOTIFIED = "notified"
    MARKED_AS_READ = "marked_as_read"
    IGNORED = "ignored"
    PERMANENTLY_IGNORED = "permanently_ignored"  # via the "Ignore" button, not a rule
    SKIPPED = "skipped"  # renotify interval not elapsed
    FILTERED_ORG = "filtered_org"
    FILTERED_ALREADY_READ = "filtered_already_read"
    FILTERED_ERROR = "filtered_error"
