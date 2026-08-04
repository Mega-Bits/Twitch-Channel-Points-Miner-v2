"""Enforce explicit game Drops before resumed Drops and normal priority."""

from __future__ import annotations

import logging
from typing import Any

from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_game_main_list_preference_patch
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import finish_started_drops_patch
from TwitchChannelPointsMiner.classes.Settings import Priority
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_drop_priority_order_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}
_SELECTION_KEYS = (
    "active_selection_campaign_id",
    "active_selection_streamer",
    "active_selection_kind",
)


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _campaign_game_values(campaign: Any) -> set[str]:
    game = getattr(campaign, "game", {}) or {}
    if not isinstance(game, dict):
        return {drop_games_patch._normalize(game)} if game else set()
    return {
        drop_games_patch._normalize(value)
        for value in (
            game.get("id"),
            game.get("name"),
            game.get("displayName"),
        )
        if value not in (None, "")
    }


def _explicit_games(twitch: Twitch, config: dict[str, Any]) -> set[str]:
    try:
        games = finish_started_drops_patch.get_explicit_drop_games(twitch)
    except (AttributeError, RuntimeError):
        games = config.get("explicit_games", ()) or config.get("games", ())
    return {
        drop_games_patch._normalize(game)
        for game in games
        if str(game).strip()
        and str(game).strip() != finish_started_drops_patch._SENTINEL_GAME
    }


def _is_explicit_campaign(
    twitch: Twitch,
    config: dict[str, Any],
    campaign: Any,
) -> bool:
    return bool(_campaign_game_values(campaign).intersection(_explicit_games(twitch, config)))


def _ordered_campaign_ids(config: dict[str, Any]) -> list[Any]:
    campaigns = config.get("campaigns_by_id", {}) or {}
    ordered = [
        campaign_id
        for campaign_id in config.get("campaign_order", ())
        if campaign_id in campaigns
    ]
    ordered.extend(campaign_id for campaign_id in campaigns if campaign_id not in ordered)
    return ordered


def _explicit_first_campaigns(
    twitch: Twitch,
    config: dict[str, Any],
) -> tuple[list[Any], list[Any]]:
    campaigns = config.get("campaigns_by_id", {}) or {}
    explicit: list[Any] = []
    completion: list[Any] = []
    for campaign_id in _ordered_campaign_ids(config):
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            continue
        target = explicit if _is_explicit_campaign(twitch, config, campaign) else completion
        target.append(campaign_id)
    return explicit, completion


def _prefer_existing_target(
    twitch: Twitch,
    config: dict[str, Any],
    campaign_ids: list[Any],
) -> list[Any]:
    preferred: list[Any] = []
    for campaign_id in (
        getattr(twitch, "locked_drop_campaign_id", None),
        config.get("target_campaign_id"),
    ):
        if campaign_id in campaign_ids and campaign_id not in preferred:
            preferred.append(campaign_id)
    preferred.extend(campaign_id for campaign_id in campaign_ids if campaign_id not in preferred)
    return preferred


def _drop_candidate(
    twitch: Twitch,
    streamers: list[Any],
    config: dict[str, Any],
) -> tuple[Any, int, str] | None:
    campaigns = config.get("campaigns_by_id", {}) or {}
    explicit_ids, completion_ids = _explicit_first_campaigns(twitch, config)

    for kind, campaign_ids in (
        ("game_drop", explicit_ids),
        ("drop_completion", completion_ids),
    ):
        for campaign_id in _prefer_existing_target(twitch, config, campaign_ids):
            campaign = campaigns.get(campaign_id)
            if campaign is None:
                continue
            preferred_index = configured._preferred_index(streamers, campaign)
            if preferred_index is not None:
                return campaign_id, preferred_index, kind
    return None


def _normal_candidates(streamers: list[Any], excluded_index: int) -> list[int]:
    return [
        index
        for index, streamer in enumerate(streamers)
        if index != excluded_index
        and _source(streamer) not in _FALLBACK_SOURCES
        and configured._watchable(streamer)
    ]


