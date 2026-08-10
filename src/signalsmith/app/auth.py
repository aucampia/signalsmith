"""GitHub token resolution."""

import logging
import os
import subprocess

from ..errors import AuthError

logger = logging.getLogger(__name__)

__all__ = ["resolve_github_token"]


def resolve_github_token() -> str:
    """Get a GitHub token from the environment or the `gh` CLI.

    Raises:
        AuthError: if no token can be found either way.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        token = result.stdout.strip()
        if token:
            logger.debug("Using token from gh CLI")
            return token
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        logger.debug("Could not get token from gh CLI: %s", exc)

    raise AuthError(
        "GitHub token not found. Set GITHUB_TOKEN/GH_TOKEN environment variable "
        "or authenticate with: gh auth login"
    )
