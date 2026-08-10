import logging
import re

from desktop_notifier.sync import DesktopNotifierSync
from pydantic.dataclasses import dataclass

from .config.models import NotifyActionConfig
from .github.models import GitHubNotification

logger = logging.getLogger(__name__)

__all__: list[str] = []

_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")


@dataclass(kw_only=True)
class RenderedNotification:
    """A notify config's title/message after template interpolation."""

    title: str
    message: str
    url: str | None = None


def _build_vars(notification: GitHubNotification) -> dict[str, str]:
    return {
        "notification.id": notification.id,
        "notification.reason": notification.reason,
        "notification.updated_at": notification.updated_at,
        "notification.subject.title": notification.subject.title,
        "notification.subject.type": notification.subject.type,
        "notification.subject.web_url": notification.subject.web_url or "",
        "notification.repository.name": notification.repository.name,
        "notification.repository.full_name": notification.repository.full_name,
    }


def format_template(template: str, notification: GitHubNotification) -> str:
    variables = _build_vars(notification)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))

    return _TEMPLATE_RE.sub(replace, template)


def render_notification(
    config: NotifyActionConfig, notification: GitHubNotification
) -> RenderedNotification:
    return RenderedNotification(
        title=format_template(config.title, notification),
        message=format_template(config.message, notification),
        url=notification.subject.web_url,
    )


def send_notification(rendered: RenderedNotification) -> None:
    """Fire-and-forget a plain notification: no click/dismiss/button support.

    Used by `run` always, and by `daemon` whenever it has no
    `NotificationDispatcher` (non-interactive, or dispatcher construction
    failed) - the process isn't guaranteed to stay alive to observe an
    interaction, so none is offered here.
    """
    logger.debug(
        "Sending notification: title=%r message=%r", rendered.title, rendered.message
    )
    try:
        DesktopNotifierSync(app_name="signalsmith").send(
            title=rendered.title, message=rendered.message
        )
    except Exception:
        logger.exception("Failed to send desktop notification")
