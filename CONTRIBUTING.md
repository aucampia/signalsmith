# Contributing to signalsmith

## Repository Layout

```
signalsmith/
├── src/signalsmith/                 # Python source code
│   ├── cli.py                   # CLI entry point (Typer): flags -> app/ -> render
│   ├── app/                     # Orchestration shared by the CLI commands
│   │   ├── context.py           # AppContext, build_app_context, open_notify_runtime
│   │   ├── cycle.py             # process_cycle: one create+execute+reap pass
│   │   ├── daemon.py            # run_daemon: the `daemon` command's poll loop
│   │   └── auth.py              # GitHub token resolution
│   ├── processor.py             # Fetch -> mask -> rule-match -> action pipeline
│   ├── actions/                 # Action implementations
│   │   ├── base.py              # Action protocol
│   │   ├── registry.py          # ACTION_SPECS: the ActionKind -> builder table
│   │   ├── factory.py           # resolve_action_config, create_action_for_rule
│   │   ├── execute.py           # execute_actions
│   │   ├── runtime.py           # NotifyRuntime (daemon's interactive-notification context)
│   │   └── notify.py / mark_as_read.py / ignore.py / skip.py  # one action kind each
│   ├── stats.py                 # RunStats
│   ├── errors.py                # SignalsmithError, AuthError (VersionError also derives from this)
│   ├── cache.py                 # Cache directory (disposable: notification/subject cache, spool trash)
│   ├── logging_config.py        # setup_logging, celpy noise filter, debug dump
│   ├── templating.py            # Jinja rendering of `notice`/`notify` title/body
│   ├── notifier.py              # Desktop notification sender (`run`, non-interactive)
│   ├── notify_dispatcher.py     # Persistent interactive notifier (`daemon`)
│   ├── cel_rules.py             # CEL expression filtering
│   ├── protocols.py             # Provider protocol (NotificationProvider)
│   ├── versioning.py            # Schema versioning for config/test/state/cache
│   ├── github/                  # GitHub API concerns
│   │   ├── client.py             # GitHub API client with caching
│   │   └── models.py             # GitHub API + cache models
│   ├── config/                  # User-facing YAML config schema
│   │   ├── models.py             # Config, Rule, ActionKind, actions, masks, etc.
│   │   └── testing.py            # Offline test harness for user config rules (`signalsmith test`)
│   ├── state/                    # Durable on-disk state
│   │   ├── models.py             # SpoolEntry, SpoolNotifyEvent, IgnoredEntry
│   │   ├── spool.py              # Notified-notification spool (also drives renotify suppression)
│   │   └── ignore_store.py       # Permanent-ignore store (the notification "Ignore" button)
│   ├── notification/             # Cross-cutting notification vocabulary
│   │   └── models.py             # NotificationOutcome
│   └── examples/                 # Example config YAMLs (see README.md)
│       ├── example-config.yaml
│       ├── example-config-with-actions.yaml
│       └── config-example-orgs.yaml
├── tests/                # pytest test suite, mirroring src/signalsmith/ packages
│   ├── app/
│   │   ├── test_context.py
│   │   ├── test_cycle.py
│   │   ├── test_daemon.py
│   │   └── test_auth.py
│   ├── actions/
│   │   ├── test_registry.py
│   │   ├── test_notify.py
│   │   ├── test_skip.py
│   │   └── test_execute.py
│   ├── github/
│   │   └── test_client.py
│   ├── config/
│   │   ├── test_models.py
│   │   └── test_testing.py
│   ├── state/
│   │   ├── test_spool.py
│   │   └── test_ignore_store.py
│   ├── conftest.py       # Shared fixtures (MockProvider, minimal Config, ...)
│   ├── test_cli.py
│   ├── test_cel_rules.py
│   ├── test_notifier.py
│   ├── test_notify_dispatcher.py
│   ├── test_processor.py
│   ├── test_templating.py
│   ├── test_example_configs.py
│   └── test_versioning.py
├── doc/                  # Reference documentation
│   ├── config.md         # Configuration file reference
│   ├── test-format.md    # Test file format
│   └── cli.md            # CLI reference
├── pyproject.toml        # Project metadata and dependencies
├── Taskfile.yml          # Task runner (go-task)
├── SPEC.md               # Technical specification and architecture
└── README.md             # Usage guide
```

