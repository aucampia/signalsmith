# Action Reference

Three action types determine what happens when a rule matches (or when no rule
matches and `default_action` kicks in). Exactly one kind per action: inline
(`notify:`, `mark_as_read:`, `ignore:`) or a `ref:` to a named definition in
the top-level `actions:` block.

## `notify`

Desktop notification.

- `title: str | None` — Jinja template, optional. Falls back to
  `notice.title` when unset. See [Template Reference](./templates.md) for
  available context objects.
- `body: str | None` — Jinja template, optional. Falls back to
  `notice.body` when unset.
- Both `title` and `body` can be omitted independently.
- `notify: {}` is a complete, valid action — uses the generic notice verbatim.

In `signalsmith daemon` (not `run`, not `daemon --non-interactive`), each
notification gets **Dismiss** (marks as read) and **Ignore** (permanent ignore
store) buttons. Clicking opens `subject.web_url` in the browser.

May degrade to a skip if `renotify_interval` hasn't elapsed since the last
alert for this notification.

- **Outcome**: `NOTIFIED` | `SKIPPED`

## `ignore`

Silent ignore. No parameters (`ignore: {}`).

No API call, no desktop notification, the notification stays unread on GitHub.

- **Outcome**: `IGNORED`

## `mark_as_read`

Mark as read on GitHub. No parameters (`mark_as_read: {}`).

Calls the GitHub API to mark the notification as read. No desktop notification.

- **Outcome**: `MARKED_AS_READ`

## Reusable actions

Define once in the top-level `actions:` block and reference by name:

```yaml
actions:
  notify_default:
    notify: {}
  dismiss_ci:
    mark_as_read: {}

rules:
  - id: 'review_requested'
    expression: 'notification.reason == "review_requested"'
    action:
      ref: notify_default

  - id: 'ci_spam'
    expression: 'notification.reason == "ci_activity"'
    action:
      ref: dismiss_ci
```

## Interactive daemon buttons

Only active in `signalsmith daemon` (not `run`). `daemon --non-interactive`
opts back out.

- **Dismiss** — marks the notification as read on GitHub (same effect as the
  `mark_as_read` rule action).
- **Ignore** — records the subject in the permanent ignore store (distinct from
  the `ignore` rule action, which is transient). Ignored subjects are skipped on
  every future run. Managed via `signalsmith ignore list|remove|clear` — see
  [CLI Reference](./cli.md).

**Concurrency**: `notify_actions.max_concurrent` (default 5) limits how many
button-bearing notifications are in flight at once. As each resolves (clicked,
button pressed, or `notify_actions.wait_timeout` seconds elapse) the next
queued one is sent.

**Linux caveat**: a notification the user never interacts with is silently
auto-expired by the notification server with no callback — it occupies its
`max_concurrent` slot for the full `wait_timeout`.

**macOS/Windows**: click/button callbacks are cross-platform in principle, but
macOS may require a signed Python interpreter, and Windows toast notifications
generally need a registered AppUserModelID via a Start Menu shortcut.
Treat non-Linux support as best-effort.

## Typical workflow

1. Start with `default_action: notify` (default) — everything alerts unless
   you write ignore rules.
2. As spam emerges, add `ignore` or `mark_as_read` rules.
3. Use `mark_as_read` when you want notifications dismissed from GitHub
   entirely.
4. Optionally switch to `default_action: ignore` for a whitelist approach —
   only alerts on matched rules.
