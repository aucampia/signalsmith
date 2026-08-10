# signalsmith

GitHub notifications manager with desktop alerts and CEL-based filtering.

## Features

- **Desktop notifications** for filtered GitHub notifications (cross-platform via `desktop-notifier`); `signalsmith daemon` additionally supports clicking a notification to open its subject in a browser, and optional Dismiss/Ignore action buttons
- **Powerful CEL filtering** with two-stage evaluation (notification + subject fields)
- **Organization filtering** with include/exclude masks
- **Flexible actions**: notify, mark-as-read, or custom combinations
- **Reusable action definitions** for DRY configuration
- **Smart caching** with conditional GitHub API requests (ETags, If-Modified-Since)
- **Durable spool** of notified notifications (also drives renotify intervals) until they're read upstream
- **Daemon mode** for continuous polling

## Installation

```bash
uv tool install signalsmith
```

For development installation, see [CONTRIBUTING.md](./CONTRIBUTING.md).

> **Upgrading from a version predating the notification spool?**
> `${XDG_DATA_HOME}/signalsmith/state.json` is no longer used and can be deleted.
> Renotify timestamps now live in the spool instead, so expect one round of
> re-notifications for anything still unread on your first run after
> upgrading. See [doc/config.md](./doc/config.md#spool).

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

Polls every 300 seconds (5 minutes) by default, or uses `poll_interval` from config.
This is a lower bound — if GitHub's response includes an `X-Poll-Interval` header
asking for a longer wait, signalsmith sleeps for that instead.

## Configuration

Create `~/.config/signalsmith/config.yaml` (or set `SIGNALSMITH_CONFIG` to a custom path):

```yaml
version: '1.1'  # config file schema version (see doc/config.md#versioning)

poll_interval: 300  # seconds

masks:
  orgs:
    include:
      - my-organization
    exclude:
      - spam-org

rules:
  - id: important-mentions
    expression: |
      notification.reason == "mention" &&
      notification.subject.type == "PullRequest"
    subject_expression: |
      !subject.draft
    action:
      notify:
        title: "PR Mention: ${notification.subject.title}"
        message: "${notification.repository.full_name}"

  - id: review-requests
    expression: |
      notification.reason == "review_requested"
    action:
      notify:
        title: "Review Request"
        message: "${notification.subject.title}"
```

### Example Configurations

See `src/signalsmith/` for detailed examples:

- **[example-config.yaml](src/signalsmith/example-config.yaml)** - Full feature showcase with comments
- **[example-config-with-actions.yaml](src/signalsmith/example-config-with-actions.yaml)** - Reusable action definitions
- **[config-example-orgs.yaml](src/signalsmith/config-example-orgs.yaml)** - Organization filtering patterns

## Testing Your Rules

Write YAML test cases in a `tests/` directory (default: `<config dir>/tests`,
independent of where the config file itself lives — see
[doc/test-format.md](./doc/test-format.md)), and run them offline
(no GitHub API calls, no token) with `signalsmith test`:

```yaml
# ~/.config/signalsmith/tests/spam-bots.yaml
version: '1.0'  # test file schema version (see doc/config.md#versioning)
cases:
  - name: bot PRs are marked as read
    parameters: ${variables.spam_bots}  # every bot login from your config's variables:
    input:
      notification:
        subject: { type: PullRequest }
      subject:
        user:
          login: ${parameter}
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

signalsmith uses [CEL (Common Expression Language)](https://github.com/google/cel-spec) for filtering.

### Two-Stage Rule Evaluation

1. **Notification expression**: Evaluated against the notification object (fast, cached)
2. **Subject expression** (optional): Evaluated against the fetched Issue/PR (requires API call)

This allows efficient filtering without fetching every subject from the API.

### Available Fields

See [SPEC.md](./SPEC.md) for complete field reference.

**Notification fields:**
- `notification.id`, `notification.reason`, `notification.unread`, `notification.updated_at`
- `notification.subject.title`, `notification.subject.type`
- `notification.repository.name`, `notification.repository.full_name`

**Subject fields** (Issues/PRs):
- `subject.state`, `subject.user.login`, `subject.labels[]`, `subject.assignees[]`
- PR-specific: `subject.draft`, `subject.mergeable_state`, `subject.requested_reviewers[]`

### Example Rules

```yaml
# Unread PR review requests
expression: |
  notification.unread &&
  notification.reason == "review_requested" &&
  notification.subject.type == "PullRequest"

# Non-draft PRs mentioning you
expression: notification.reason == "mention"
subject_expression: !subject.draft

# Issues with specific labels
expression: notification.subject.type == "Issue"
subject_expression: |
  subject.labels.exists(l, l.name == "bug")
```

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development workflow and project structure.

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
- **[doc/config.md](./doc/config.md)** - Configuration reference
- **[doc/test-format.md](./doc/test-format.md)** - Test file format
- **[doc/cli.md](./doc/cli.md)** - CLI reference
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** - Development guide

## License

UNLICENSED
