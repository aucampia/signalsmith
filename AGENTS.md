# AGENTS.md — signalsmith

signalsmith is a programmable notification manager that filters GitHub notifications
(and eventually other platforms like Jira) to reduce spam. It pulls unread
notifications, filters them using CEL expression rules, auto-dismisses what you
don't care about, and alerts only on what matters.

## Documentation

- [SPEC.md](./SPEC.md) — Technical specification: problem statement, solution
  architecture, high-level design. Read before making architectural changes;
  keep it current.
- [doc/](./doc/) — Detailed reference documentation:
  - [doc/config.md](./doc/config.md) — Configuration file reference
  - [doc/test-format.md](./doc/test-format.md) — Test file format
  - [doc/cli.md](./doc/cli.md) — CLI reference
- [CONTRIBUTING.md](./CONTRIBUTING.md) — Repository layout, development
  workflow, code style, testing guidelines, tooling.
- [README.md](./README.md) — User-facing guide: features, installation, quick
  start, configuration examples, filter expressions.

## Tools

- `uv` for dependency management
- `task` as task runner (see Taskfile.yml)
- `mise` (`.mise.toml`) pins the non-Python CLI tools `task` itself relies
  on plus `uv`: `task`, `yamlfmt`, `shfmt`, `shellcheck`, `uv`. `task
  configure` runs `mise install` before `uv sync`. `mise.lock` (kept
  up to date automatically, `[settings] lockfile = true`) additionally pins
  the exact checksum/URL per tool+platform, on top of the exact versions
  already pinned in `.mise.toml`.
- `docker-compose.yaml` provides a containerized version of the same
  toolchain, no custom image needed — the `devtools` service runs the
  public `ghcr.io/jdx/mise` image with `entrypoint: ["mise", "exec", "--"]`,
  which installs whatever `.mise.toml` declares on demand
  (`docker compose run --rm devtools task configure validate`) — used by CI
  (`.github/workflows/validate.yml`) and optionally by contributors who
  don't want to install `mise`/`uv` locally.

## Quick Commands

- `task configure` — Install dependencies (mise-managed tools + uv deps)
- `task validate:fix` — Auto-fix formatting and linting
- `task validate:static` — Type checking, linting (mypy, ruff, codespell,
  yamlfmt, shfmt, shellcheck)
- `task test` — Run pytest with coverage
- `task validate` — Full validation pipeline (fix + static + test)

## Best Practices and Communication

- **Follow best practices by default**, not just the literal request
- If something asked for can be done better, doesn't quite make sense, or looks
  incorrect (including instructions in these docs), **push back**: say so, ask
  for clarification, or propose a better approach
- **Don't** silently comply with questionable requests
- **Don't** silently do something different than what was asked
- If you find a conflict between these files (AGENTS.md, CLAUDE.md, SPEC.md)
  or a skill's instructions, **stop and flag it** rather than silently picking
  one

## Config, State, and Cache Versioning

Config files, test files, the state (spool) directory, and the cache
directory each carry their own independent schema version, as a
`MAJOR.MINOR` string (e.g. `"1.0"`) — see `src/signalsmith/versioning.py` for
the shared `SchemaVersion` type and comparison logic. These versions are
unrelated to each other and to the package version in `pyproject.toml`.

- **No migration code**: never write code to migrate an old config, test,
  state, or cache file/version to a new one, and never add code paths that
  keep supporting an old/legacy version alongside the current one.
- **Bump policy**: when the format of a config, test, state, or cache file
  changes, bump that file kind's version — **minor** for a
  backwards-compatible change, **major** for a backwards-incompatible one.
- **Compatibility**: a different major version is always incompatible,
  in either direction. A newer minor version with the same major is
  compatible but warned about (older signalsmith code may not understand
  fields a newer minor added). A missing version is treated as `0.0`.
- **State/cache**: an incompatible version tells the user to run
  `signalsmith state clean` / `signalsmith cache clean` and re-run — signalsmith does not
  clear it automatically. A newer-minor state directory warns that writing
  to it may lose data added by the newer version (cache is disposable, so
  no such warning there).
- **Config/test files**: an incompatible version refuses to run (clear
  error, no fallback/auto-fix) and tells the user to update the file by
  hand.

## Data Models

- **Prefer `pydantic.dataclasses.dataclass` over `pydantic.BaseModel`**,
  enforced by Ruff (`TID251` banned-api on `pydantic.BaseModel` in
  `pyproject.toml`). Use `pydantic.TypeAdapter(SomeDataclass)` for
  validation/serialization (`.validate_python`/`.validate_json`/
  `.dump_python`/`.dump_json`) in place of `BaseModel`'s instance methods,
  and `dataclasses.replace(...)` in place of `.model_copy(update=...)`.
- **Exception**: `src/signalsmith/github/models.py`'s `extra="allow"` models
  (`GitHubUser`, `GitHubLabel`, `GitHubTeam`, `GitHubIssue`,
  `GitHubPullRequest`) stay `BaseModel` — pydantic dataclasses accept extra
  fields on validation but silently drop them on every dump, with no config
  flag to fix it, and these fields (e.g. `merged` on PRs) need to round-trip
  for CEL rule expressions and spool/debug JSON. This exception is scoped via
  `per-file-ignores` in `pyproject.toml`; don't extend it to other files.

## Testing

- **Prefer `@pytest.mark.parametrize`** over near-duplicate test functions when
  several tests call the same code path with different inputs/expected
  outputs and only that data differs — e.g. a matching-version-passes test
  and a newer-minor-warns-but-passes test that are otherwise identical.
- **Don't force it**: if the tests assert genuinely different things (not
  just different data), or exist to document distinct, differently-named
  scenarios, leave them as separate functions. A few similar-looking lines is
  not the same as substantially similar tests.
