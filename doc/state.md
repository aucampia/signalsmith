# State Reference

State lives at `${XDG_DATA_HOME}/signalsmith/` and holds durable data that
survives between runs: the notification spool, permanent ignore store, and
outcome history. All three stores share a single `version.json` marker at the
state root.

## Versioning

A `version.json` marker at `${XDG_DATA_HOME}/signalsmith/version.json`
carries the state directory's schema version (current: `'2.1'`). Written
automatically on first use; not user-editable.

Compatibility rules (same MAJOR.MINOR scheme as config and test files):

- Different major → refuses to run; tells you to run
  `signalsmith state clean` and re-run. Signalsmith does **not** clear state
  automatically.
- Same major, newer minor → compatible but warns that writing to it may lose
  data added by the newer version.
- Missing/empty directory → `version.json` is written silently on first run.

Run `signalsmith state clean` to recover from a version mismatch: it moves
all spool entries to trash, then removes the entire state directory. The next
run recreates it with the current version.

## Spool

Every notification that results in a `notify` action is written to a durable
spool entry:

- **Location**: `spool.dir` in config, defaults to
  `${XDG_DATA_HOME}/signalsmith/spool/`. One JSON file per notified
  notification: `<provider>-<notification-id>.json` (e.g.
  `github-14523452.json`).
- **Contents**: the full notification, the fetched subject (or `null`), the
  matched rule (JSON snapshot), the rendered title/body,
  `received_at`/`last_notified_at`/`notify_count`, and a capped history of
  recent notify events.
- **Renotification**: the entry's `last_notified_at` drives
  `renotify_interval` — replaced what used to be tracked in a separate
  `state.json`.
- **Lifecycle**: entries are kept until the notification disappears from the
  provider's unread feed, then moved (not deleted) to trash. A notification
  that `run`/`daemon` marks as read itself is *not* removed immediately — it
  lingers until the next fetch confirms it's gone upstream.
- **Trash**:
  `${XDG_CACHE_HOME}/signalsmith/trash/spool/<provider>-<notification-id>-<timestamp>.json`
  — timestamped so re-spooling and re-reaping never collide. Never read back by
  signalsmith; swept by `signalsmith cache clean`.
- **Inspect**: `signalsmith spool list` (see [CLI Reference](./cli.md)).
- **Reset**: `signalsmith spool clean` (see [CLI Reference](./cli.md)).

## Permanent Ignore Store

Subjects ignored via the interactive Ignore button are recorded here:

- **Location**:
  `${XDG_DATA_HOME}/signalsmith/ignored/<sanitized-subject-url>.json` — under
  the same state root as the spool, sharing its version marker.
- Each entry records the subject's API URL, when it was added, and its
  title/repository/type for display.
- Consulted on **every** run (`run` and `daemon` alike) before rule
  evaluation — an ignored subject never triggers a subject fetch or gets
  notified on again.
- Permanent by design: there is no `reap`/trash lifecycle.
  `signalsmith ignore remove <subject-url>` or `signalsmith ignore clear`
  hard-delete.
- **Inspect**: `signalsmith ignore list` (see [CLI Reference](./cli.md)).

## History Store

Every notification that flows through the pipeline is recorded as a JSON
file, overwritten on each re-processing so only the most recent outcome per
notification is retained:

- **Location**:
  `${XDG_DATA_HOME}/signalsmith/history/<provider>-<notification-id>.json`.
- **Contents**: provider, notification_id, `recorded_at`, `outcome` (e.g.
  `notified`, `ignored`, `marked_as_read`, `skipped`), rule_id, the
  notification payload, rendered title/body (for notified outcomes), and the
  fetched subject (if available).
- **Inspect**: `signalsmith history list` with `--action` filter, `--limit`,
  and `--json` flag (see [CLI Reference](./cli.md)).
