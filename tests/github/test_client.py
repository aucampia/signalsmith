import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from signalsmith.github.client import GitHubClient
from signalsmith.versioning import (
    CACHE_VERSION,
    SchemaVersion,
    VersionError,
    read_marker,
    write_marker,
)


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def mock_notification_data() -> dict[str, Any]:
    """Sample notification data from GitHub API."""
    return {
        "id": "123",
        "reason": "mention",
        "unread": True,
        "updated_at": "2026-06-17T00:00:00Z",
        "last_read_at": None,
        "subject": {
            "title": "Test Issue",
            "url": "https://api.github.com/repos/owner/repo/issues/1",
            "latest_comment_url": "https://api.github.com/repos/owner/repo/issues/comments/1",
            "type": "Issue",
        },
        "repository": {
            "id": 1,
            "name": "repo",
            "full_name": "owner/repo",
            "private": False,
        },
        "url": "https://api.github.com/notifications/threads/123",
        "subscription_url": "https://api.github.com/notifications/threads/123/subscription",
    }


def test_get_notifications_fetches_from_api(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """Test fetching notifications from GitHub API."""
    client = GitHubClient("test-token", temp_cache_dir)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        # Mock the API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [mock_notification_data]
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",  # No pagination
        }
        mock_client.get.return_value = mock_response

        notifications = client.get_notifications()

        assert len(notifications) == 1
        assert notifications[0].id == "123"
        assert notifications[0].reason == "mention"
        assert notifications[0].subject.title == "Test Issue"


def test_get_notifications_respects_limit(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """Test that limit stops fetching early."""
    client = GitHubClient("test-token", temp_cache_dir)

    # Create 5 notifications
    notifications_data = [{**mock_notification_data, "id": str(i)} for i in range(5)]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = notifications_data
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
        }
        mock_client.get.return_value = mock_response

        # Fetch with limit of 2
        notifications = client.get_notifications(limit=2)

        assert len(notifications) == 2
        assert notifications[0].id == "0"
        assert notifications[1].id == "1"


def test_get_notifications_saves_cache(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """Test that notifications are saved to cache."""
    client = GitHubClient("test-token", temp_cache_dir)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [mock_notification_data]
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
        }
        mock_client.get.return_value = mock_response

        client.get_notifications()

        # Check cache file was created
        cache_file = temp_cache_dir / "notifications.json"
        assert cache_file.exists()


def test_get_notifications_archives_as_jsonl(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """Every fetch appends one JSONL line per notification to the archive file."""
    client = GitHubClient("test-token", temp_cache_dir)

    def mock_response_for(notification_data: dict[str, Any]) -> Mock:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [notification_data]
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
        }
        return mock_response

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response_for(mock_notification_data)
        client.get_notifications()

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response_for(
            {**mock_notification_data, "id": "456"}
        )
        client.get_notifications(refresh=True)

    archive_files = list(temp_cache_dir.glob("notifications-archive-*.jsonl"))
    assert len(archive_files) == 1
    lines = archive_files[0].read_text().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["notification"]["id"] == "123"
    assert "fetched_at" in first

    second = json.loads(lines[1])
    assert second["notification"]["id"] == "456"


def test_get_notifications_uses_cache(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """Test that cache_only=True reads from cache without API call."""
    # First client to populate the cache
    client = GitHubClient("test-token", temp_cache_dir, cache_only=False)

    # Populate the cache
    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [mock_notification_data]
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
        }
        mock_client.get.return_value = mock_response

        client.get_notifications()

    # Now create cache_only client - should not make API call
    cache_only_client = GitHubClient("test-token", temp_cache_dir, cache_only=True)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        notifications = cache_only_client.get_notifications()

        # Should not have made any HTTP requests
        mock_client.get.assert_not_called()
        assert len(notifications) == 1
        assert notifications[0].id == "123"


def test_get_notifications_limit_does_not_write_cache(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """A limit-truncated fetch must not persist to the on-disk cache.

    The ETag/Last-Modified captured from the first page reflect the full
    notification feed, not just the limited subset fetched. Caching the
    truncated list under that metadata would make a later conditional
    request incorrectly return only the truncated list forever (until the
    feed changes), silently hiding the rest of the unread notifications.
    """
    client = GitHubClient("test-token", temp_cache_dir)
    notifications_data = [{**mock_notification_data, "id": str(i)} for i in range(5)]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = notifications_data
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
        }
        mock_client.get.return_value = mock_response

        notifications = client.get_notifications(limit=2)

        assert len(notifications) == 2
        cache_file = temp_cache_dir / "notifications.json"
        assert not cache_file.exists()


