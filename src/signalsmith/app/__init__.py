"""Application layer: orchestration shared by the CLI commands.

`cli.py` handles flag parsing and output formatting only; everything about
*how* a poll cycle or the daemon loop actually runs lives here, so it's
usable (and testable) without going through Typer at all.
"""

from .auth import resolve_github_token
from .context import AppContext, build_app_context, open_notify_runtime
from .cycle import process_cycle
from .daemon import run_daemon

__all__ = [
    "AppContext",
    "build_app_context",
    "open_notify_runtime",
    "process_cycle",
    "resolve_github_token",
    "run_daemon",
]
