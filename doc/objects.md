# Object Reference

Canonical field reference for the data objects available in rule expressions
and templates. No other doc page duplicates these listings.

The source of truth for these shapes is `src/signalsmith/github/models.py`.
Signalsmith is not the schema owner — see the external references below for the
upstream GitHub API documentation and example responses.

## `notification`

Always available, requires no API call. Built from the
[GitHub Notifications API](https://docs.github.com/en/rest/activity/notifications#about-notifications)
response.

- `notification.id` (str) — unique notification ID
- `notification.reason` (str) — e.g. `"mention"`, `"assign"`,
  `"review_requested"`, `"ci_activity"`, `"subscribed"`, `"comment"`
- `notification.unread` (bool)
- `notification.updated_at` (str, ISO-8601)
- `notification.last_read_at` (str or null)
- `notification.url` (str) — API URL for this notification
- `notification.subscription_url` (str)

### `notification.subject`

- `notification.subject.title` (str)
- `notification.subject.type` (str) — e.g. `"Issue"`, `"PullRequest"`,
  `"Release"`, `"Discussion"`
- `notification.subject.url` (str or null) — API URL for the subject detail
- `notification.subject.latest_comment_url` (str or null)
- `notification.subject.web_url` (str or null) — computed web URL
  (e.g. `https://github.com/owner/repo/pull/123`), derived from `.url`

### `notification.repository`

- `notification.repository.id` (int)
- `notification.repository.name` (str)
- `notification.repository.full_name` (str) — e.g. `"org/repo"`
- `notification.repository.private` (bool)
- `notification.repository.org` (str) — computed, the owner portion of
  `full_name` (e.g. `"org"`)

## `subject`

The full Issue or PullRequest object, fetched from the GitHub API only when a
rule expression or template touches it. In rule expressions this is a lazy
proxy — see [Rule Expression Reference](./rules.md#lazy-subject-access).

Comes from either the
[GitHub Issues API](https://docs.github.com/en/rest/issues/issues#get-an-issue--example-response)
or
[GitHub Pull Requests API](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request--example-response).

### Common to Issues and PRs

- `subject.id` (int)
- `subject.number` (int)
- `subject.title` (str)
- `subject.body` (str or null)
- `subject.state` (str) — `"open"` or `"closed"`
- `subject.created_at` (str, ISO-8601)
- `subject.updated_at` (str, ISO-8601)

#### `subject.user`

- `subject.user.login` (str)
- `subject.user.id` (int)
- `subject.user.type` (str)

#### `subject.assignees[]`

List of users. Each item:

- `.login` (str)
- `.id` (int)
- `.type` (str)

#### `subject.labels[]`

List of labels. Each item:

- `.name` (str)
- `.color` (str)
- `.id` (int)

### PR-only fields

Only present on PullRequest subjects.

- `subject.draft` (bool)
- `subject.merged` (bool) — from `extra="allow"`; present in the raw payload
  but not in the declared schema
- `subject.mergeable_state` (str or null)
- `subject.requested_reviewers[]` — list of users (`.login`, `.id`, `.type`)
- `subject.requested_teams[]` — list of teams (`.slug`, `.name`, `.id`)

### `extra="allow"`

Both `GitHubIssue` and `GitHubPullRequest` models accept undeclared fields
from the GitHub API. Fields like `closed_at`, `milestone`, `merged_by` (on
PRs) may be accessible even though not listed here. The GitHub API docs linked
above are the authoritative reference.

## `account`

Sourced from `GET /user`
([GitHub Users API](https://docs.github.com/en/rest/users/users#get-the-authenticated-user)),
cached indefinitely since it never changes.

- `account.github.username` (str) — the authenticated user's GitHub login

## `variables`

User-defined from the config's top-level `variables:` block. A plain `dict`,
so access it safely with `.get()` chaining:

```yaml
variables.get('ontopic', {}).get('prefixes', [])
```

`|default` only guards the final hop — once an intermediate key is missing,
the error has already occurred. `.get()` is safe at every level.
