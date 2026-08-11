# Test File Format

Filter rules can be tested offline (no GitHub API calls, no token) by writing YAML test files under a `tests/` directory: `<test dir>/*.yaml` (e.g. `~/.config/signalsmith/tests/spam-bots.yaml`). `<test dir>` is resolved independently of the config file's location (checked in order): the `--tests-dir` CLI flag, the `SIGNALSMITH_TEST_DIR` environment variable, or `<config dir>/tests` (see [config.md](./config.md) for how `<config dir>` is resolved). Run them with `signalsmith test`.

Each file holds a list of `cases`. A case's `input:` block supplies a *partial* `notification` (and optional partial `subject`/`account`) — only the fields the rule under test cares about; the rest are filled in from built-in defaults. An `input:` block can also be set at the file level as shared defaults that every case's `input:` deep-merges over. A `variables:` block (file-level and/or case-level) replaces the config's `variables:` for that test, enabling tests to simulate absent or modified variable blocks. An `expect` block names the rule that should win:

```yaml
version: '2.1'                    # required, test file schema version (see doc/config.md#versioning)

input:                            # optional, file-level defaults for every case
  account:
    github: { username: someuser }

cases:
  - name: bot PRs are marked as read
    parameters: '{{ variables.spam_bots }}'   # parameterize over a config variables.* list
    input:
      notification:
        subject:
          type: PullRequest
      subject:
        user:
          login: '{{ parameter }}'            # current item from `parameters`
    expect:
      rule: bot_pr_mark_as_read
      action: mark_as_read               # optional cross-check

  - name: closed PRs are marked as read
    parameters: [closed, merged]         # or an inline list
    input:
      notification:
        subject:
          type: PullRequest
      subject:
        state: '{{ parameter }}'
    expect:
      rule: closed_pr_mark_as_read

  - name: unrelated issue notifies by default
    input:
      notification:
        subject:
          type: Issue
    expect:
      rule: null      # asserts *no* rule matches
      action: notify

  - name: rule id varies by parameter
    parameters:
      - { prefix: 'grc-', rule: off_topic_mark_as_read }
      - { prefix: 'my-team-', rule: reviewer_or_assignee }
    input:
      notification:
        repository:
          full_name: 'myorg/{{ parameter.prefix }}repo'
    expect:
      rule: '{{ parameter.rule }}'   # expect.rule/expect.action can reference `parameter` too
```

## Test Case Structure

- **`version`** (required, file-level) — the test file's schema version (`MAJOR.MINOR`, e.g. `'2.1'`). An incompatible version refuses to run the file; see [Versioning](./config.md#versioning) for the compatibility rules and bump policy.
- **`variables`** (optional, file-level and/or case-level) — replaces the config's `variables:` block for this test. **Never merges**: the most specific `variables:` (case > file > config) wins wholesale. `variables: {}` means no variables at all. Enables testing rules against absent or modified variable blocks. Can contain `{{ ... }}` templates referencing `config.variables` (the real config's variables, for reconstruction) and `account`, but **not** `parameter` (parameters are expanded after variables are resolved, using the effective variables in scope). **Note**: case-level variables also cannot reference file-level variables (only `config.variables` is exposed, not `file.variables`) — a case must reconstruct from config if it wants to build on file-level overrides. Example reconstruction idioms:
  - `variables: "{{ config.variables }}"` — use the config verbatim
  - `variables: "{{ dict(config.variables, ontopic={}) }}"` — shallow override
  - `variables: "{{ dict(config.variables|items|rejectattr('0','eq','ontopic')|list) }}"` — all keys except one
  - `variables: "{{ dict(default_user=account.github.username) }}"` — use account info
- **`expect.rule`** — id of the rule expected to be the first match; `null` asserts that *no* rule matches (falling through to `default_action`).
- **`expect.action`** (optional) — cross-checks the resulting `notify`/`mark_as_read`/`ignore` action, catching a case where the right *action* happens via the wrong rule.
- Both `expect.rule` and `expect.action` support the same `{{ ... }}` templating as `input.notification`/`input.subject` (see the last example above) — useful when one parameterized case exercises more than one rule.
- **`parameters`** — either an inline YAML list, or a `{{ variables.some_list }}` reference resolved from the *effective* variables (after any test-level override). The case runs once per item, with `{{ parameter }}` bound to the current item (`{{ parameter.field }}` for object items, e.g. `spam_groups`). Omit `parameters` to run the case once, unparameterized.
- **`{{ ... }}` templating** — a value that is *exactly* `{{ expr }}` is replaced with the resolved value's real type (list/dict/scalar/bool), evaluated the same way rule `expression`/`subject_expression` are (see [Rule Expressions Are Jinja](./config.md#rule-expressions-are-jinja)); a `{{ expr }}` embedded in a longer string is rendered to text instead, same as a `notice`/`notify` template. Available references: `parameter`, `variables` (the effective variables after any test override), `config` (object with `.variables` field holding the real config's variables), `account`.
- **`input.account`** — optional, at file level and/or per case; case-level `input` deep-merges over file-level `input`, which deep-merges over the built-in default `{github: {username: testuser}}`. Needed for rules using `account.github.username`.
- **`input`** at file level sets defaults shared by every case; a case's own `input` is deep-merged over those defaults, so a case only needs to state what it changes.
- Partial `input.notification`/`input.subject` values are deep-merged over an internal default skeleton that satisfies all required model fields, so a case only needs to set what the rule actually inspects.

Note: a leading `{{` is a YAML flow-mapping indicator, so **any** `{{ ... }}` value must be quoted (`login: '{{ parameter }}'`) — not just inside flow-style mappings (`{ key: value }`), unlike the old `${...}` syntax, which needed no quoting.

## Running Tests

Run tests with:

```bash
signalsmith test
```

Options:
- `--tests-dir PATH`: Directory of test files (default: `tests/` next to the config file).
- `-k TEXT`: Only run cases whose name contains this substring.

The command exits non-zero if any case fails, making it suitable for CI pipelines.
