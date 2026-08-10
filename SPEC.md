# signalsmith — Notification Manager

## Problem

GitHub notifications (and eventually other platforms like Jira) have no built-in filtering
mechanisms. This results in notification spam drowning out the alerts that actually matter.

## Solution

`signalsmith` provides a programmable filtering layer between notification sources and your
attention:

1. **Pull** unread notifications from the source (currently GitHub)
2. **Filter** using CEL expression rules to classify each notification
3. **Auto-dismiss** (mark as read) notifications you don't care about
4. **Alert** only on notifications that pass your filters

Every notification that triggers an alert is spooled to disk durably until it's
read upstream (superseding the old `state.json`, which only tracked
renotify timestamps) — see [doc/config.md](./doc/config.md#spool).

The typical workflow is iterative refinement:
- Start with minimal rules, see what gets through
- Add ignore rules as spam patterns emerge
- Tune or remove rules when you miss something important
- Converge toward a zero-noise notification stream over time

### Current Support

- **GitHub** notifications (via GitHub API)

### Future Support

- Jira (planned)
- Other notification sources as needed

## Configuration

`signalsmith` uses a YAML configuration file to define:
- Filter rules (CEL expressions evaluated in order)
- Actions (notify, ignore, mark_as_read)
- Variables (reusable data for expressions)
- Organization masks and polling intervals

See [doc/config.md](./doc/config.md) for the complete configuration reference.

## CLI

`signalsmith` provides commands for running one-time polls, continuous daemon mode, offline testing of rules, and cache management.

See [doc/cli.md](./doc/cli.md) for the complete CLI reference.

## Architecture

Three layers, each usable (and testable) independently of the one above it:

- **`cli`** (`src/signalsmith/cli.py`) — Typer commands: parse flags, call
  into `app`, format output. Holds no pipeline logic itself.
- **`app`** (`src/signalsmith/app/`) — orchestration shared by the CLI
  commands: `build_app_context` wires up config/provider/spool/ignore-store
  (`AppContext`), `process_cycle` runs one create-actions/execute/reap pass,
  `run_daemon` is the poll loop `daemon` drives, `auth` resolves a GitHub
  token. Nothing here depends on Typer, so it can be driven from a test, a
  script, or eventually a different frontend without going through the CLI.
- **Domain modules** — `processor` (fetch → org-mask → rule-match → action,
  see `cel_rules.RuleMatcher`), `actions` (turns a matched rule into an
  executable `Action`, rendering its notice via `templating` along the way),
  `templating` (Jinja rendering of the top-level `notice:` block and a
  `notify` action's `title`/`body` overrides), `state` (durable spool +
  permanent-ignore store), `github` (the current `NotificationProvider`
  implementation), `config` (the YAML schema and its offline test harness).

Notice/notify rendering happens in `actions.registry._build_notify`, at
action-construction time inside `processor.create_actions` - not lazily in
`Action.execute()`. This is what lets `--dry-run` show the actual rendered
title/body rather than raw notification fields, and what lets a `notify`
template fetch a subject on demand (via `templating.template_names`) even
when the matched rule's own `subject_expression` didn't need one.

### Extension point: adding an action kind

Configurable action kinds (`notify`, `mark_as_read`, `ignore` today) are
driven by a single table, `actions.registry.ACTION_SPECS`, keyed by
`config.models.ActionKind`. Adding a new kind (see `zxxi-TODO.md`:
unsubscribe, assign-to-self, add-as-reviewer) means:

1. Add the value to `ActionKind` and a matching field to
   `ActionDefinition`/`RuleAction` (`config/models.py`).
2. Add the action class (`actions/<kind>.py`) and its `ACTION_SPECS` entry
   (`actions/registry.py`).

`tests/actions/test_registry.py` is parametrized over `ActionKind` and fails
if either step is missed, rather than only surfacing as a runtime error deep
in `actions.factory.resolve_action_config`.

### Known gap before a second provider (e.g. Jira)

`protocols.NotificationProvider` is hard-typed to GitHub's notification/
subject models (`GitHubNotification`, `GitHubIssue | GitHubPullRequest`)
rather than generic over them. Adding a second provider needs that
genericized first - not done as part of the `cli`/`actions` layering
refactor, since no second provider exists yet to validate the abstraction
against.
