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
        """Find first matching rule using two-stage rule evaluation.

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

        skipped: list[str] = []
        matched_rule: Rule | None = None

        for rule in self._rules:
            logger.debug(
                "Evaluating rule %r for notification %s",
                rule.id,
                notification.debug_info,
            )
            # Stage 1: Evaluate expression against notification
            try:
                result = templating.evaluate(rule.expression, context)
                if not result:
                    skipped.append(rule.id)
                    continue  # Stage 1 failed, skip this rule
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

            # If no subject_expression, stage 1 match is sufficient
            if not rule.subject_expression:
                matched_rule = rule
                break

            # Stage 2: Fetch subject and evaluate subject_expression
            if subject_fetcher is None:
                logger.warning(
                    "Rule %r has subject_expression but no subject_fetcher provided",
                    rule.id,
                )
                skipped.append(rule.id)
                continue

            try:
                subject = subject_fetcher(
                    notification.subject.url or "",
                    notification.subject.type,
                    notification.updated_at,
                )
            except NotImplementedError as e:
                raise RuntimeError(
                    f"Rule {rule.id!r} subject_expression cannot be evaluated:\n"
                    f"  Subject type {notification.subject.type!r} is not supported.\n"
                    f"  Notification: {notification.debug_info}"
                ) from e
            except Exception as e:
                raise RuntimeError(
                    f"Failed to fetch subject for rule {rule.id!r}:\n"
                    f"  Error: {e}\n"
                    f"  Notification: {notification.debug_info}"
                ) from e

            combined_context = templating.build_context(
                notification, subject, self._account, self._variables
            )

            # Evaluate subject_expression
            try:
                subject_result = templating.evaluate(
                    rule.subject_expression, combined_context
                )
            except jinja2.TemplateError as e:
                raise RuntimeError(
                    f"Jinja expression error for rule {rule.id!r} subject_expression on notification {notification.id}:\n"
                    f"  Error: {e}\n"
                    f"  Expression: {rule.subject_expression}\n"
                    f"  Subject type: {notification.subject.type}\n"
                    f"  Notification: {notification.debug_info}"
                ) from e
            except Exception as e:
                raise RuntimeError(
                    f"Unexpected error evaluating rule {rule.id!r} subject_expression on notification {notification.id}:\n"
                    f"  Error: {e}\n"
                    f"  Expression: {rule.subject_expression}\n"
                    f"  Subject type: {notification.subject.type}\n"
                    f"  Notification: {notification.debug_info}"
                ) from e

            if subject_result:
                matched_rule = rule
                break  # Both stages matched!
            skipped.append(rule.id)

        if skipped:
            logger.info(
                "Notification %s: rules not matched: %s",
                notification.debug_info,
                ", ".join(skipped),
            )

        return matched_rule
