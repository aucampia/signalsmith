# Configuration Reference

## Configuration File

Config file location (checked in order):
1. `SIGNALSMITH_CONFIG` environment variable (if set)
2. `<config dir>/config.yaml`, where `<config dir>` is (checked in order):
   1. `SIGNALSMITH_CONFIG_DIR` environment variable (if set)
   2. `${XDG_CONFIG_HOME}/signalsmith` (default, typically `~/.config/signalsmith`)

Setting `SIGNALSMITH_CONFIG` alone only moves the config file — it has no
effect on the test directory (see [test-format.md](./test-format.md)), which
resolves independently via `SIGNALSMITH_TEST_DIR`/`SIGNALSMITH_CONFIG_DIR`.

## Configuration Structure

```yaml
# Config file schema version (see Versioning below). Required.
version: '3.0'

# Seconds between re-notifications for persistent unread items (default: 3600)
renotify_interval: 3600

# Seconds between polls in daemon mode (default: 300).
# Daemon mode treats this as a lower bound: if GitHub's response includes an
# X-Poll-Interval header larger than this value, the larger value is used for
# that sleep instead, per GitHub's guidance not to poll faster than it asks.
poll_interval: 300

# Default action for notifications that don't match any rule (default: notify)
# Options: "notify" (desktop alert) or "ignore" (silent, stays unread)
default_action: notify

# Organization filtering (optional) — applied before rule evaluation.
# Matched against the owner/org part of repository.full_name.
# include and exclude are mutually exclusive.
masks:
  orgs:
    include:
      - my-org
    # exclude:
    #   - spam-org

# Generic notice computed for every notification (optional - see Notices and
# Templates below). `notify` actions use this by default and can override
# either field.
notice:
  title: '{{ notification.subject.type }}: {{ notification.subject.title }}'
  body: '{{ notification.repository.full_name }} ({{ notification.reason }})'

# Reusable action definitions, referenced from rules via action.ref (optional).
actions:
  notify_default:
    notify: {}   # uses the generic notice above verbatim

rules:
  - id: 'issue_mention'
    expression: 'notification.subject.type == "Issue" and notification.reason == "mention"'
    action:
      notify:
        title: 'GitHub Issue Mention'
        body: 'You were mentioned in: {{ notification.subject.title }}'

  - id: 'issue_assigned_to_me'
    expression: 'notification.subject.type == "Issue"'
    subject_expression: "account.github.username in subject.assignees|map(attribute='login')"
    action:
      ref: notify_default

  - id: 'spam_repos'
    expression: 'notification.repository.full_name == "org/noisy-repo"'
    action:
      ignore: {}

  - id: 'ci_notifications'
    expression: 'notification.reason == "ci_activity"'
    action:
      mark_as_read: {}

# Spool of notified notifications (optional). See Spool below.
# spool:
#   dir: /custom/path/spool  # default: ${XDG_DATA_HOME}/signalsmith/spool

# Interactive "Dismiss"/"Ignore" action buttons (optional). Only takes effect
# in `signalsmith daemon` - see Clicking and Interactive Notifications below.
# notify_actions:
#   enabled: true
#   max_concurrent: 5    # at most this many button-bearing notifications in flight at once
#   wait_timeout: 20     # seconds; governs the concurrency limit above only
```

## Rules

- Rules are evaluated **in order**; the first matching rule wins.
- **Default behavior**: If no rule matches, uses the configured `default_action` (default: `notify`).
  - `default_action: notify` (default) — Desktop notification for unmatched items
  - `default_action: ignore` — Silent ignore for unmatched items (stays unread, no alert)
- Each rule's `id` must be unique within the config.
- A rule's `action` is either inline (`notify`/`mark_as_read`/`ignore`, exactly one) or a `ref` pointing at a name defined in the top-level `actions:` block — never both.

## Rule Expressions Are Jinja

