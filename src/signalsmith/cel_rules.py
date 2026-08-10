import json
import logging
from collections.abc import Callable
from typing import Any

import celpy

from .config.models import Rule
from .github.models import (
    GITHUB_NOTIFICATION_ADAPTER,
    GitHubIssue,
    GitHubNotification,
    GitHubPullRequest,
)

logger = logging.getLogger(__name__)

__all__: list[str] = []


class RuleMatcher:
    def __init__(
        self, rules: list[Rule], context: dict[str, Any] | None = None
    ) -> None:
        """Compile rules.

        Args:
            rules: Rules to compile.
            context: Extra top-level variables merged into every expression's
                activation alongside `notification`/`subject` (e.g. `account`).
        """
        self._context = context or {}
        env = celpy.Environment()
        self._compiled: list[tuple[Rule, celpy.Runner, celpy.Runner | None]] = []
        for rule in rules:
            try:
                # Compile expression
                ast = env.compile(rule.expression)
                program = env.program(ast)

                # Compile subject_expression if present
                subject_program = None
                if rule.subject_expression:
                    subject_ast = env.compile(rule.subject_expression)
                    subject_program = env.program(subject_ast)
                    logger.debug(
                        "Compiled rule %r expression: %s, subject_expression: %s",
                        rule.id,
                        rule.expression,
                        rule.subject_expression,
                    )
                else:
                    logger.debug("Compiled rule %r: %s", rule.id, rule.expression)

                self._compiled.append((rule, program, subject_program))
            except Exception:
                logger.exception(
                    "Failed to compile rule %r: expression=%s, subject_expression=%s",
                    rule.id,
                    rule.expression,
                    rule.subject_expression,
                )
                raise

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
        # Build CEL activation from notification JSON
        notification_dict = GITHUB_NOTIFICATION_ADAPTER.dump_python(
            notification, mode="json"
        )
        activation: Any = celpy.json_to_cel(  # type: ignore[attr-defined]
            {"notification": notification_dict, **self._context}
        )

        skipped: list[str] = []
        matched_rule: Rule | None = None

        for rule, program, subject_program in self._compiled:
            logger.debug(
                "Evaluating rule %r for notification %s",
                rule.id,
                notification.debug_info,
            )
            # Stage 1: Evaluate expression against notification
            try:
                result = program.evaluate(activation)
                if not result:
                    skipped.append(rule.id)
                    continue  # Stage 1 failed, skip this rule
            except celpy.CELEvalError as e:  # type: ignore[attr-defined]
                raise RuntimeError(
                    f"CEL evaluation error for rule {rule.id!r} expression on notification {notification.id}:\n"
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
            if subject_program is None:
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

            subject_dict = json.loads(subject.model_dump_json())

            # Create activation with BOTH notification and subject
            combined_activation: Any = celpy.json_to_cel(  # type: ignore[attr-defined]
                {
                    "notification": notification_dict,
                    "subject": subject_dict,
                    **self._context,
                }
            )

            # Evaluate subject_expression
            try:
                subject_result = subject_program.evaluate(combined_activation)
            except celpy.CELEvalError as e:  # type: ignore[attr-defined]
                raise RuntimeError(
                    f"CEL evaluation error for rule {rule.id!r} subject_expression on notification {notification.id}:\n"
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
