"""Aggregate counters for a single run."""

from collections import Counter
from dataclasses import dataclass, field

from .notification.models import NotificationOutcome

__all__ = ["RunStats"]


@dataclass
class RunStats:
    """Aggregate counters for a single run, populated during processing."""

    found: int = 0
    outcomes: Counter[NotificationOutcome] = field(default_factory=Counter)
    by_org: Counter[str] = field(default_factory=Counter)
    by_repo: Counter[str] = field(default_factory=Counter)
    by_reason: Counter[str] = field(default_factory=Counter)
    by_creator: Counter[str] = field(default_factory=Counter)

    @property
    def notified(self) -> int:
        return self.outcomes[NotificationOutcome.NOTIFIED]

    @property
    def marked_as_read(self) -> int:
        return self.outcomes[NotificationOutcome.MARKED_AS_READ]

    @property
    def ignored(self) -> int:
        return self.outcomes[NotificationOutcome.IGNORED]

    @property
    def skipped(self) -> int:
        return self.outcomes[NotificationOutcome.SKIPPED]

    def summary(self) -> str:
        outcome_parts = " ".join(
            f"{outcome.value}={self.outcomes[outcome]}"
            for outcome in NotificationOutcome
        )
        return f"found={self.found} {outcome_parts}"

    def breakdown(self, top_repos: int = 10, top_creators: int = 10) -> str:
        lines = ["By org:"]
        for org, count in self.by_org.most_common():
            lines.append(f"  {count:5d}  {org}")

        lines.append("")
        lines.append(f"Top {top_repos} repos:")
        for repo, count in self.by_repo.most_common(top_repos):
            lines.append(f"  {count:5d}  {repo}")

        lines.append("")
        lines.append("By reason:")
        for reason, count in self.by_reason.most_common():
            lines.append(f"  {count:5d}  {reason}")

        if self.by_creator:
            lines.append("")
            lines.append(f"Top {top_creators} PR/issue creators:")
            for creator, count in self.by_creator.most_common(top_creators):
                lines.append(f"  {count:5d}  {creator}")

        return "\n".join(lines)
