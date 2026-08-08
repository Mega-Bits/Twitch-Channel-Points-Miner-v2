"""Bind dashboard watch-slot updates to the final watch selector."""

from __future__ import annotations

from typing import Any

from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner.classes.Twitch import Twitch

_PATCH_MARKER = "_status_dashboard_final_selector_patch"


def apply_patch() -> None:
    """Wrap the final selector so dashboard slots see authoritative results."""
    current = getattr(Twitch, "_select_streamers_to_watch", None)
    if current is None or getattr(current, _PATCH_MARKER, False):
        return

    def select_with_final_dashboard(
        self: Twitch,
        streamers: list[Any],
        priority: list[Any],
        max_watch_amount: int = 2,
    ) -> list[int]:
        selected = list(current(self, streamers, priority, max_watch_amount))
        with dashboard._STATE_LOCK:
            state = dashboard._STATES.get(id(self))
        if state is not None:
            state.update_watch(streamers, selected)
        return selected

    setattr(select_with_final_dashboard, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_final_dashboard
