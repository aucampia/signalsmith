# Configuration Reference

## Configuration File

Config file location (checked in order):

1. `SIGNALSMITH_CONFIG` environment variable (if set)
2. `<config dir>/config.yaml`, where `<config dir>` is (checked in order):
   1. `SIGNALSMITH_CONFIG_DIR` environment variable (if set)
   2. `${XDG_CONFIG_HOME}/signalsmith` (default, typically
      `~/.config/signalsmith`)

Setting `SIGNALSMITH_CONFIG` alone only moves the config file — it has no
effect on the test directory (see [test-format.md](./test-format.md)), which
resolves independently via `SIGNALSMITH_TEST_DIR`/`SIGNALSMITH_CONFIG_DIR`.

## Configuration Structure

```yaml
# Config file schema version (see Versioning below). Required.
version: '4.0'

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
    expression: >
      notification.subject.type == "Issue"
      and (account.github.username in subject.assignees|map(attribute='login'))
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
```

## Rules

- Rules are evaluated **in order**; the first matching rule wins.
- **Default behavior**: If no rule matches, uses the configured
  `default_action` (default: `notify`).
  - `default_action: notify` (default) — Desktop notification for
    unmatched items
  - `default_action: ignore` — Silent ignore for unmatched items (stays
    unread, no alert)
- Each rule's `id` must be unique within the config.
- A rule's `action` is either inline
  (`notify`/`mark_as_read`/`ignore`, exactly one) or a `ref` pointing at
  a name defined in the top-level `actions:` block — never both.

## Rule Expressions Are Jinja

For the complete field reference on `notification`, `subject`, `account`, and
`variables`, see [Object Reference](./objects.md). For Jinja operators,
filters, idioms, and lazy subject access, see
[Rule Expression Reference](./rules.md).

## Lazy Subject Access

`subject` in rule expressions is a lazy proxy, fetched only on first attribute
access. `and`/`or` short-circuit, so cheap `notification.*` checks on the left
gate the expensive API call. See
[Rule Expression Reference](./rules.md#lazy-subject-access) for the full
explanation and good/bad examples.

## Account Variables

`account.github.username` is the authenticated user's GitHub login, sourced
from `GET /user` (not hardcoded). See [Object Reference](./objects.md#account).

## Variables

A top-level `variables:` block defines arbitrary data exposed as a `variables`
object in expressions and templates. See
[Object Reference](./objects.md#variables) for safe access patterns.

```yaml
variables:
  offtopic:
    prefixes:
      - myorg/team-a-
    repos:
      - myorg/some-noisy-repo
  spam_bots:
    - dependabot[bot]

rules:
  - id: off_topic_mark_as_read
    expression: >
      variables.get('offtopic', {}).get('prefixes', [])|select('startingwith', notification.repository.full_name)|first is defined
      or notification.repository.full_name in variables.get('offtopic', {}).get('repos', [])
    action:
      mark_as_read: {}
```

`notification.repository.org` (derived from `full_name`, not part of the raw
GitHub API payload) is available directly in expressions — no need to
re-derive it from `full_name` yourself, e.g.
`notification.repository.org == g.org`.

## Testing Rules

Filter rules can be tested offline without making any GitHub API calls.
See [Test File Format](./test-format.md) for detailed documentation on
writing test cases.

## Actions

Three action types: `notify` (desktop alert), `ignore` (silent), and
`mark_as_read` (dismiss on GitHub). See [Action Reference](./actions.md)
for parameters, outcomes, interactive daemon buttons, and reusable actions.

## Notices and Templates

The top-level `notice:` block computes a generic title/body once per
notification. `notify` actions use it verbatim (`notify: {}`) or override
individual fields (`notify: {title: '...'}`). The rendered `notice` is
available as `{{ notice.title }}`/`{{ notice.body }}` in notify templates.

Which objects are available in each template context, the on-demand subject
fetch, and failure handling are documented in
[Template Reference](./templates.md).

## Clicking and Interactive Notifications

Only in `signalsmith daemon` (not `run`). Each notification gets **Dismiss**
(mark as read) and **Ignore** (permanent ignore) buttons.
`daemon --non-interactive` opts out. See
[Action Reference](./actions.md#interactive-daemon-buttons) for concurrency,
platform caveats, and the permanent ignore store.

## Authentication

Token is obtained in the following order:

1. `GITHUB_TOKEN` environment variable
2. `GH_TOKEN` environment variable
3. `gh auth token` command (if `gh` CLI is authenticated)

If none of these are available, the tool will exit with an error
suggesting to run `gh auth login`.

## Caching

Notifications, subjects, and the authenticated user are cached on disk to
avoid redundant API calls. See [Cache Reference](./cache.md) for the full
directory layout, conditional request behavior, and the notification archive.

## State (Spool, Permanent Ignore, History)

Notifications that result in a `notify` action are written to a durable spool.
Subjects explicitly ignored via the interactive Ignore button are recorded
permanently. Every notification outcome is logged to a history store.

See [State Reference](./state.md) for the spool lifecycle, permanent ignore
store, history store, and state versioning.

## Versioning

The config file, test files (see [Test File Format](./test-format.md)), the
state directory (see [State Reference](./state.md#versioning)), and the cache
directory (see [Cache Reference](./cache.md#versioning)) each carry their own
independent `MAJOR.MINOR` schema version (unrelated to each other and to
signalsmith's package version):

- A different major version is always incompatible, in either direction.
- The same major with a newer minor is compatible but warned about (this
  signalsmith version may not understand fields a newer minor added).
- A missing version is treated as `0.0`.

What happens on an incompatible version differs by kind:

- **Config/test files**: signalsmith refuses to run and tells you to update
  the file by hand — there is no auto-fix or migration.
- **State/cache directories**: signalsmith tells you to run
  `signalsmith state clean` / `signalsmith cache clean` and re-run; it does
  not clear them automatically.

### What changed from 4.0

Version 5.0 removes the `notify_actions.enabled` field. Dismiss and Ignore
buttons are now always present in `signalsmith daemon` (interactive) mode —
there is no flag to suppress them. `notify_actions` still accepts
`max_concurrent` / `wait_timeout`.

**Migration**: Delete `enabled:` from the `notify_actions` block and bump
`version:` to `'5.0'`:

```yaml
# Before (4.0):
notify_actions:
  enabled: true
  max_concurrent: 5
  wait_timeout: 20

# After (5.0):
notify_actions:
  max_concurrent: 5
  wait_timeout: 20
```

A leftover `enabled:` line with `version: '5.0'` will produce a
`ValidationError` since unknown keys are hard errors (introduced in 4.0).
