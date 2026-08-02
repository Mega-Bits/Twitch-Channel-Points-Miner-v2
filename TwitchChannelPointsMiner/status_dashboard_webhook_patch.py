"""Route the persistent status dashboard to its own optional webhook."""

from __future__ import annotations

from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner.classes.Settings import Settings

_PATCH_MARKER = "_status_dashboard_webhook_patch"


class _DashboardWebhook:
    """Minimal endpoint object expected by the existing dashboard publisher."""

    __slots__ = ("webhook_api",)

    def __init__(self, webhook_api: str) -> None:
        self.webhook_api = webhook_api


def _configured_dashboard_webhook():
    logger_settings = getattr(Settings, "logger", None)
    discord = getattr(logger_settings, "discord", None) if logger_settings else None
    if discord is None:
        return None

    webhook_api = str(
        getattr(discord, "dashboard_webhook_api", "") or ""
    ).strip()
    if not webhook_api:
        return None
    return _DashboardWebhook(webhook_api)


def apply_patch() -> None:
    """Use only dashboard_webhook_api for persistent dashboard requests.

    An empty dashboard_webhook_api disables Discord dashboard publishing while
    leaving the normal event webhook and local dashboard state untouched.
    """
    state_class = dashboard.DashboardState
    current = state_class._discord
    if getattr(current, _PATCH_MARKER, False):
        return

    def dedicated_dashboard_webhook(self):
        return _configured_dashboard_webhook()

    setattr(dedicated_dashboard_webhook, _PATCH_MARKER, True)
    state_class._discord = dedicated_dashboard_webhook
