# Contributing to signalsmith

## Repository Layout

```
signalsmith/
├── src/signalsmith/                 # Python source code
│   ├── cli.py                  # CLI entry point (Typer)
│   ├── processor.py            # Notification processing orchestrator
│   ├── actions.py              # Action implementations
│   ├── notifier.py             # Desktop notification sender
│   ├── cel_rules.py            # CEL expression filtering
│   ├── protocols.py            # Provider protocol (NotificationProvider)
│   ├── versioning.py           # Schema versioning for config/test/state/cache
│   ├── github/                 # GitHub API concerns
│   │   ├── client.py           # GitHub API client with caching
│   │   └── models.py           # GitHub API + cache models
│   ├── config/                 # User-facing YAML config schema
│   │   ├── models.py           # Config, Rule, actions, masks, etc.
│   │   └── testing.py          # Offline test harness for user config rules (`signalsmith test`)
│   ├── state/                  # Durable on-disk state
│   │   ├── models.py           # SpoolEntry, SpoolNotifyEvent
│   │   └── spool.py            # Notified-notification spool (also drives renotify suppression)
│   ├── notification/           # Cross-cutting notification vocabulary
│   │   └── models.py           # NotificationOutcome
│   ├── example-config.yaml              # Full config example
│   ├── example-config-with-actions.yaml # Reusable actions example
│   └── config-example-orgs.yaml         # Organization filtering example
├── tests/                # pytest test suite, mirroring src/signalsmith/ packages
│   ├── github/
│   │   └── test_client.py
│   ├── config/
│   │   ├── test_models.py
│   │   └── test_testing.py
│   ├── state/
│   │   └── test_spool.py
│   ├── test_cel_rules.py
│   ├── test_notifier.py
│   ├── test_processor.py
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
   This runs `uv sync` to install all dependencies including dev tools.

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

3. **Run tests:**
   ```bash
   task test
   ```
   This runs pytest with coverage reporting.

4. **Full validation (all of the above):**
   ```bash
   task validate
   ```
   Runs `validate:fix`, `validate:static`, and `test` in sequence.

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

Example config files live in `src/signalsmith/`:
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

## Project Tooling

- **Package manager:** uv (fast Python package installer)
- **Build backend:** hatchling
- **Task runner:** go-task (Taskfile.yml)
- **CLI framework:** Typer
- **Data validation:** Pydantic v2, via `pydantic.dataclasses.dataclass` (not
  `BaseModel` — see AGENTS.md for the narrow exception and why)
- **Testing:** pytest + pytest-cov
- **Type checking:** mypy (strict mode)
- **Linting/formatting:** ruff
