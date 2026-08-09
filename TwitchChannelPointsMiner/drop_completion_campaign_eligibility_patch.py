"""Keep started Drop completion off generic game-directory candidates."""

from __future__ import annotations

import logging
from typing import Any

from TwitchChannelPointsMiner import drop_completion_game_drop_arbitration_patch as controller

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_drop_completion_campaign_eligibility_patch"
_GENERIC_GAME_DIRECTORY_SOURCE = "game_drop"


def _is_generic_game_directory(streamer: Any) -> bool:
    return controller._source(streamer) == _GENERIC_GAME_DIRECTORY_SOURCE


def apply_patch() -> None:
    """Require campaign-specific evidence for started Drop completion channels."""
    current_completion = controller._completion_candidate
    if getattr(current_completion, _PATCH_MARKER, False):
        return

    current_observe = controller._observe_slot

    def completion_without_generic_game_directory(
        twitch: Any,
        streamers: list[Any],
        config: dict[str, Any],
        excluded_indexes: set[int],
    ):
        hidden: dict[int, bool] = {}
        for index, streamer in enumerate(streamers):
            if not _is_generic_game_directory(streamer):
                continue
            hidden[index] = bool(getattr(streamer, "is_online", False))
            streamer.is_online = False
        try:
            return current_completion(twitch, streamers, config, excluded_indexes)
        finally:
            for index, was_online in hidden.items():
                streamers[index].is_online = was_online

    def observe_without_generic_completion_owner(
        twitch: Any,
        streamers: list[Any],
        config: dict[str, Any],
        name: str,
    ):
        state = controller._slot(config, name)
        if isinstance(state, dict) and state.get("kind") == "drop_completion":
            username = str(state.get("username") or "")
            index = controller._streamer_index(streamers, username)
            if index is not None and _is_generic_game_directory(streamers[index]):
                logger.info(
                    "Releasing generic DROPS_ENABLED game-directory channel %s from started Drop completion; exact campaign eligibility was not proven",
                    username,
                )
                controller._clear_slot(config, name)
                return None, None
        return current_observe(twitch, streamers, config, name)

    setattr(completion_without_generic_game_directory, _PATCH_MARKER, True)
    setattr(observe_without_generic_completion_owner, _PATCH_MARKER, True)
    controller._completion_candidate = completion_without_generic_game_directory
    controller._observe_slot = observe_without_generic_completion_owner
