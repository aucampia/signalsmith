# GitHubUser/GitHubLabel/GitHubTeam/GitHubIssue/GitHubPullRequest stay as
# BaseModel (exempted from the TID251 ban in pyproject.toml): they rely on
# `extra="allow"` to preserve undeclared GitHub API fields (e.g. `merged` on
# PRs) for CEL rule expressions and debug/spool JSON, and pydantic dataclasses
# silently drop extra fields on dump with no way to opt back in.
from pydantic import BaseModel, TypeAdapter
from pydantic.dataclasses import dataclass

__all__: list[str] = []


class GitHubUser(BaseModel):
    model_config = {"extra": "allow"}

    login: str
    id: int
    type: str


class GitHubLabel(BaseModel):
    model_config = {"extra": "allow"}

    id: int
    name: str
    color: str


class GitHubTeam(BaseModel):
    model_config = {"extra": "allow"}

    id: int
    name: str
    slug: str


class GitHubIssue(BaseModel):
    model_config = {"extra": "allow"}

    id: int
    number: int
    title: str
    body: str | None = None
    state: str
    user: GitHubUser
    assignees: list[GitHubUser] = []
    labels: list[GitHubLabel] = []
    created_at: str
    updated_at: str


class GitHubPullRequest(BaseModel):
    model_config = {"extra": "allow"}

    id: int
    number: int
    title: str
    body: str | None = None
    state: str
    user: GitHubUser
    assignees: list[GitHubUser] = []
    labels: list[GitHubLabel] = []
    draft: bool = False
    requested_reviewers: list[GitHubUser] = []
    requested_teams: list[GitHubTeam] = []
    mergeable_state: str | None = None
    created_at: str
    updated_at: str


@dataclass(kw_only=True)
class GitHubSubject:
    title: str
    url: str | None = None
    latest_comment_url: str | None = None
    type: str

    @property
    def web_url(self) -> str | None:
        """Convert API URL to web URL.

        Examples:
            https://api.github.com/repos/owner/repo/pulls/123
            -> https://github.com/owner/repo/pull/123

            https://api.github.com/repos/owner/repo/issues/456
            -> https://github.com/owner/repo/issues/456
        """
        if not self.url:
            return None

        # Replace API domain with web domain
        web_url = self.url.replace(
            "https://api.github.com/repos/", "https://github.com/"
        )

        # Convert /pulls/ to /pull/ (singular) for pull requests
        web_url = web_url.replace("/pulls/", "/pull/")

        return web_url


@dataclass(kw_only=True)
class GitHubRepository:
    id: int
    name: str
    full_name: str
    private: bool = False

    @property
    def org(self) -> str:
        """Extract organization name from full_name.

        Returns the owner/org portion of the full_name.
        Example: "my-organization/my-repo" -> "my-organization"
        """
        return self.full_name.split("/")[0]


@dataclass(kw_only=True)
class GitHubNotification:
    id: str
    reason: str
    unread: bool
    updated_at: str
    last_read_at: str | None = None
    subject: GitHubSubject
    repository: GitHubRepository
    url: str
    subscription_url: str

    @property
    def debug_info(self) -> str:
        return f"<<< {self.id} {self.reason} {self.subject.title} {self.subject.web_url} >>>"


GITHUB_NOTIFICATION_ADAPTER: TypeAdapter[GitHubNotification] = TypeAdapter(
    GitHubNotification
)
GITHUB_ISSUE_ADAPTER: TypeAdapter[GitHubIssue] = TypeAdapter(GitHubIssue)
GITHUB_PULL_REQUEST_ADAPTER: TypeAdapter[GitHubPullRequest] = TypeAdapter(
    GitHubPullRequest
)


# ---------------------------------------------------------------------------
# Cache models
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class CacheMetadata:
    fetched_at: str
    last_modified: str | None = None
    etag: str | None = None


@dataclass(kw_only=True)
class CacheData:
    metadata: CacheMetadata
    notifications: list[GitHubNotification]


CACHE_DATA_ADAPTER: TypeAdapter[CacheData] = TypeAdapter(CacheData)
