# CLI Reference

```
signalsmith  run | daemon | test | cache clean | spool list | spool clean | state clean | ignore list | ignore remove | ignore clear
```

Every command accepts its own `--verbose`/`-v` (there is no global flag
before the command name).

## `run`

Run one poll cycle and exit. Prints a run summary (see [Run Statistics](#run-statistics)) and, unless `--dry-run`, spools every notified notification and reaps spool entries no longer in the unread feed before exiting.

**Options:**
- `--cache-only`: Use cached notification list only; make no API call for the notification list. Subjects (Issues/PRs) are still fetched from the API as needed. Mutually exclusive with `--refresh-notifications`.
- `--force`: Re-notify even if `renotify_interval` has not elapsed.
- `--dry-run`: Print to stdout what would happen; make no mark-as-read calls, no desktop notifications, no state changes. Each action is prefixed with `[DRY RUN]` for clarity.
- `--limit N`: Process at most N notifications (useful for testing or limiting API usage).
- `--dump-json`: Dump notifications and fetched subjects as formatted JSON to stdout in addition to normal processing (useful for writing rule expressions).
- `--refresh-notifications`: Bypass the notification list cache and refetch from the API even if unchanged. Subjects are cached separately and unaffected. Mutually exclusive with `--cache-only`.

## `daemon`

Run continuously, polling on a configurable interval. Unlike `run`, `daemon` sends notifications interactively by default (see [Clicking and Interactive Notifications](./config.md#clicking-and-interactive-notifications)): clicking a notification opens its subject in a browser, and Dismiss (always) and Ignore (when a subject URL is available) buttons are added.

**Options:**
- `--poll-interval SECONDS`: Override `poll_interval` from config (default: from config file).
- `--non-interactive`: Send plain, non-interactive notifications - the exact same code path `run` always uses, with no click-to-open, dismiss, or ignore support. Also the automatic fallback if the interactive notifier fails to start (e.g. no D-Bus session available), logged once.

## `test`

Run the offline rule tests described in [Test File Format](./test-format.md) and exit non-zero if any case fails. Makes no GitHub API calls.

- `--tests-dir PATH`: Directory of test files (default: `SIGNALSMITH_TEST_DIR` env var, or `<config dir>/tests` — see [test-format.md](./test-format.md)).
- `-k TEXT`: Only run cases whose name contains this substring.

## `cache clean`

Remove the local cache directory (`${XDG_CACHE_HOME}/signalsmith/`), including the version marker, the notification list cache, the subject cache, the notification archive, and the spool trash (`trash/spool/`). The live spool itself lives under `${XDG_DATA_HOME}/signalsmith/` and is unaffected.

## `spool list`

List notifications currently held in the spool (see [Spool](./config.md#spool)), sorted by when they were first received.

- `--json`: Dump full spool entries as JSON instead of the one-line-per-entry summary.

## `spool clean`

Move every spool entry to the spool trash. Since the spool also tracks `renotify_interval`, this resets renotify suppression — the next run re-notifies on everything still unread. Unlike `state clean`, the state directory's version marker is left in place.

## `state clean`

Move every spool entry to trash (same as `spool clean`) and additionally remove the whole state directory (`${XDG_DATA_HOME}/signalsmith/`), including its version marker. This is the recovery command for a state version mismatch (see [Versioning](./config.md#versioning)) — the next run recreates the directory and stamps it with the current version.

**Note:** `state clean`, `spool clean`, and `cache clean` all deliberately skip the version compatibility check, since they're the commands used to *fix* a version mismatch — they must work even when the directory they're clearing has an incompatible or corrupt version marker.

## `ignore list`

List subjects currently held in the permanent-ignore store (see [Permanent Ignore Store](./config.md#permanent-ignore-store)).

- `--json`: Dump full entries as JSON instead of the one-line-per-entry summary.

## `ignore remove`

Remove a single subject from the permanent-ignore store, given its subject URL (as shown by `ignore list`). The subject will be notified on again on the next run if a rule (or the default action) would otherwise match it.

## `ignore clear`

Remove every entry from the permanent-ignore store.

## Run Statistics

After each run (both `run` and `daemon`), a summary is printed (via stdout for `run`, via logs for `daemon`):

- **Summary line**: counts of `found`, `notified`, `ignored`, `marked_as_read`, `skipped`, `permanently_ignored` (`skipped` = matched a `notify` rule but the renotify interval hadn't elapsed; `permanently_ignored` = subject was recorded via the "Ignore" button on a prior run, see [Permanent Ignore Store](./config.md#permanent-ignore-store)).
- **Breakdown**: notification counts by organization, by top repositories, by reason (`mention`, `assign`, `review_requested`, etc.), and by top PR/Issue creators (only populated for notifications where a subject was actually fetched).

## Rate Limiting

When `X-RateLimit-Remaining` reaches 0 the client sleeps until the `X-RateLimit-Reset` Unix timestamp before retrying.
