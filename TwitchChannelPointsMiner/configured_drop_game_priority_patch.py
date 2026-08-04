"""Give configured Drop games priority over unrelated Drop-enabled streams."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_configured_drop_game_priority_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _campaign_ids(streamer: Any) -> set[Any]:
    stream = getattr(streamer, "stream", None)
    ids = set(getattr(stream, "campaigns_ids", ()) or ())
    for campaign in getattr(stream, "campaigns", ()) or ():
        campaign_id = getattr(campaign, "id", None)
        if campaign_id is not None and bool(getattr(campaign, "drops", ()) or ()):
            ids.add(campaign_id)
    return ids


def _game_values(game: Any) -> set[str]:
    values: set[str] = set()
    if not isinstance(game, dict):
        return values
    for key in ("id", "name", "displayName"):
        value = game.get(key)
        if value not in (None, ""):
            values.add(drop_games_patch._normalize(value))
    return values


def _same_game(streamer: Any, campaign: Any) -> bool:
    stream_game = getattr(getattr(streamer, "stream", None), "game", {}) or {}
    campaign_game = getattr(campaign, "game", {}) or {}

    stream_id = stream_game.get("id") if isinstance(stream_game, dict) else None
    campaign_id = campaign_game.get("id") if isinstance(campaign_game, dict) else None
    if stream_id not in (None, "") and campaign_id not in (None, ""):
        return str(stream_id) == str(campaign_id)
    return bool(_game_values(stream_game).intersection(_game_values(campaign_game)))


def _watchable(streamer: Any) -> bool:
    return (
        getattr(streamer, "is_online", False) is True
        and (
            getattr(streamer, "online_at", 0) == 0
            or (time.time() - getattr(streamer, "online_at", 0)) > 30
        )
    )


def _eligible_for_campaign(streamer: Any, campaign: Any) -> bool:
    if campaign is None or not _watchable(streamer):
        return False
    settings = getattr(streamer, "settings", None)
    if getattr(settings, "claim_drops", False) is not True:
        return False

    campaign_id = getattr(campaign, "id", None)
    if campaign_id is None or campaign_id not in _campaign_ids(streamer):
        return False
    if not _same_game(streamer, campaign):
        return False

    if _source(streamer) in _FALLBACK_SOURCES:
        fallback_ids = set(getattr(streamer, "fallback_campaign_ids", ()) or ())
        if fallback_ids and campaign_id not in fallback_ids:
            return False
    return True


def _has_any_drop_eligibility(streamer: Any) -> bool:
    settings = getattr(streamer, "settings", None)
    return (
        getattr(streamer, "is_online", False) is True
        and getattr(settings, "claim_drops", False) is True
        and bool(_campaign_ids(streamer))
    )


def _watch_streak_needed(streamer: Any) -> bool:
    settings = getattr(streamer, "settings", None)
    stream = getattr(streamer, "stream", None)
    offline_at = float(getattr(streamer, "offline_at", 0) or 0)
    return (
        _watchable(streamer)
        and getattr(settings, "watch_streak", False) is True
        and getattr(stream, "watch_streak_missing", False) is True
        and (offline_at == 0 or ((time.time() - offline_at) // 60) > 30)
        and float(getattr(stream, "minute_watched", 0) or 0) < 7
    )


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


def _preferred_index(
    streamers: list[Any],
    campaign: Any,
) -> int | None:
    candidates = [
        index
        for index, streamer in enumerate(streamers)
        if _eligible_for_campaign(streamer, campaign)
    ]
    if not candidates:
        return None

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
        campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
        preferred_index = _preferred_index(streamers, campaign)
        if preferred_index is None:
            missing_key = campaign_id
            if campaign_id is not None and config.get(
                "missing_priority_campaign"
            ) != missing_key:
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

        # Keep a Watch Streak in the normal slot even when that channel also
        # advertises another Drop. Once the streak is complete, avoid all other
        # Drop-eligible channels so only the configured campaign receives progress.
        streak_priority = [
            index
            for index in selected
            if index != preferred_index
            and _watch_streak_needed(streamers[index])
        ]
        non_drop_priority = [
            index
            for index in selected
            if index != preferred_index
            and index not in streak_priority
            and not _has_any_drop_eligibility(streamers[index])
        ]
        safe_priority = list(dict.fromkeys(streak_priority + non_drop_priority))
        return ([preferred_index] + safe_priority)[:max_watch_amount]

    setattr(select_with_configured_game_priority, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_configured_game_priority
