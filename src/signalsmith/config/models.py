import logging
import os
from dataclasses import field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import TypeAdapter, model_validator
from pydantic.dataclasses import dataclass
from xdg import xdg_config_home

from ..versioning import CONFIG_VERSION, SchemaVersion, check_file_version

logger = logging.getLogger(__name__)

__all__: list[str] = []


class DefaultAction(StrEnum):
    """Default action for unmatched notifications."""

    NOTIFY = "notify"
    IGNORE = "ignore"


@dataclass(kw_only=True)
class NotifyActionConfig:
    title: str
    message: str


@dataclass(kw_only=True)
class MarkAsReadActionConfig:
    pass


@dataclass(kw_only=True)
class IgnoreActionConfig:
    pass


@dataclass(kw_only=True)
class ActionDefinition:
    """Action definition that can be reused across multiple rules."""

    notify: NotifyActionConfig | None = None
    mark_as_read: MarkAsReadActionConfig | None = None
    ignore: IgnoreActionConfig | None = None

    @model_validator(mode="after")
    def validate_exactly_one(self) -> Self:
        n = sum(
            [
                self.notify is not None,
                self.mark_as_read is not None,
                self.ignore is not None,
            ]
        )
        if n != 1:
            raise ValueError(
                "Exactly one of 'notify', 'mark_as_read', or 'ignore' must be set"
            )
        return self


@dataclass(kw_only=True)
class RuleAction:
    """Action for a rule - either inline or by reference."""

    ref: str | None = None
    notify: NotifyActionConfig | None = None
    mark_as_read: MarkAsReadActionConfig | None = None
    ignore: IgnoreActionConfig | None = None

    @model_validator(mode="after")
    def validate_exactly_one(self) -> Self:
        has_ref = self.ref is not None
        has_inline = sum(
            [
                self.notify is not None,
                self.mark_as_read is not None,
                self.ignore is not None,
            ]
        )
        if has_ref and has_inline > 0:
            raise ValueError(
                "Cannot specify both 'ref' and inline action (notify/mark_as_read/ignore)"
            )
        if not has_ref and has_inline != 1:
            raise ValueError(
                "Must specify either 'ref' or exactly one of 'notify'/'mark_as_read'/'ignore'"
            )
        return self


@dataclass(kw_only=True)
class Rule:
    id: str
    expression: str
    subject_expression: str | None = None
    action: RuleAction


@dataclass(kw_only=True)
class OrgMasks:
    """Organization filtering configuration."""

    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @model_validator(mode="after")
    def validate_mutually_exclusive(self) -> Self:
        """Ensure include and exclude are not both specified."""
        if self.include and self.exclude:
            raise ValueError(
                "Cannot specify both 'include' and 'exclude' org masks. Use one or the other."
            )
        return self


@dataclass(kw_only=True)
class Masks:
    """Notification filtering masks applied at API level."""

    orgs: OrgMasks = field(default_factory=OrgMasks)


@dataclass(kw_only=True)
class SpoolConfig:
    """Configuration for the notified-notification spool."""

    dir: Path | None = None  # default: ${XDG_DATA_HOME}/signalsmith/spool


@dataclass(kw_only=True)
class NotifyActionsConfig:
    """Interactive "Dismiss"/"Ignore" action buttons on notifications.

    Only takes effect in `signalsmith daemon` (not `run`, and not with `daemon
    --non-interactive`): a one-shot process can't stay alive to catch a
    button press. `max_concurrent`/`wait_timeout` govern the sliding-window
    concurrency limit on button-bearing notifications only - plain
    click-to-open notifications aren't throttled.
    """

    enabled: bool = False
    max_concurrent: int = 5
    wait_timeout: int = 20  # seconds


@dataclass(kw_only=True)
class Config:
    version: SchemaVersion = CONFIG_VERSION
    actions: dict[str, ActionDefinition] = field(default_factory=dict)
    masks: Masks = field(default_factory=Masks)
    rules: list[Rule]
    renotify_interval: int = 3600
    poll_interval: int = 300
    default_action: DefaultAction = DefaultAction.NOTIFY
    variables: dict[str, Any] = field(default_factory=dict)
    spool: SpoolConfig = field(default_factory=SpoolConfig)
    notify_actions: NotifyActionsConfig = field(default_factory=NotifyActionsConfig)

    @model_validator(mode="after")
    def validate_unique_rule_ids(self) -> Self:
        """Ensure all rule IDs are unique."""
        rule_ids = [rule.id for rule in self.rules]
        duplicates = [id for id in rule_ids if rule_ids.count(id) > 1]
        if duplicates:
            unique_duplicates = list(set(duplicates))
            raise ValueError(
                f"Duplicate rule IDs found: {', '.join(unique_duplicates)}. "
                f"All rule IDs must be unique."
            )
        return self

    @model_validator(mode="after")
    def validate_action_refs(self) -> Self:
        """Ensure all action refs point to defined actions."""
        for rule in self.rules:
            if rule.action.ref and rule.action.ref not in self.actions:
                raise ValueError(
                    f"Rule {rule.id!r} references undefined action {rule.action.ref!r}"
                )
        return self

    @classmethod
    def resolve_config_dir(cls) -> Path:
        """Resolve the config dir (SIGNALSMITH_CONFIG_DIR > XDG default)."""
        env_dir = os.environ.get("SIGNALSMITH_CONFIG_DIR")
        if env_dir:
            return Path(env_dir)
        return xdg_config_home() / "signalsmith"

    @classmethod
    def resolve_config_path(cls, config_file: Path | None = None) -> Path:
        """Resolve the config file path (explicit arg > SIGNALSMITH_CONFIG > config dir default)."""
        if config_file is not None:
            return config_file
        env_config = os.environ.get("SIGNALSMITH_CONFIG")
        if env_config:
            return Path(env_config)
        return cls.resolve_config_dir() / "config.yaml"

    @classmethod
    def resolve_test_dir(cls, tests_dir: Path | None = None) -> Path:
        """Resolve the test dir (explicit arg > SIGNALSMITH_TEST_DIR > config dir default)."""
        if tests_dir is not None:
            return tests_dir
        env_dir = os.environ.get("SIGNALSMITH_TEST_DIR")
        if env_dir:
            return Path(env_dir)
        return cls.resolve_config_dir() / "tests"

    @classmethod
    def load(cls, config_file: Path | None = None) -> Config:
        config_file = cls.resolve_config_path(config_file)
        logger.debug("Loading config from %s", config_file)
        with config_file.open() as fh:
            data = yaml.safe_load(fh)
        check_file_version(
            data,
            CONFIG_VERSION,
            label=f"Config file {config_file}",
            remedy="Update the config file to a compatible version.",
        )
        return CONFIG_ADAPTER.validate_python(data)


CONFIG_ADAPTER: TypeAdapter[Config] = TypeAdapter(Config)
