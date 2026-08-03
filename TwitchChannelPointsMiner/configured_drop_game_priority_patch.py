"""Give configured Drop games priority over unrelated Drop-enabled streams."""

from __future__ import annotations

import logging
from typing import Any

from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_configured_drop_game_priority_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _campaign_ids(streamer: Any) -> set[Any]:
    campaigns = getattr(getattr(streamer, "stream", None), "campaigns", ()) or ()
    return {
        getattr(campaign, "id", None)
        for campaign in campaigns
        if getattr(campaign, "id", None) is not None
        and bool(getattr(campaign, "drops", ()) or ())
    }


def _target_campaign_id(twitch: Twitch, config: dict[str, Any]) -> Any:
    campaign_by_id = config.get("campaigns_by_id", {}) or {}
    valid_ids = set(campaign_by_id)
    if not valid_ids:
        return None

    locked_id = getattr(twitch, "locked_drop_campaign_id", None)
    if locked_id in valid_ids:
        return locked_id

    configured_target = config.get("target_campaign_id")
    if configured_target in valid_ids:
        return configured_target

    order = [
        campaign_id
        for campaign_id in config.get("campaign_order", ())
        if campaign_id in valid_ids
    ]
    if not order:
        order = list(campaign_by_id)

    progress = getattr(twitch, "drop_campaign_progress", {}) or {}
    position = {campaign_id: index for index, campaign_id in enumerate(order)}
    return max(
        order,
        key=lambda campaign_id: (
            progress.get(campaign_id, 0),
            -position.get(campaign_id, 0),
        ),
    )


def _has_campaign_drop(streamer: Any, campaign_id: Any) -> bool:
    return (
        campaign_id is not None
        and drop_games_patch._has_drop(streamer)
        and campaign_id in _campaign_ids(streamer)
    )


def _preferred_index(
    streamers: list[Any],
    campaign_id: Any,
) -> int | None:
    candidates = [
        index
        for index, streamer in enumerate(streamers)
        if _has_campaign_drop(streamer, campaign_id)
    ]
    if not candidates:
        return None

    # Preserve the fork's main-list preference. Directory and campaign fallback
    # channels are considered only when no configured-list streamer qualifies.
    candidates.sort(
        key=lambda index: (
            _source(streamers[index]) in _FALLBACK_SOURCES,
            index,
        )
    )
    return candidates[0]


def _campaign_name(config: dict[str, Any], campaign_id: Any) -> str:
    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    return str(getattr(campaign, "name", campaign_id))


def _game_name(config: dict[str, Any], campaign_id: Any) -> str:
    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    game = getattr(campaign, "game", {}) or {}
    if isinstance(game, dict):
        value = game.get("displayName") or game.get("name")
    else:
        value = game
    return str(value or "unknown game")


def apply_patch() -> None:
    """Install configured-game selection after the existing Drop selectors."""
    original_select = getattr(Twitch, "_select_streamers_to_watch", None)
    if original_select is None or getattr(original_select, _PATCH_MARKER, False):
        return

    def select_with_configured_game_priority(
        self: Twitch,
        streamers: list[Any],
        priority: list[Any],
        max_watch_amount: int = 2,
    ) -> list[int]:
        selected = list(original_select(self, streamers, priority, max_watch_amount))
        config = drop_games_patch._CONFIG.get(id(self))
        if not config:
            return selected[:max_watch_amount]

        campaign_id = _target_campaign_id(self, config)
        preferred_index = _preferred_index(streamers, campaign_id)
        if preferred_index is None:
            missing_key = campaign_id
            if campaign_id is not None and config.get("missing_priority_campaign") != missing_key:
                logger.info(
                    "No eligible live channel found yet for configured Drop campaign %s (%s)",
                    _campaign_name(config, campaign_id),
                    _game_name(config, campaign_id),
                )
                config["missing_priority_campaign"] = missing_key
            return selected[:max_watch_amount]

        config.pop("missing_priority_campaign", None)
        config["target_campaign_id"] = campaign_id

        selection_key = (campaign_id, streamers[preferred_index].username)
        if config.get("forced_priority_selection") != selection_key:
            logger.info(
                "Prioritizing configured Drop game %s via %s for campaign %s",
                _game_name(config, campaign_id),
                streamers[preferred_index].username,
                _campaign_name(config, campaign_id),
            )
            config["forced_priority_selection"] = selection_key

        # A second Drop-enabled stream can steal progress from the configured
        # campaign. Keep only non-Drop priority selections beside the forced slot.
        safe_priority = [
            index
            for index in selected
            if index != preferred_index
            and not drop_games_patch._has_drop(streamers[index])
        ]
        return ([preferred_index] + safe_priority)[:max_watch_amount]

    setattr(select_with_configured_game_priority, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_configured_game_priority
