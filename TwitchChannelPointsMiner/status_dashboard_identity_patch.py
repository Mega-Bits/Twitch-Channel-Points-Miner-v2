"""Expose the active miner version and dashboard renderer in status output."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from TwitchChannelPointsMiner import status_dashboard_patch as dashboard

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_status_dashboard_identity_patch"
_VERSION = Path(__file__).with_name("VERSION").read_text(encoding="utf-8").strip()


def _renderer_name() -> str:
    if getattr(
        dashboard.DashboardState,
        "_status_dashboard_enhancements_patch",
        False,
    ):
        return "enhanced"
    return "legacy"


def apply_patch() -> None:
    """Add an observable build identity to the dashboard and startup logs."""
    state_class = dashboard.DashboardState
    if getattr(state_class, _PATCH_MARKER, False):
        return

    original_render_overview = state_class._render_overview

    def render_overview_with_identity(
        self: Any,
        snapshot: dict[str, Any],
    ) -> str:
        rendered = original_render_overview(self, snapshot)
        lines = rendered.splitlines()
        identity = f"**Version:** `{_VERSION}` · renderer: `{_renderer_name()}`"
        if lines:
            lines.insert(1, identity)
        else:
            lines.append(identity)
        return "\n".join(lines)

    original_start = state_class.start

    def start_with_identity(self: Any) -> Any:
        logger.info(
            "Discord dashboard renderer active: version %s, renderer %s",
            _VERSION,
            _renderer_name(),
        )
        return original_start(self)

    setattr(render_overview_with_identity, _PATCH_MARKER, True)
    setattr(start_with_identity, _PATCH_MARKER, True)
    state_class._render_overview = render_overview_with_identity
    state_class.start = start_with_identity
    setattr(state_class, _PATCH_MARKER, True)
