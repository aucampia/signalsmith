# signalsmith

GitHub notifications manager with desktop alerts and Jinja-based filtering.

## Features

- **Desktop notifications** for filtered GitHub notifications
  (cross-platform via `desktop-notifier`); `signalsmith daemon`
  additionally supports clicking a notification to open its subject in
  a browser, and optional Dismiss/Ignore action buttons
- **Powerful Jinja filtering** with two-stage evaluation (notification +
  subject fields)
- **Organization filtering** with include/exclude masks
- **Flexible actions**: notify, mark-as-read, or custom combinations
- **Reusable action definitions** for DRY configuration
- **Smart caching** with conditional GitHub API requests (ETags,
  If-Modified-Since)
- **Durable spool** of notified notifications (also drives renotify
  intervals) until they're read upstream
- **Daemon mode** for continuous polling

## Installation

Not yet published to PyPI - install straight from GitHub:

```bash
uv tool install git+https://github.com/aucampia/signalsmith
```

For development installation, see [CONTRIBUTING.md](./CONTRIBUTING.md).

> **Upgrading from a version predating the notification spool?**
> `${XDG_DATA_HOME}/signalsmith/state.json` is no longer used and can be
> deleted. Renotify timestamps now live in the spool instead, so expect one
> round of re-notifications for anything still unread on your first run after
> upgrading. See [doc/state.md](./doc/state.md).

## Quick Start

### Single Poll

Run once and exit:

```bash
signalsmith run
```

Options:

- `--verbose` / `-v` - Debug logging
- `--cache-only` - Use cached notifications (no API call)
- `--force` - Ignore renotify intervals
- `--dry-run` - Show what would happen without executing actions
- `--limit N` - Process only first N notifications
- `--dump-json` - Print notifications/subjects as JSON for debugging filters

### Daemon Mode

Run continuously with periodic polling:

```bash
signalsmith daemon --poll-interval 300
```

Polls every 300 seconds (5 minutes) by default, or uses `poll_interval` from
config. This is a lower bound — if GitHub's response includes an
`X-Poll-Interval` header asking for a longer wait, signalsmith sleeps for that
instead.

## Configuration

Create `~/.config/signalsmith/config.yaml` (or set `SIGNALSMITH_CONFIG`
to a custom path):

```yaml
version: '4.0'  # config file schema version (see doc/config.md#versioning)

poll_interval: 300  # seconds

masks:
  orgs:
    include:
      - my-organization
    exclude:
      - spam-org

# Generic notice computed for every notification - see
# doc/config.md#notices-and-templates. `notify: {}` below uses it verbatim.
notice:
  title: "{{ notification.subject.type }}: {{ notification.subject.title }}"
  body: "{{ notification.repository.full_name }} ({{ notification.reason }})"

rules:
  - id: important-mentions
    expression: |
      notification.reason == "mention" and
      notification.subject.type == "PullRequest" and
      (not subject.draft)
    action:
      notify:
        title: "PR Mention: {{ notification.subject.title }}"
        body: "{{ notification.repository.full_name }}"

  - id: review-requests
    expression: |
      notification.reason == "review_requested"
    action:
      notify: {}
```

### Example Configurations

See [`examples/config.yaml`](examples/config.yaml) for a complete working
example with:

- On-topic and off-topic repository filtering
- Bot PR auto-dismissal
- Team-based spam filtering
- Direct assignee/reviewer notifications

The [`examples/tests/`](examples/tests/) directory contains comprehensive rule
tests for the example config. You can run them with:

```bash
SIGNALSMITH_CONFIG_DIR=examples signalsmith test
```

## Testing Your Rules

Write YAML test cases in a `tests/` directory (default: `<config dir>/tests`,
independent of where the config file itself lives — see
[doc/test-format.md](./doc/test-format.md)), and run them offline
(no GitHub API calls, no token) with `signalsmith test`:

