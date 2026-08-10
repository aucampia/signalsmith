"""Domain error hierarchy.

Library code (`app/`, `config/`, `state/`, `github/`, ...) raises these
instead of anything Typer-specific, so it stays importable and testable
without a CLI runtime. `cli.main` is the single place that catches
`SignalsmithError` and turns it into a process exit - see `cli.py:main`.
"""

__all__ = ["AuthError", "SignalsmithError"]


class SignalsmithError(Exception):
    """Base class for errors that should abort the current command cleanly."""


class AuthError(SignalsmithError):
    """No usable GitHub credentials were found."""
