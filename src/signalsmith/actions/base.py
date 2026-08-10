"""The `Action` protocol.

`ActionKind` itself lives in `config.models` (re-exported here for
convenience) rather than here: `config.models` declares the `notify`/
`mark_as_read`/`ignore` fields it enumerates, and every module in this
package already depends on `config.models`, so defining it there is what
keeps `config.models` -> `actions` a one-way dependency instead of a cycle.
"""

from typing import Protocol

from ..config.models import ActionKind
from ..notification.models import NotificationOutcome

__all__ = ["Action", "ActionKind"]


class Action(Protocol):
    """Protocol for actions that can be executed on notifications."""

    outcome: NotificationOutcome
    """The `NotificationOutcome` this action reports once created (regardless
    of whether `execute` has run yet - `processor.create_actions` reads this
    to update `RunStats` before executing anything)."""

    def execute(self, dry_run: bool = False) -> None:
        """Execute the action.

        Args:
            dry_run: If True, print what would happen instead of executing
        """
        ...