```yaml
# ~/.config/signalsmith/tests/spam-bots.yaml
version: '2.1'  # test file schema version (see doc/config.md#versioning)
cases:
  - name: bot PRs are marked as read
    parameters: '{{ variables.spam_bots }}'  # every bot login from your config's variables:
    input:
      notification:
        subject: { type: PullRequest }
      subject:
        user:
          login: '{{ parameter }}'
    expect:
      rule: bot_pr_mark_as_read
      action: mark_as_read
```

```bash
signalsmith test
```

See [doc/test-format.md](./doc/test-format.md) for the full test file schema.

## Authentication

signalsmith needs a GitHub token. It will look for:

1. `GITHUB_TOKEN` environment variable
2. `GH_TOKEN` environment variable
3. `gh auth token` (GitHub CLI)

Set up auth with:

```bash
gh auth login
```

Or set an environment variable:

```bash
export GITHUB_TOKEN=ghp_...
```

## Rule Expressions

signalsmith uses [Jinja](https://jinja.palletsprojects.com/) expressions for
filtering - the same engine `notice`/`notify` templates use, evaluated to a
real value rather than rendered to text. See
[Rule Expression Reference](./doc/rules.md) for the full
picture, including `StrictUndefined` behavior and the filters that replace
CEL-style macros like `exists`/`all`.

### Two-Stage Rule Evaluation

1. **Notification expression**: Evaluated against the notification object
   (fast, cached)
2. **Subject expression** (optional): Evaluated against the fetched
   Issue/PR (requires API call)

This allows efficient filtering without fetching every subject from the API.

### Available Objects

Rule expressions and templates have access to four objects:
`notification` (GitHub API notification, always available), `subject` (full
Issue/PR, fetched on demand), `account` (`{github: {username: "..."}}`),
and `variables` (your user-defined `variables:` block).

See [Object Reference](./doc/objects.md) for the complete field reference.

### Notice and Notify Templates

A top-level `notice:` block computes a generic title/body per notification.
`notify: {}` uses it verbatim; `notify: {title: '...'}` overrides individual
fields, with `{{ notice.title }}`/`{{ notice.body }}` available in scope.

See [Template Reference](./doc/templates.md) for template context variables
and failure handling.

### Example Rules

```yaml
# Unread PR review requests
expression: |
  notification.unread and
  notification.reason == "review_requested" and
  notification.subject.type == "PullRequest"

# Non-draft PRs mentioning you
expression: |
  notification.reason == "mention"
  and notification.subject.type == "PullRequest"
  and (not subject.draft)

# Issues with specific labels
expression: |
  notification.subject.type == "Issue"
  and (subject.labels|selectattr('name', 'eq', 'bug')|first is defined)
```

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development workflow and project
structure.

Quick commands:

```bash
task configure       # Install dependencies
task validate:fix    # Auto-fix formatting
task validate:static # Type checking, linting
task test            # Run tests
task validate        # Full validation pipeline
task cli             # Run CLI locally
```

## Documentation

- **[SPEC.md](./SPEC.md)** - Technical specification and architecture
- **[doc/config.md](./doc/config.md)** — Configuration file format,
  rules, notice templates, authentication, versioning
- **[doc/objects.md](./doc/objects.md)** — Field reference for
  `notification`, `subject`, `account`, `variables`
- **[doc/rules.md](./doc/rules.md)** — Rule expression reference
  (operators, idioms, lazy subject)
- **[doc/templates.md](./doc/templates.md)** — `notice`/`notify` template
  reference (context variables, failure handling)
- **[doc/actions.md](./doc/actions.md)** — Action reference (notify,
  ignore, mark_as_read)
- **[doc/state.md](./doc/state.md)** — State directory reference (spool,
  permanent ignore store, history, versioning)
- **[doc/cache.md](./doc/cache.md)** — Cache directory reference
  (layout, conditional requests, archive, versioning)
- **[doc/test-format.md](./doc/test-format.md)** — Test file format
- **[doc/cli.md](./doc/cli.md)** - CLI reference
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** - Development guide

## License

This project is dedicated to the public domain under
[CC0 1.0 Universal](./LICENSE).
