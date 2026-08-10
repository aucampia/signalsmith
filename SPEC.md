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