## Development Workflow

### Initial Setup

1. **Install dependencies:**
   ```bash
   task configure
   ```
   This runs `mise install` (fetches the pinned versions of `task`,
   `yamlfmt`, `shfmt`, `shellcheck`, and `uv` from `.mise.toml`) and then
   `uv sync` to install Python dependencies including dev tools.

   Alternatively, run everything in the containerized devtools environment
   (also what CI uses) without installing `mise`/`uv` locally:
   ```bash
   docker compose run --rm devtools task configure validate
   ```

### Making Changes

1. Make your changes to `src/signalsmith/` or `tests/`
2. Follow the validation workflow below before committing

### Validation Workflow

Run these commands in order:

1. **Auto-fix formatting and linting:**
   ```bash
   task validate:fix
   ```
   This runs `ruff format` and `ruff check --fix` to automatically fix code style issues.

2. **Run static checks:**
   ```bash
   task validate:static
   ```
   This runs:
   - `mypy` - Type checking (strict mode)
   - `ruff check` - Linting
   - `codespell` - Spell checking
   - `yamlfmt` - YAML formatting (CI/infra files only)
   - `shfmt` / `shellcheck` - Shell script formatting and linting

3. **Run tests:**
   ```bash
   task test
   ```
   This runs pytest with coverage reporting.

4. **Full validation (static checks + tests):**
   ```bash
   task validate
   ```
   Runs `validate:static` then `test` - it does *not* run `validate:fix`.
   Use `task fix-and-validate` to run all three (`validate:fix`,
   `validate:static`, `test`) in sequence.

### Running the CLI Locally

```bash
# Via task command
task cli

# Or directly with uv
uv run signalsmith --help
uv run signalsmith run --verbose
uv run signalsmith daemon --poll-interval 300
```

## Code Style

- **Python version:** 3.14+
- **Formatter:** ruff (Black-compatible, 88 char line length)
- **Type hints:** Required (mypy strict mode enabled)
- **Imports:** Sorted with isort rules (via ruff)
- **Async:** pytest-asyncio for async tests

## Testing

- Place tests in `tests/` directory
- Name test files `test_*.py`
- Use pytest fixtures for common setup
- Import from `signalsmith.*` (not relative imports in tests)
- Aim for good coverage of core logic (CEL filters, processing, API client)

## Configuration Files

Example config files live in `src/signalsmith/examples/`:
- `example-config.yaml` - Comprehensive feature showcase
- `example-config-with-actions.yaml` - Reusable action definitions
- `config-example-orgs.yaml` - Organization filtering patterns

User configs go in `~/.config/signalsmith/config.yaml` (not tracked in git).

## Dependencies

Dependencies are managed via `pyproject.toml`:
- **Runtime:** Listed in `[project.dependencies]`
- **Development:** Listed in `[dependency-groups.dev]`

After adding dependencies, run `task configure` to update the lockfile.

## Submitting Changes

1. Run `task validate` to ensure all checks pass
2. Commit your changes with a descriptive message
3. Create a pull request with a clear description of what changed and why

## Continuous Integration

Pull requests against `main` are validated by the `Validate` GitHub Actions
workflow (`.github/workflows/validate.yml`), which runs `task configure
validate` inside the `devtools` container (see `docker-compose.yaml`) — the
same command described in the Validation Workflow section above.

## Project Tooling

- **Package manager:** uv (fast Python package installer)
- **Build backend:** hatchling
- **Task runner:** go-task (Taskfile.yml)
- **Tool version pinning:** mise (`.mise.toml`) for `task`, `yamlfmt`,
  `shfmt`, `shellcheck`, `uv`
- **CLI framework:** Typer
- **Data validation:** Pydantic v2, via `pydantic.dataclasses.dataclass` (not
  `BaseModel` — see AGENTS.md for the narrow exception and why)
- **Testing:** pytest + pytest-cov
- **Type checking:** mypy (strict mode)
- **Linting/formatting:** ruff, yamlfmt, shfmt, shellcheck
