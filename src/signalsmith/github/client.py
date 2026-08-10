import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..versioning import CACHE_VERSION, ensure_store_version
from .models import (
    CACHE_DATA_ADAPTER,
    GITHUB_NOTIFICATION_ADAPTER,
    CacheData,
    CacheMetadata,
    GitHubIssue,
    GitHubNotification,
    GitHubPullRequest,
)

logger = logging.getLogger(__name__)

__all__: list[str] = []

_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"


def _utc_now_iso() -> str:
    """Current UTC time as an ISO 8601 string with a literal Z suffix.

    `datetime.isoformat()` renders a UTC-aware datetime with a `+00:00`
    offset instead - using `Z` everywhere on disk avoids anyone mistaking
    it for a naive/local timestamp.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RateLimitError(Exception):
    def __init__(self, reset_at: datetime) -> None:
        self.reset_at = reset_at
        super().__init__(f"Rate limit exceeded; resets at {reset_at.isoformat()}")


class GitHubClient:
    name = "github"

    def __init__(self, token: str, cache_dir: Path, cache_only: bool = False) -> None:
        self._token = token
        self._cache_dir = cache_dir
        self._cache_file = cache_dir / "notifications.json"
        self._cache_only = cache_only
        ensure_store_version(
            cache_dir,
            CACHE_VERSION,
            label="Cache",
            clean_command="signalsmith cache clean",
        )
        # GitHub's suggested minimum seconds between notification polls, from the
        # most recent response's X-Poll-Interval header (None until first fetch).
        self.poll_interval: int | None = None

    def _record_poll_interval(self, headers: httpx.Headers) -> None:
        value = headers.get("X-Poll-Interval")
        if value is None:
            return
        try:
            self.poll_interval = int(value)
        except ValueError:
            logger.warning("Ignoring non-integer X-Poll-Interval header: %r", value)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    def _load_cache(self) -> CacheData | None:
        if not self._cache_file.exists():
            return None
        try:
            return CACHE_DATA_ADAPTER.validate_json(self._cache_file.read_text())
        except Exception:
            logger.warning("Failed to load cache; ignoring", exc_info=True)
            return None

    def _save_cache(self, cache: CacheData) -> None:
        self._cache_file.write_bytes(CACHE_DATA_ADAPTER.dump_json(cache, indent=2))

    def _archive_notifications(self, notifications: list[GitHubNotification]) -> None:
        """Append every fetch result to a JSON Lines archive for later analysis/debugging.

        Unlike notifications.json, which is only written on unlimited fetches
        so it stays safe to drive conditional (ETag) requests, this file is
        appended to unconditionally on every fetch - including --limit,
        --cache-only and --refresh-notifications - and is never read back by
        signalsmith itself. One line per notification, each tagged with the time
        it was retrieved, so the history can be inspected/analyzed later
        (e.g. with `jq`) without needing to have captured it at the time.

        Split into one file per UTC hour (notifications-archive-YYYYMMDDTHHZ.jsonl)
        so no single file grows unbounded and a time window is easy to locate.
        """
        now = datetime.now(UTC)
        archive_file = (
            self._cache_dir
            / f"notifications-archive-{now.strftime('%Y%m%dT%HZ')}.jsonl"
        )
        fetched_at = _utc_now_iso()
        with archive_file.open("a") as fh:
            for notification in notifications:
                fh.write(
                    json.dumps(
                        {
                            "fetched_at": fetched_at,
                            "notification": GITHUB_NOTIFICATION_ADAPTER.dump_python(
                                notification, mode="json"
                            ),
                        }
                    )
                )
                fh.write("\n")

    def _check_rate_limit(self, headers: httpx.Headers) -> None:
        remaining = int(headers.get("X-RateLimit-Remaining", "1"))
        if remaining > 0:
            return
        reset_ts = int(headers.get("X-RateLimit-Reset", "0"))
        reset_at = datetime.fromtimestamp(reset_ts, tz=UTC)
        wait = max(0.0, (reset_at - datetime.now(tz=UTC)).total_seconds())
        logger.warning(
            "Rate limit reached; sleeping %.0f s until %s", wait, reset_at.isoformat()
        )
        time.sleep(wait)

    def _fetch_page(
        self,
        client: httpx.Client,
        url: str,
        extra_headers: dict[str, str],
    ) -> httpx.Response:
        headers = {**self._headers(), **extra_headers}
        response = client.get(
            url, headers=headers, params={"all": "false", "per_page": "50"}
        )
        self._check_rate_limit(response.headers)
        response.raise_for_status()
        return response

    def get_notifications(
        self, limit: int | None = None, refresh: bool = False
    ) -> list[GitHubNotification]:
        cache = self._load_cache()

        if self._cache_only:
            if cache is not None:
                logger.debug(
                    "Using cached notifications (%d items)", len(cache.notifications)
                )
                cached = cache.notifications
                if limit is not None:
                    cached = cached[:limit]
                    logger.debug("Limiting to %d notifications", limit)
                self._archive_notifications(cached)
                return cached
            logger.warning(
                "--cache-only requested but no cache file found; fetching from API"
            )

        conditional_headers: dict[str, str] = {}
        if cache is not None and not refresh:
            if cache.metadata.etag:
                conditional_headers["If-None-Match"] = cache.metadata.etag
            elif cache.metadata.last_modified:
                conditional_headers["If-Modified-Since"] = cache.metadata.last_modified
        elif refresh:
            logger.debug("Refresh requested; skipping conditional cache headers")

        notifications: list[GitHubNotification] = []
        cache_metadata: CacheMetadata | None = None

        with httpx.Client(follow_redirects=True) as client:
            url: str | None = f"{_API_BASE}/notifications"
            first_page = True
            while url is not None:
                # Only add params on first request; pagination URLs already include them
                # all=false means only unread notifications (default GitHub behavior)
                params = {"all": "false", "per_page": "50"} if first_page else None
                response = client.get(
                    url,
                    headers={
                        **self._headers(),
                        **(conditional_headers if first_page else {}),
                    },
                    params=params,
                )
                self._check_rate_limit(response.headers)
                if first_page:
                    self._record_poll_interval(response.headers)

                if first_page and response.status_code == 304:
                    logger.debug("Notifications unchanged (304); using cache")
                    assert cache is not None
                    cached_notifications = cache.notifications
                    if limit is not None:
                        cached_notifications = cached_notifications[:limit]
                    self._archive_notifications(cached_notifications)
                    return cached_notifications

                response.raise_for_status()
                page_data = response.json()
                for item in page_data:
                    notifications.append(
                        GITHUB_NOTIFICATION_ADAPTER.validate_python(item)
                    )
                    # Stop if we've reached the limit
                    if limit is not None and len(notifications) >= limit:
                        logger.debug("Reached notification limit of %d", limit)
                        url = None
                        break

                if first_page:
                    # Capture metadata from first page for caching
                    cache_metadata = CacheMetadata(
                        fetched_at=_utc_now_iso(),
                        last_modified=response.headers.get("Last-Modified"),
                        etag=response.headers.get("ETag"),
                    )
                    first_page = False

                # Write cache incrementally after each page, but only for
                # unlimited fetches: the ETag/Last-Modified captured above
                # reflect the full notification feed, so persisting a
                # limit-truncated list under that metadata would make a
                # later 304 response incorrectly serve the truncated list.
                if limit is None:
                    self._save_cache(
                        CacheData(
                            metadata=cache_metadata
                            or CacheMetadata(fetched_at=_utc_now_iso()),
                            notifications=notifications,
                        )
                    )
                    logger.debug(
                        "Saved %d notifications to cache (page complete)",
                        len(notifications),
                    )

                # Pagination via Link header - the URL already includes query params
                # (unless we already set url=None due to limit being reached)
                if url is not None:
                    url = None
                    link_header = response.headers.get("Link", "")
                    for part in link_header.split(","):
                        part = part.strip()
                        if 'rel="next"' in part:
                            url = part.split(";")[0].strip().strip("<>")
                            break

        logger.debug("Fetched %d total notifications from GitHub", len(notifications))
        self._archive_notifications(notifications)
        return notifications

    @retry(
        retry=retry_if_exception_type((httpx.ReadTimeout, httpx.ConnectTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _fetch_subject_with_retry(self, subject_url: str) -> httpx.Response:
        """Fetch subject from GitHub API with automatic retries on timeout.

        Uses tenacity to retry up to 3 times with exponential backoff on timeout errors.
        """
        with httpx.Client(timeout=10.0) as client:
            response = client.get(subject_url, headers=self._headers())
            self._check_rate_limit(response.headers)
            response.raise_for_status()
            return response

    def get_authenticated_user(self) -> str:
        """Fetch the authenticated user's GitHub login.

        Cached indefinitely on disk since a login essentially never changes,
        so config expressions can reference it without an API call on every run.
        """
        user_cache_file = self._cache_dir / "user.json"
        if user_cache_file.exists():
            try:
                return str(json.loads(user_cache_file.read_text())["login"])
            except Exception:
                logger.warning("Failed to load cached user; refetching", exc_info=True)

        with httpx.Client() as client:
            response = client.get(f"{_API_BASE}/user", headers=self._headers())
            self._check_rate_limit(response.headers)
            response.raise_for_status()
            data = response.json()

        user_cache_file.write_text(json.dumps(data, indent=2))
        return str(data["login"])

    def mark_as_read(self, notification_id: str) -> None:
        logger.debug("Marking notification %s as read", notification_id)
        with httpx.Client() as client:
            response = client.patch(
                f"{_API_BASE}/notifications/threads/{notification_id}",
                headers=self._headers(),
            )
            self._check_rate_limit(response.headers)
            response.raise_for_status()

    def get_subject(
        self,
        subject_url: str,
        subject_type: str,
        notification_updated_at: str,
    ) -> GitHubIssue | GitHubPullRequest:
        """Fetch full subject object from GitHub API.

        Args:
            subject_url: The notification.subject.url field
            subject_type: The notification.subject.type field
            notification_updated_at: The notification.updated_at field for staleness check

        Returns:
            Parsed subject object (GitHubIssue or GitHubPullRequest)

        Raises:
            NotImplementedError: If subject_type is not Issue or PullRequest
        """
        # Validate subject_type first
        if subject_type not in ("Issue", "PullRequest"):
            raise NotImplementedError(
                f"Subject type '{subject_type}' not yet supported. "
                f"Only Issue and PullRequest are currently implemented."
            )

        # Use the full URL path as cache path to ensure uniqueness across repos
        # e.g., https://api.github.com/repos/owner/repo/pulls/42
        # -> subjects/api.github.com/repos/owner/repo/pulls/42.json
        url_path = subject_url.replace("https://", "").replace("http://", "")
        cache_file = self._cache_dir / "subjects" / f"{url_path}.json"

        # Check cache staleness
        if cache_file.exists():
            cache_mtime = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=UTC)
            notification_time = datetime.fromisoformat(
                notification_updated_at.replace("Z", "+00:00")
            )

            logger.debug(
                "Subject %s cache check: notification_time=%s, cache_mtime=%s",
                url_path,
                notification_time.isoformat(),
                cache_mtime.isoformat(),
            )

            # Consider cache fresh if notification time is within 1 second of cache mtime
            # This handles timezone/precision issues and treats "about the same time" as fresh
            time_diff = abs((notification_time - cache_mtime).total_seconds())
            logger.debug(
                "Subject %s time_diff=%.2fs (threshold=1.0s)",
                url_path,
                time_diff,
            )

            if time_diff <= 1.0:
                # Cache is fresh, load and return
                logger.debug(
                    "Using cached subject %s (cache fresh, time_diff=%.2fs)",
                    url_path,
                    time_diff,
                )
                return self._parse_subject(cache_file.read_text(), subject_type)
            elif notification_time < cache_mtime:
                # Notification is older than cache - cache is definitely fresh
                logger.debug(
                    "Using cached subject %s (notification older than cache)",
                    url_path,
                )
                return self._parse_subject(cache_file.read_text(), subject_type)
            else:
                logger.debug(
                    "Subject %s cache is stale (notification is %.2fs newer than cache)",
                    url_path,
                    time_diff,
                )
        else:
            logger.debug("Subject %s cache miss (file does not exist)", url_path)

        # Cache miss or stale - fetch from API with retries
        logger.debug("Fetching subject %s from API: %s", subject_type, subject_url)
        response = self._fetch_subject_with_retry(subject_url)

        # Store the raw response to preserve all fields from the API
        response_json = response.json()
        subject = self._parse_subject(response.text, subject_type)

        # Write to cache_file (create dirs if needed)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # Store raw API response with indentation for easy inspection
        cache_file.write_text(json.dumps(response_json, indent=2))

        logger.debug(
            "Cached subject %s (subject updated_at=%s)",
            url_path,
            subject.updated_at,
        )
        return subject

    def _parse_subject(
        self, json_text: str, subject_type: str
    ) -> GitHubIssue | GitHubPullRequest:
        """Parse subject JSON into the appropriate model."""
        if subject_type == "Issue":
            return GitHubIssue.model_validate_json(json_text)
        elif subject_type == "PullRequest":
            return GitHubPullRequest.model_validate_json(json_text)
        else:
            raise NotImplementedError(f"Subject type '{subject_type}' not supported")
