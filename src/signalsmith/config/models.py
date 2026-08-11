import logging
import os
from dataclasses import field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import jinja2
import yaml
from pydantic import ConfigDict, TypeAdapter, model_validator
from pydantic.dataclasses import dataclass
from xdg import xdg_config_home

from ..versioning import CONFIG_VERSION, SchemaVersion, check_file_version

logger = logging.getLogger(__name__)

__all__: list[str] = []


class DefaultAction(StrEnum):
    """Default action for unmatched notifications."""

    NOTIFY = "notify"
    IGNORE = "ignore"


class ActionKind(StrEnum):
    """Configurable action kinds - the values users write in YAML.

    Every member's value must match a field name shared by `ActionDefinition`
    and `RuleAction` below - `validate_exactly_one` on both, and
    `actions.registry.ACTION_SPECS`, iterate this enum rather than hardcoding
    the three kinds by name. Deliberately excludes `SkipAction`
    (`actions/skip.py`): skipping is what a `notify` action degrades to when
    the renotify interval hasn't elapsed, never something a user configures
    directly.
    """

    NOTIFY = "notify"
    MARK_AS_READ = "mark_as_read"
    IGNORE = "ignore"


def _oxford_join(items: list[str]) -> str:
    if len(items) <= 2:
        return " or ".join(items)
    return ", ".join(items[:-1]) + f", or {items[-1]}"


_ACTION_KIND_VALUES = [kind.value for kind in ActionKind]
_ACTION_KIND_LIST = _oxford_join([f"'{v}'" for v in _ACTION_KIND_VALUES])
_ACTION_KIND_SLASH_LIST = "/".join(_ACTION_KIND_VALUES)
_ACTION_KIND_QUOTED_SLASH_LIST = "/".join(f"'{v}'" for v in _ACTION_KIND_VALUES)

# Jinja template text (rendered by `templating.py`). Defined here rather than
# in `templating.py` so `NoticeConfig` can use them as field defaults without
# a config -> templating -> config import cycle. `templating._static_default_title`/
# `_static_default_body` independently mirror this text as plain Python (so
# they can never themselves raise) - kept in sync by
# `test_templating.py::test_default_config_matches_builtin_default`.
DEFAULT_NOTICE_TITLE = (
    "{{ notification.subject.type }}: {{ notification.subject.title }}"
)
DEFAULT_NOTICE_BODY = (
    "{{ notification.repository.full_name }} ({{ notification.reason }})"
)


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class NoticeConfig:
    """Top-level `notice:` block: the generic notice computed for every
    notification, before any rule-specific `notify.title`/`notify.body`
    override (see `NotifyActionConfig`)."""

    title: str = DEFAULT_NOTICE_TITLE
    body: str = DEFAULT_NOTICE_BODY


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class NotifyActionConfig:
    # Jinja templates (see `templating.py`); `None` defaults to the
    # corresponding rendered `notice.title`/`notice.body`.
    title: str | None = None
    body: str | None = None


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class MarkAsReadActionConfig:
    pass


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class IgnoreActionConfig:
    pass


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class ActionDefinition:
    """Action definition that can be reused across multiple rules."""

    notify: NotifyActionConfig | None = None
    mark_as_read: MarkAsReadActionConfig | None = None
    ignore: IgnoreActionConfig | None = None

    @model_validator(mode="after")
    def validate_exactly_one(self) -> Self:
        n = sum(getattr(self, kind.value) is not None for kind in ActionKind)
        if n != 1:
            raise ValueError(f"Exactly one of {_ACTION_KIND_LIST} must be set")
        return self


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class RuleAction:
    """Action for a rule - either inline or by reference."""

    ref: str | None = None
    notify: NotifyActionConfig | None = None
    mark_as_read: MarkAsReadActionConfig | None = None
    ignore: IgnoreActionConfig | None = None

    @model_validator(mode="after")
    def validate_exactly_one(self) -> Self:
        has_ref = self.ref is not None
        has_inline = sum(getattr(self, kind.value) is not None for kind in ActionKind)
        if has_ref and has_inline > 0:
            raise ValueError(
                f"Cannot specify both 'ref' and inline action ({_ACTION_KIND_SLASH_LIST})"
            )
        if not has_ref and has_inline != 1:
            raise ValueError(
                f"Must specify either 'ref' or exactly one of {_ACTION_KIND_QUOTED_SLASH_LIST}"
            )
        return self


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class Rule:
    id: str
    expression: str
    action: RuleAction

    @model_validator(mode="after")
    def validate_expression_syntax(self) -> Self:
        """Catch a Jinja syntax error at config-load time, not at match time.

        Local import: `templating` imports `NoticeConfig`/`NotifyActionConfig`
        from this module, so a module-level import here would cycle.
        """
        from .. import templating

        try:
            templating.compile_expression(self.expression)
        except jinja2.TemplateSyntaxError as exc:
            raise ValueError(
                f"Rule {self.id!r} has an invalid expression: "
                f"{self.expression!r}: {exc}"
            ) from exc
        return self


# Dumps a matched `Rule` to a plain dict for `state.models.SpoolEntry.rule` -
# see the comment there for why the spool stores raw JSON rather than a `Rule`.
RULE_ADAPTER: TypeAdapter[Rule] = TypeAdapter(Rule)


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
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


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class Masks:
    """Notification filtering masks applied at API level."""

    orgs: OrgMasks = field(default_factory=OrgMasks)


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class SpoolConfig:
    """Configuration for the notified-notification spool."""

    dir: Path | None = None  # default: ${XDG_DATA_HOME}/signalsmith/spool


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
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


@dataclass(kw_only=True, config=ConfigDict(extra="forbid"))
class Config:
    version: SchemaVersion = CONFIG_VERSION
    actions: dict[str, ActionDefinition] = field(default_factory=dict)
    masks: Masks = field(default_factory=Masks)
    notice: NoticeConfig = field(default_factory=NoticeConfig)
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