def _normal_priority_index(
    streamers: list[Any],
    priority: list[Any],
    excluded_index: int,
) -> int | None:
    candidates = _normal_candidates(streamers, excluded_index)
    if not candidates:
        return None

    for configured_priority in priority:
        if configured_priority == Priority.DROPS:
            # The dedicated first slot already owns Drop selection.
            continue
        if configured_priority == Priority.STREAK:
            streak = next(
                (
                    index
                    for index in candidates
                    if configured._watch_streak_needed(streamers[index])
                ),
                None,
            )
            if streak is not None:
                return streak
        elif configured_priority == Priority.ORDER:
            return candidates[0]
        elif configured_priority in (
            Priority.POINTS_ASCENDING,
            Priority.POINTS_DESCENDING,
        ):
            return sorted(
                candidates,
                key=lambda index: int(getattr(streamers[index], "channel_points", 0) or 0),
                reverse=configured_priority == Priority.POINTS_DESCENDING,
            )[0]
        elif configured_priority == Priority.SUBSCRIBED:
            subscribed = [
                index
                for index in candidates
                if streamers[index].viewer_has_points_multiplier()
            ]
            if subscribed:
                return sorted(
                    subscribed,
                    key=lambda index: streamers[index].total_points_multiplier(),
                    reverse=True,
                )[0]

    # A configuration containing only DROPS still gets the first online list
    # streamer in the normal slot after the dedicated Drop slot is assigned.
    return candidates[0]


def _set_selection(
    config: dict[str, Any],
    campaign_id: Any,
    streamer: Any,
    kind: str,
) -> None:
    selection = (campaign_id, streamer.username, kind)
    if config.get("priority_order_selection") != selection:
        label = "configured game Drop" if kind == "game_drop" else "started Drop completion"
        logger.info(
            "Selecting %s campaign %s via %s",
            label,
            campaign_id,
            streamer.username,
        )
        config["priority_order_selection"] = selection
    config["active_selection_campaign_id"] = campaign_id
    config["active_selection_streamer"] = streamer.username
    config["active_selection_kind"] = kind
    config["target_campaign_id"] = campaign_id


def _clear_selection(config: dict[str, Any]) -> None:
    for key in _SELECTION_KEYS:
        config.pop(key, None)


def _explicit_first_configured_campaigns(
    config: dict[str, Any],
    campaigns: list[Any],
) -> list[Any]:
    ordered = list(_ORIGINAL_CONFIGURED_CAMPAIGNS(config, campaigns))
    explicit_games = {
        drop_games_patch._normalize(game)
        for game in config.get("explicit_games", ())
        if str(game).strip()
    }
    if not explicit_games:
        return ordered

    explicit: list[Any] = []
    completion: list[Any] = []
    for campaign in ordered:
        target = (
            explicit
            if _campaign_game_values(campaign).intersection(explicit_games)
            else completion
        )
        target.append(campaign)
    return explicit + completion


def apply_patch() -> None:
    """Install the final deterministic two-slot selection order."""
    original_select = getattr(Twitch, "_select_streamers_to_watch", None)
    if original_select is None or getattr(original_select, _PATCH_MARKER, False):
        return

    global _ORIGINAL_CONFIGURED_CAMPAIGNS
    _ORIGINAL_CONFIGURED_CAMPAIGNS = drop_game_main_list_preference_patch._configured_campaigns
    drop_game_main_list_preference_patch._configured_campaigns = (
        _explicit_first_configured_campaigns
    )

    def select_with_explicit_drop_priority(
        self: Twitch,
        streamers: list[Any],
        priority: list[Any],
        max_watch_amount: int = 2,
    ) -> list[int]:
        selected = list(original_select(self, streamers, priority, max_watch_amount))
        config = drop_games_patch._CONFIG.get(id(self))
        if not config:
            return selected[:max_watch_amount]

        candidate = _drop_candidate(self, streamers, config)
        if candidate is None:
            _clear_selection(config)
            return selected[:max_watch_amount]

        campaign_id, drop_index, kind = candidate
        _set_selection(config, campaign_id, streamers[drop_index], kind)

        result = [drop_index]
        if max_watch_amount > 1:
            normal_index = _normal_priority_index(streamers, priority, drop_index)
            if normal_index is not None:
                result.append(normal_index)
        return result[:max_watch_amount]

    setattr(select_with_explicit_drop_priority, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_explicit_drop_priority
