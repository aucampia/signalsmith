from unittest.mock import MagicMock

from signalsmith.actions import execute_actions
from signalsmith.actions.base import Action
from signalsmith.notification.models import NotificationOutcome
from signalsmith.stats import RunStats


def _fake_action(outcome: NotificationOutcome) -> MagicMock:
    action = MagicMock(spec=Action)
    action.outcome = outcome
    return action


def test_execute_actions_runs_every_action_and_updates_stats() -> None:
    notify_action = _fake_action(NotificationOutcome.NOTIFIED)
    ignore_action = _fake_action(NotificationOutcome.IGNORED)
    stats = RunStats()

    execute_actions(
        [
            (NotificationOutcome.NOTIFIED, notify_action),
            (NotificationOutcome.IGNORED, ignore_action),
        ],
        dry_run=False,
        stats=stats,
    )

    notify_action.execute.assert_called_once_with(dry_run=False)
    ignore_action.execute.assert_called_once_with(dry_run=False)
    assert stats.outcomes[NotificationOutcome.NOTIFIED] == 1
    assert stats.outcomes[NotificationOutcome.IGNORED] == 1


def test_execute_actions_updates_stats_for_none_action() -> None:
    """Filtered notifications yield (outcome, None) - stats still count them."""
    stats = RunStats()

    execute_actions(
        [(NotificationOutcome.FILTERED_ALREADY_READ, None)], dry_run=False, stats=stats
    )

    assert stats.outcomes[NotificationOutcome.FILTERED_ALREADY_READ] == 1


def test_execute_actions_dry_run_propagates_to_each_action() -> None:
    action = _fake_action(NotificationOutcome.NOTIFIED)

    execute_actions([(NotificationOutcome.NOTIFIED, action)], dry_run=True)

    action.execute.assert_called_once_with(dry_run=True)
