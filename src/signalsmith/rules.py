import logging
from collections.abc import Callable
from typing import Any

import jinja2

from . import templating
from .config.models import Rule
from .github.models import GitHubIssue, GitHubNotification, GitHubPullRequest

logger = logging.getLogger(__name__)

__all__: list[str] = []


class RuleMatcher:
    def __init__(
        self, rules: list[Rule], *, account: dict[str, Any], variables: dict[str, Any]
    ) -> None:
        """Store rules for matching.

        Args:
            rules: Rules to match, in priority order.
            account: The `account` name available to every expression.
            variables: The `variables` name available to every expression.
        """
        self._rules = rules
        self._account = account
        self._variables = variables

    def find_matching_rule(
        self,
        notification: GitHubNotification,
        subject_fetcher: Callable[[str, str, str], GitHubIssue | GitHubPullRequest]
        | None = None,
    ) -> Rule | None:
        """Find first matching rule.

        Args:
            notification: The notification to match
            subject_fetcher: Optional function to fetch subject details
                            Signature: (subject_url, subject_type, notification_updated_at) -> subject_object

        Returns:
            First matching rule or None
        """
        context = templating.build_context(
            notification, None, self._account, self._variables
        )
        # `subject` is lazy: the fetch only happens if an expression actually touches
        # it, so a cheap `notification.*` guard on the left of `and` still guards the
        # API call - see doc/config.md#lazy-subject-access.
        if subject_fetcher is None:

            def _no_fetcher(url: str, type: str, updated_at: str) -> Any:
                raise templating.SubjectUnavailableError(
                    "no subject fetcher was provided", type, url
                )

            subject_fetcher = _no_fetcher
        context["subject"] = templating.LazySubject(notification, subject_fetcher)

        skipped: list[str] = []
        matched_rule: Rule | None = None

        for rule in self._rules:
            logger.debug(
                "Evaluating rule %r for notification %s",
                rule.id,
                notification.debug_info,
            )
            try:
                result = templating.evaluate(rule.expression, context)
            except templating.SubjectUnavailableError as e:
                raise RuntimeError(
                    f"Rule {rule.id!r} expression cannot be evaluated: it references `subject`, but "
                    f"subject type {e.subject_type!r} is not supported.\n"
                    f"  Error: {e}\n"
                    f"  Expression: {rule.expression}\n"
                    f"  Notification: {notification.debug_info}"
                ) from e
            except templating.SubjectFetchError as e:
                raise RuntimeError(
                    f"Failed to fetch subject for rule {rule.id!r}:\n"
                    f"  Error: {e}\n"
                    f"  Expression: {rule.expression}\n"
                    f"  Subject type: {e.subject_type}\n"
                    f"  Notification: {notification.debug_info}"
                ) from e
            except jinja2.TemplateError as e:
                raise RuntimeError(
                    f"Jinja expression error for rule {rule.id!r} expression on notification {notification.id}:\n"
                    f"  Error: {e}\n"
                    f"  Expression: {rule.expression}\n"
                    f"  Notification: {notification.debug_info}"
                ) from e
            except Exception as e:
                raise RuntimeError(
                    f"Unexpected error evaluating rule {rule.id!r} expression on notification {notification.id}:\n"
                    f"  Error: {e}\n"
                    f"  Expression: {rule.expression}\n"
                    f"  Notification: {notification.debug_info}"
                ) from e

            if result:
                matched_rule = rule
                break
            skipped.append(rule.id)

        if skipped:
            logger.info(
                "Notification %s: rules not matched: %s",
                notification.debug_info,
                ", ".join(skipped),
            )

        return matched_rule
