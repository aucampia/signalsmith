# Cache Reference

The cache lives at `${XDG_CACHE_HOME}/signalsmith/` and is disposable — all of
it can be regenerated from the GitHub API. `signalsmith cache clean` removes
the entire tree.

## Versioning

A `version.json` marker at `${XDG_CACHE_HOME}/signalsmith/version.json`
carries the cache directory's schema version (current: `'2.0'`). Written
automatically on first use; not user-editable.

Compatibility rules (same MAJOR.MINOR scheme as config and test files):

- Different major → refuses to run; tells you to run
  `signalsmith cache clean` and re-run. Signalsmith does **not** clear the
  cache automatically.
- Same major, newer minor → compatible without warning (cache is disposable,
  no data-loss concern).
- Missing/empty directory → `version.json` is written silently on first run.

## Notification List

`${XDG_CACHE_HOME}/signalsmith/notifications.json` caches the full
notification list from the GitHub API.

- Queried with `If-Modified-Since` / `ETag` conditional requests; a
  `304 Not Modified` response reuses cached data without counting against
  rate limits.
- `--cache-only` forces use of this file, making **zero** API calls for the
  notification list.
- Only written on unlimited (no `--limit`) fetches, so its
  ETag/Last-Modified metadata always corresponds to the full feed.

## Subjects

`${XDG_CACHE_HOME}/signalsmith/subjects/<host>/repos/<owner>/<repo>/<issues|pulls>/<number>.json`
caches per-subject (Issue/PR) data fetched for rule expressions.

- Considered fresh as long as the notification's `updated_at` hasn't advanced
  past the cache file's mtime; refetched otherwise.

## Authenticated User

`${XDG_CACHE_HOME}/signalsmith/user.json` caches the authenticated GitHub
user from `GET /user`. Since it never changes, it's cached indefinitely.

## Notification Archive

`${XDG_CACHE_HOME}/signalsmith/notifications-archive-<YYYYMMDDT<HH>Z>.jsonl`
(one file per UTC hour) — every fetch result is appended here, regardless
of `--limit`, `--cache-only`, or `--refresh-notifications`.

- One JSON line per notification: `{"fetched_at": "<ISO-8601>",
  "notification": {...}}`.
- Written unconditionally and never read back by signalsmith — useful for
  inspecting real notification payloads while writing rule expressions or for
  later analysis (e.g. with `jq`).

## Spool Trash

`${XDG_CACHE_HOME}/signalsmith/trash/spool/` holds spool entries moved by
`reap` or `clear` (see [State Reference](./state.md#spool)). Never read back
by signalsmith; swept by `signalsmith cache clean`.