def test_get_notifications_304_response_respects_limit(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """A cached (304) response must still be sliced to the requested limit."""
    client = GitHubClient("test-token", temp_cache_dir)
    notifications_data = [{**mock_notification_data, "id": str(i)} for i in range(5)]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = notifications_data
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
        }
        mock_client.get.return_value = mock_response

        # Unlimited fetch populates a full 5-item cache.
        client.get_notifications()

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 304
        mock_response.headers = {"X-RateLimit-Remaining": "5000"}
        mock_client.get.return_value = mock_response

        notifications = client.get_notifications(limit=2)

        assert len(notifications) == 2
        assert notifications[0].id == "0"
        assert notifications[1].id == "1"


def test_get_notifications_refresh_skips_conditional_headers(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """refresh=True must not send If-None-Match/If-Modified-Since, even with a cache."""
    client = GitHubClient("test-token", temp_cache_dir)
    notifications_data = [{**mock_notification_data, "id": str(i)} for i in range(5)]

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = notifications_data
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
        }
        mock_client.get.return_value = mock_response

        # Unlimited fetch populates the cache with an ETag.
        client.get_notifications()

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        # Fresh data from the API, e.g. a notification was cleared upstream.
        refreshed_data = [{**mock_notification_data, "id": str(i)} for i in range(3)]
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = refreshed_data
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:01 GMT",
            "ETag": '"def456"',
            "Link": "",
        }
        mock_client.get.return_value = mock_response

        notifications = client.get_notifications(refresh=True)

        call_headers = mock_client.get.call_args.kwargs["headers"]
        assert "If-None-Match" not in call_headers
        assert "If-Modified-Since" not in call_headers
        assert len(notifications) == 3


def test_get_notifications_records_poll_interval(
    temp_cache_dir: Path, mock_notification_data: dict[str, Any]
) -> None:
    """The client should expose GitHub's suggested X-Poll-Interval, if sent."""
    client = GitHubClient("test-token", temp_cache_dir)
    assert client.poll_interval is None

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [mock_notification_data]
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "Last-Modified": "Mon, 17 Jun 2026 00:00:00 GMT",
            "ETag": '"abc123"',
            "Link": "",
            "X-Poll-Interval": "60",
        }
        mock_client.get.return_value = mock_response

        client.get_notifications()

        assert client.poll_interval == 60


def test_get_authenticated_user_fetches_and_caches(temp_cache_dir: Path) -> None:
    """The login is fetched from /user once, then served from the disk cache."""
    client = GitHubClient("test-token", temp_cache_dir)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"login": "testuser", "id": 1}
        mock_response.headers = {"X-RateLimit-Remaining": "5000"}
        mock_client.get.return_value = mock_response

        assert client.get_authenticated_user() == "testuser"
        mock_client.get.assert_called_once()

    cache_file = temp_cache_dir / "user.json"
    assert cache_file.exists()

    # A fresh client instance should read the cached login without an API call.
    other_client = GitHubClient("test-token", temp_cache_dir)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        assert other_client.get_authenticated_user() == "testuser"
        mock_client.get.assert_not_called()


def test_mark_as_read(temp_cache_dir: Path) -> None:
    """Test marking a notification as read."""
    client = GitHubClient("test-token", temp_cache_dir)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 205
        mock_response.headers = {"X-RateLimit-Remaining": "5000"}
        mock_client.patch.return_value = mock_response

        client.mark_as_read("123")

        # Verify the PATCH was called with correct URL
        mock_client.patch.assert_called_once()
        call_args = mock_client.patch.call_args
        assert "notifications/threads/123" in call_args[0][0]


# ---------------------------------------------------------------------------
# Cache versioning
# ---------------------------------------------------------------------------


def test_fresh_cache_dir_gets_version_marker(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    GitHubClient("test-token", cache_dir)
    assert read_marker(cache_dir) == CACHE_VERSION


def test_populated_cache_dir_without_marker_raises(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "notifications.json").write_text("{}")
    with pytest.raises(VersionError):
        GitHubClient("test-token", cache_dir)


@pytest.mark.parametrize(
    ("marker_version", "expect_warning"),
    [
        (CACHE_VERSION, False),
        (SchemaVersion(major=CACHE_VERSION.major, minor=CACHE_VERSION.minor + 1), True),
    ],
    ids=["matching", "newer-minor"],
)
def test_cache_dir_with_compatible_marker_opens_fine(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    marker_version: SchemaVersion,
    expect_warning: bool,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "notifications.json").write_text("{}")
    write_marker(cache_dir, marker_version)
    with caplog.at_level(logging.WARNING):
        GitHubClient("test-token", cache_dir)
    assert any("newer" in record.message for record in caplog.records) == expect_warning


def test_cache_dir_with_incompatible_marker_raises_with_remedy(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "notifications.json").write_text("{}")
    write_marker(cache_dir, SchemaVersion(major=CACHE_VERSION.major + 1, minor=0))
    with pytest.raises(VersionError) as exc_info:
        GitHubClient("test-token", cache_dir)
    assert "signalsmith cache clean" in str(exc_info.value)
