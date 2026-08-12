# Rule Expression Reference

## How rules work

Rules are evaluated **in order**; the first matching rule wins. Each rule has
a unique `id`, a bare Jinja `expression`, and an `action` (inline or by
reference — see [Action Reference](./actions.md)).

If no rule matches, the configured `default_action` (default: `notify`) is
used: `notify` means desktop-alert everything that didn't match a rule;
`ignore` means silently skip unmatched notifications.

For the full field reference on `notification`, `subject`, `account`, and
`variables`, see [Object Reference](./objects.md).

## Available objects

| Object | Always present? | Notes |
|---|---|---|
| `notification` | Yes | GitHub notification payload; no API call |
| `subject` | Lazy | Full Issue/PR object, fetched on first attribute access |
| `account` | Yes | `{github: {username: "..."}}` from `GET /user` |
| `variables` | Yes | User-defined `variables:` block (plain `dict`) |

## Expressions are Jinja

`expression` is a bare [Jinja](https://jinja.palletsprojects.com/) expression
(not `{{ }}`-wrapped) evaluated to a Python value — falsy means no match,
truthy means match. `&&`/`||`/`!` become `and`/`or`/`not`.

### Operators

`and`, `or`, `not`, `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`.
`+` concatenates lists.

### Filters

- `|default(value)` — supply fallback for an undefined or missing field
- `|select(test)` / `|select(test, arg)` — keep items that pass the test
- `|selectattr(attr, test)` / `|selectattr(attr, test, arg)` — filter on
  attribute
- `|reject(test)` / `|rejectattr(...)` — inverse; keep items that fail
- `|map(attribute='name')` — extract an attribute from each item
- `|first` — first item (or `Undefined` if empty)
- `|list` — evaluate a generator into a list
- `|length` — item count
- `|join(sep)` — join strings
- `|sort` — sort items
- `|tojson` — serialize to JSON

The standard Jinja filter reference is at
[docs](https://jinja.palletsprojects.com/en/stable/templates/#builtin-filters).

### Tests

- `is defined` / `is undefined`
- `is none`
- `startingwith` (custom) — `items | select('startingwith', str)` tests
  `str.startswith(item)` for each item in the list. For a single literal
  prefix, plain `.startswith()` is simpler:
  `notification.repository.full_name.startswith("a-")`.

### StrictUndefined

Missing attributes raise rather than silently evaluating to `None` — typos are
hard errors, not accidental no-matches.

- `subject.requested_reviewers|default([])` — supply an explicit fallback for
  a field that may be absent
- `subject.foo is defined` — check presence without a fallback
- **`subject is defined` is always true** in a rule expression — the name is
  always bound to the lazy proxy, even before a fetch. Use
  `notification.subject.type in ("Issue", "PullRequest")` to filter for
  fetchable types.
- **Nested access**: `variables.ontopic.prefixes|default([])` does **not**
  protect against a missing `variables.ontopic` — `|default` only guards the
  final hop. Use `.get()` chaining:

  ```yaml
  variables.get('ontopic', {}).get('prefixes', [])
  ```

## Common idioms

**"Any item matches":**

```yaml
items | select('startingwith', notification.repository.full_name) | first is defined
```

**"No item fails a check":**

```yaml
subject.requested_teams | rejectattr('slug', 'in', spam_teams) | first is undefined
```

**Plain string method (single literal):**

```yaml
notification.repository.full_name.startswith("myorg/team-")
```

## Lazy subject access

`subject` is a lazy proxy — it only fetches the full Issue/PR from the GitHub
API on first attribute access. Jinja's `and`/`or` short-circuit like Python's,
so a cheap `notification.*` check on the left can gate the expensive API call.

**Good** (fetches subject only for PRs):

```yaml
expression: >
  notification.subject.type == "PullRequest"
  and (subject.merged or subject.state == "closed")
```

**Bad** (fetches subject for everything, then filters to PRs):

```yaml
expression: >
  (subject.merged or subject.state == "closed")
  and notification.subject.type == "PullRequest"
```

**Parenthesize** when merging `and`-joined conditions — `a and b or c`
reassociates as `(a and b) or c`, not `a and (b or c)`.

## Testing rules

Run `signalsmith test` to validate rules against offline test cases without
making API calls. See [Test File Format](./test-format.md).
