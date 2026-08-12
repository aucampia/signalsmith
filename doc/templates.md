# Template Reference

The `notice:` block and `notify` action use
[Jinja](https://jinja.palletsprojects.com/) templates to render notification
titles and bodies — `{{ ... }}` for values with optional
`{% if %}`/`{% for %}` blocks. Plain text, no HTML autoescaping.

For the full field reference on `notification`, `subject`, `account`, and
`variables`, see [Object Reference](./objects.md).

## What objects are available where

Not every context sees the same objects. This is the only place that maps them:

| Context | `notification` | `subject` | `account` | `variables` | `notice` |
|---|---|---|---|---|---|
| Rule `expression` | Yes | Lazy (on access) | Yes | Yes | No |
| `notice.title` / `notice.body` | Yes | On-demand fetch | Yes | Yes | No |
| `notify.title` / `notify.body` | Yes | On-demand fetch | Yes | Yes | Yes |

### `subject` in templates

If a `notice` or `notify` template references `subject` and no rule expression
already fetched it, signalsmith fetches it before rendering. If the fetch
fails or the type is unfetchable (e.g. `Release`), `subject` is `Undefined`
in the template scope.

Guard for it explicitly:

```yaml
{% if subject is defined %}{{ subject.user.login }}{% endif %}
```

## The `notice:` block

Computed once per notification, before rule-specific `notify` overrides.
Defaults (optional, override either field independently):

```yaml
notice:
  title: '{{ notification.subject.type }}: {{ notification.subject.title }}'
  body: '{{ notification.repository.full_name }} ({{ notification.reason }})'
```

## `notify` overrides

A `notify` action's `title` and `body` are each independently optional —
unset fields fall back to the rendered `notice.title`/`notice.body`.
`notify: {}` uses the generic notice verbatim.

The already-rendered notice is available as `notice` in scope:

```yaml
rules:
  - id: 'urgent_assignment'
    expression: 'notification.reason == "assign"'
    action:
      notify:
        title: '[Assigned] {{ notice.title }}'
        # body omitted — falls back to rendered notice.body
```

## Failure handling

If a template fails to render, the notification is not dropped — a fallback is
used instead.

- **Subject unavailable** (unfetchable type, fetch failed) — if the template
  references `subject`, logged at `WARNING`; otherwise `ERROR`. Fallback:
  `notice.*` → built-in defaults; `notify.*` → rendered `notice.*`.
- **Any other error** (typo, undefined variable, syntax error) — logged at
  `ERROR`. Same fallback chain.

The WARNING/ERROR split is scoped to templates that actually reference
`subject` — a typo in a template that happens to mention `subject` is still a
config bug (ERROR), not a transient subject availability issue (WARNING).

Built-in defaults (always available, never fail):

```text
title: "{{ notification.subject.type }}: {{ notification.subject.title }}"
body:  "{{ notification.repository.full_name }} ({{ notification.reason }})"
```