`expression` and `subject_expression` are bare [Jinja](https://jinja.palletsprojects.com/)
expressions (not `{{ }}`-wrapped templates) — the same engine `notice`/`notify`
templates use, evaluated to a real Python value rather than rendered to text.
`&&`/`||`/`!` become `and`/`or`/`not`; equality/comparison/`in` are unchanged.

**Missing attributes raise.** Referencing something not present on the
current notification/subject (e.g. `subject.merged` on an Issue) raises, and
that one notification is reported as errored rather than silently not
matching — same as a syntax error. Guard explicitly:

- `subject.requested_reviewers|default([])` — supply a fallback value when a
  field may be entirely absent from the fetched subject.
- `subject.foo is defined` — check presence without supplying a fallback.
- **Nested access**: `variables.ontopic.prefixes|default([])` does **not** protect against a missing `variables.ontopic` — the `|default` filter only guards the final hop. Once `variables.ontopic` is `Undefined`, accessing `.prefixes` raises before the filter runs. Use `.get()` chaining for nested lookups:
  ```yaml
  variables.get('ontopic', {}).get('prefixes', [])
  ```
  This is safe for `variables` (a plain `dict`) and prevents errors when variable blocks are removed from the config.

**No `exists`/`all`/`has`/`size()` macros.** Use Jinja's built-in filters
instead — `select`/`selectattr`/`rejectattr`/`map`/`in`/`length`. Two idioms
come up often:

- "any item matches": `items|select(...)|first is defined`
- "no item fails a check": `items|reject(...)|first is undefined`

There's no built-in prefix test, so signalsmith registers one custom Jinja
test, `startingwith`, usable with `select`/`selectattr`:

```yaml
expression: >
  variables.get('offtopic', {}).get('prefixes', [])|select('startingwith', notification.repository.full_name)|first is defined
  or notification.repository.full_name in variables.get('offtopic', {}).get('repos', [])
```

Plain Python string methods also work directly (e.g.
`notification.repository.full_name.startswith("a-")`), which is simpler than
`startingwith` when checking against a single literal prefix rather than a
list.

## Two-Stage Rule Evaluation

Rules support an optional second evaluation stage for fields that require an extra API call:

1. **`expression`** (required) — evaluated against a `notification` object mirroring the GitHub API response (fields: `id`, `reason`, `unread`, `updated_at`, `subject.title`, `subject.type`, `repository.name`, `repository.full_name`). Cheap — no extra API calls.
2. **`subject_expression`** (optional) — evaluated only if `expression` matched. Fetches the full Issue/PullRequest object (cached under `${XDG_CACHE_HOME}/signalsmith/subjects/`, see [Caching](#caching)) and exposes it as `subject`, alongside `notification` still in scope. Both stages must pass for the rule to match.

Subject fields (see `src/signalsmith/github/models.py` for the full model):

- Common to Issues and PRs: `subject.state`, `subject.user.login`, `subject.assignees[]`, `subject.labels[]` (each with `.name`, `.color`)
- PR-specific: `subject.draft`, `subject.merged`, `subject.mergeable_state`, `subject.requested_reviewers[]`, `subject.requested_teams[]` (each with `.slug`, `.name`)

## Account Variables

Both `expression` and `subject_expression` also have an `account` object in scope, sourced from the GitHub API rather than hardcoded in config:

- `account.github.username` — the authenticated user's GitHub login (from `GET /user`, cached indefinitely under `${XDG_CACHE_HOME}/signalsmith/user.json` since it never changes)

Example — match assignees/reviewers without hardcoding a username:

```yaml
subject_expression: >
  account.github.username in
  (subject.assignees + subject.requested_reviewers|default([]))|map(attribute='login')
```

`requested_reviewers` isn't present on every subject type, hence
`|default([])` — see [Rule Expressions Are Jinja](#rule-expressions-are-jinja)
below for why a plain `is defined` guard doesn't work the same way here.

Example — only match PRs I'm not a direct reviewer/assignee on:

```yaml
- id: closed_pr_mark_as_read
  expression: 'notification.subject.type == "PullRequest"'
  subject_expression: 'subject.merged or subject.state == "closed"'
  action:
    mark_as_read: {}
```

## Variables

A top-level `variables:` block defines arbitrary data (lists, nested maps, whatever) that's exposed as a `variables` object in both `expression` and `subject_expression`. This lets you write generic rules once and keep the specific values (repo lists, bot names, team names) in one place instead of duplicating them across expressions.

```yaml
variables:
  offtopic:
    prefixes:
      - myorg/team-a-
    repos:
      - myorg/some-noisy-repo
  spam_bots:
    - dependabot[bot]
  spam_groups:
    - { org: myorg, id: some-team-slug }

rules:
  - id: off_topic_mark_as_read
    expression: >
      variables.get('offtopic', {}).get('prefixes', [])|select('startingwith', notification.repository.full_name)|first is defined
      or notification.repository.full_name in variables.get('offtopic', {}).get('repos', [])
    action:
      mark_as_read: {}

  - id: bot_pr_mark_as_read
    expression: 'notification.subject.type == "PullRequest"'
    subject_expression: 'subject.user.login in variables.spam_bots'
    action:
      mark_as_read: {}
```

`notification.repository.org` (derived from `full_name`, not part of the raw
GitHub API payload) is available directly in expressions — no need to
re-derive it from `full_name` yourself, e.g.
`notification.repository.org == g.org`.

## Testing Rules

Filter rules can be tested offline without making any GitHub API calls. See [Test File Format](./test-format.md) for detailed documentation on writing test cases.

## Actions

- **`notify`**: Desktop alert ✅, Marked as read ❌ — **"Alert me"**: Desktop notification, stays unread on GitHub for follow-up
- **`ignore`**: Desktop alert ❌, Marked as read ❌ — **"Don't alert, but keep unread"**: Silently ignore, stays unread on GitHub
- **`mark_as_read`**: Desktop alert ❌, Marked as read ✅ — **"Dismiss completely"**: Mark as read on GitHub, no alert

**Typical workflow:**
1. Start with `default_action: notify` (default) — everything alerts unless you write ignore rules
2. As spam emerges, add `ignore` rules for patterns you don't care about
3. Use `mark_as_read` when you want notifications completely dismissed from GitHub
4. Switch to `default_action: ignore` when you prefer a whitelist approach (only alert on matched rules)

## Notices and Templates

Every `notify` action has a `title` and a `body`. Rather than write those out
per rule, a top-level `notice:` block computes a generic notice for **every**
notification, and `notify` actions build on it:

```yaml
notice:
  title: '{{ notification.subject.type }}: {{ notification.subject.title }}'
  body: '{{ notification.repository.full_name }} ({{ notification.reason }})'

rules:
  - id: 'reviewer_or_assignee'
    expression: '...'
    action:
      notify: {}   # uses the generic notice verbatim

  - id: 'urgent'
    expression: '...'
    action:
      notify:
        title: '[urgent] {{ notice.title }}'
        # body omitted -> falls back to the rendered notice.body
```

`notice.title`/`notice.body` are both optional and independently default to
the built-in strings shown above. A `notify` action's `title`/`body` are also
optional and independently default to the rendered `notice.title`/
`notice.body` - so `notify: {}` is a complete, valid action.

Templates are [Jinja](https://jinja.palletsprojects.com/) - `{{ ... }}` for
values, `{% if %}`/`{% for %}` etc. for logic. Available names depend on which
template you're writing:

- **`notice.title`/`notice.body`**: `notification`, `subject` (only if some
  rule's `subject_expression` already fetched one, or a template here
  references `subject` and it's fetchable - see below), `account`, `variables`
  - the exact same objects `expression`/`subject_expression` see (above).
- **`notify.title`/`notify.body`**: all of the above, **plus `notice`**
  (`notice.title`, `notice.body`) - the already-rendered generic notice.

**On-demand subject fetch**: if a `notice`/`notify` template references
`subject` and no rule already fetched one while matching, signalsmith fetches
it before rendering (same cache as `subject_expression`). A config whose
templates never mention `subject` never pays for that extra fetch.

**Failure handling**: a template that fails to render (an undefined
reference, a typo) falls back rather than dropping the notification -
`notice.title`/`notice.body` fall back to the built-in default strings above;
`notify.title`/`notify.body` fall back to the rendered `notice`. Two cases:

- The subject genuinely isn't available (its type has nothing fetchable, e.g.
  `Release`, or the fetch failed) - logged at **warning**, since nothing is
  wrong with the config. Guard for this explicitly if you want different
  behavior: `{% if subject is defined %}{{ subject.user.login }}{% endif %}`.
- Any other template error (a typo, an undefined variable, bad syntax) -
  logged at **error**, since this is a genuine config problem worth fixing.

## Clicking and Interactive Notifications

`signalsmith daemon` sends notifications via [`desktop-notifier`](https://github.com/samschott/desktop-notifier), which supports real click callbacks - unlike `signalsmith run`, which always sends a plain, non-interactive notification and exits immediately after.

- **Click-to-open**: in `daemon` mode, clicking a notification opens `notification.subject.web_url` in the default browser, whenever a URL is available. This is unconditional - it's not gated by `notify_actions.enabled` below.
- **`notify_actions.enabled`**: adds "Dismiss" (marks the notification as read on GitHub, like the `mark_as_read` rule action) and "Ignore" (see below) buttons to every notification. Also `daemon`-only.
- **Permanent ignore**: the "Ignore" button records the subject in a durable store, consulted on **every** future run (both `run` and `daemon`, regardless of `notify_actions.enabled`) so that subject is never notified on again - distinct from the existing `ignore` rule action, which is transient and re-evaluated every run. Manage it with `signalsmith ignore list`/`remove`/`clear` (see [CLI Reference](./cli.md)).
- **`notify_actions.max_concurrent`/`wait_timeout`**: at most `max_concurrent` button-bearing notifications are ever in flight at once; as soon as one resolves (clicked, button pressed, or `wait_timeout` seconds elapse) the next queued one is sent. Plain click-to-open notifications (buttons disabled) aren't subject to this limit.
- **Why `daemon`-only**: `desktop-notifier`'s click/button callbacks only fire while its event loop is actively running. `signalsmith run` exits right after sending, so it can never observe a later interaction - `daemon`'s persistent background thread is what makes this work. `daemon --non-interactive` opts back out to the same plain, non-interactive send `run` always uses.
- **Auto-expiry caveat**: on Linux, a notification the user doesn't interact with is silently auto-expired by the notification server - no callback fires at all. This means an unactioned button-bearing notification occupies its `max_concurrent` slot for the full `wait_timeout`, not just until it visually disappears.
- **Platform support**: click/button callbacks are cross-platform in principle (Linux/D-Bus, macOS, Windows), but macOS may require a signed Python interpreter for notifications to work at all, and Windows toast notifications generally require a registered AppUserModelID via a Start Menu shortcut, which signalsmith doesn't provision - treat Windows support as best-effort/unverified.

## Authentication

Token is obtained in the following order:
1. `GITHUB_TOKEN` environment variable
2. `GH_TOKEN` environment variable
3. `gh auth token` command (if `gh` CLI is authenticated)

If none of these are available, the tool will exit with an error suggesting to run `gh auth login`.

## Caching

- **Version marker**: `${XDG_CACHE_HOME}/signalsmith/version.json` — see [Versioning](#versioning). Written automatically on first use; not user-editable.
- **Notification list**: `${XDG_CACHE_HOME}/signalsmith/notifications.json`. The GitHub API is queried with `If-Modified-Since` / `ETag` conditional requests; a `304 Not Modified` response reuses cached data without counting against quota. Passing `--cache-only` forces use of this file, making **zero** API calls for the notification list. Only written on unlimited (no `--limit`) fetches, so its ETag/Last-Modified metadata always corresponds to the full feed.
- **Subjects** (Issues/PRs fetched for `subject_expression` evaluation): `${XDG_CACHE_HOME}/signalsmith/subjects/api.github.com/repos/<owner>/<repo>/<issues|pulls>/<number>.json`. Considered fresh as long as the notification's `updated_at` hasn't advanced past the cache file's mtime; refetched otherwise.
- **Notification archive**: `${XDG_CACHE_HOME}/signalsmith/notifications-archive-<YYYYMMDDTHHZ>.jsonl` (one file per UTC hour) — every fetch result (regardless of `--limit`, `--cache-only`, or `--refresh-notifications`) is appended here, one JSON line per notification: `{"fetched_at": "<ISO-8601>", "notification": {...}}`. Written unconditionally and never read back by signalsmith. Each hourly file grows without bound within that hour (not deduplicated) — useful for inspecting real notification payloads while writing rule expressions, and for later analysis of what signalsmith has seen over time (e.g. with `jq`).

## Spool

Every notification that results in a `notify` action is written to a durable spool entry:

- **Version marker**: `${XDG_DATA_HOME}/signalsmith/version.json` — see [Versioning](#versioning). Lives at the state root above the spool itself (not inside `spool.dir`, even if that's overridden), so it also covers any future state store added alongside the spool. Written automatically on first use; not user-editable.
- **Live spool**: `${XDG_DATA_HOME}/signalsmith/spool/<provider>-<notification-id>.json` (e.g. `github-14523452.json`) — one JSON file per notified notification, containing the full notification, the fetched subject (if a rule's `subject_expression` fetched one; `null` otherwise), the matched rule (as a plain JSON snapshot, not tied to the current config schema), the rendered title/body, `received_at`/`last_notified_at`/`notify_count`, and a capped history of recent notify events. This also drives `renotify_interval`: the entry's `last_notified_at` replaces what used to be tracked in a separate `state.json`, so there's nothing else to keep in sync.
- Kept until the notification disappears from the provider's unread feed, at which point it's moved (not deleted) to the trash below. A notification `run`/`daemon` marks as read itself is *not* removed immediately — it lingers until the next full fetch confirms it's actually gone upstream.
- Configurable location: `spool.dir` (default shown above).
- Inspect with `signalsmith spool list`; reset with `signalsmith spool clean` (see [CLI Reference](./cli.md)).

- **Trash**: `${XDG_CACHE_HOME}/signalsmith/trash/spool/<provider>-<notification-id>-<timestamp>.json` — where reaped/cleaned spool entries go, timestamped so re-spooling and re-reaping the same notification never collides. Never read back by signalsmith; swept by `signalsmith cache clean` along with the rest of the cache directory.

Upgrading from a version that used `state.json`: that file is no longer read or written and can be deleted. Nothing seeds the new spool's `last_notified_at` from it, so expect one round of re-notifications for still-unread items on the first run after upgrading.

## Permanent Ignore Store

Subjects ignored via the "Ignore" button (see [Clicking and Interactive Notifications](#clicking-and-interactive-notifications)) are recorded here:

- **Location**: `${XDG_DATA_HOME}/signalsmith/ignored/<sanitized-subject-url>.json` - lives under the same state root as the spool, sharing its version marker (no separate version kind).
- Each entry records the subject's API URL (the key), when it was added, and its title/repository/type for display.
- Consulted on **every** run (`run` and `daemon` alike) before rule evaluation - an ignored subject never triggers a subject fetch or gets notified on again, regardless of `notify_actions.enabled`.
- Unlike the spool, entries here are permanent by design: there's no `reap`/trash lifecycle. `signalsmith ignore remove <subject-url>` or `signalsmith ignore clear` hard-delete.
- Inspect with `signalsmith ignore list` (see [CLI Reference](./cli.md)).

## Versioning

The config file, test files (see [Test File Format](./test-format.md)), the state directory, and the cache directory each carry their own independent `MAJOR.MINOR` schema version (unrelated to each other and to signalsmith's package version):

- A different major version is always incompatible, in either direction.
- The same major with a newer minor is compatible but warned about (this signalsmith version may not understand fields a newer minor added).
- A missing version is treated as `0.0`.

What happens on an incompatible version differs by kind:

- **Config/test files**: signalsmith refuses to run and tells you to update the file by hand — there is no auto-fix or migration.
- **State/cache directories**: signalsmith tells you to run `signalsmith state clean` / `signalsmith cache clean` and re-run; it does not clear them automatically. A newer-minor state directory additionally warns that writing to it may lose data added by the newer version (not a concern for the disposable cache).

A fresh (missing or empty) state/cache directory silently gets today's version marker written to it — no warning on first run.
