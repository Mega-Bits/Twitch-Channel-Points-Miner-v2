"""Finish active in-progress Drop campaigns outside the configured game list."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from TwitchChannelPointsMiner import drop_game_main_list_preference_patch
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Settings import FollowersOrder

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_finish_started_drops_patch"
_SENTINEL_GAME = "__finish_started_drops_inventory__"
_RUN_CONFIG: dict[int, dict[str, Any]] = {}


def _clean_games(values: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value).strip()
            for value in (values or ())
            if str(value).strip()
        )
    )


def _game_name(campaign: Any) -> str | None:
    game = getattr(campaign, "game", None)
    if isinstance(game, dict):
        value = game.get("displayName") or game.get("name")
    else:
        value = game
    rendered = str(value or "").strip()
    return rendered or None


def _now_for(value: Any) -> datetime:
    timezone = getattr(value, "tzinfo", None)
    return datetime.now(timezone) if timezone is not None else datetime.utcnow()


def _is_active_inventory_campaign(campaign: Any) -> bool:
    if getattr(campaign, "in_inventory", False) is not True:
        return False
    if not list(getattr(campaign, "drops", ()) or ()):
        return False

    now = None
    start_at = getattr(campaign, "start_at", None)
    if start_at is not None:
        now = _now_for(start_at)
        if start_at > now:
            return False

    end_at = getattr(campaign, "end_at", None)
    if end_at is not None:
        if now is None or getattr(now, "tzinfo", None) != getattr(end_at, "tzinfo", None):
            now = _now_for(end_at)
        if end_at <= now:
            return False
    return True


def _progress(campaign: Any) -> float:
    return max(
        (
            float(getattr(drop, "percentage_progress", 0) or 0)
            for drop in (getattr(campaign, "drops", ()) or ())
        ),
        default=0.0,
    )


def _started_unmonitored_campaigns(
    campaigns: list[Any],
    explicit_games: tuple[str, ...],
) -> list[Any]:
    explicit = {drop_games_patch._normalize(game) for game in explicit_games}
    selected = []
    for campaign in campaigns:
        game = _game_name(campaign)
        if not game or drop_games_patch._normalize(game) in explicit:
            continue
        if _is_active_inventory_campaign(campaign):
            selected.append(campaign)
    selected.sort(key=_progress, reverse=True)
    return selected


def _configured_campaigns_with_started(
    config: dict[str, Any],
    campaigns: list[Any],
) -> list[Any]:
    explicit_config = dict(config)
    explicit_config["games"] = tuple(config.get("explicit_games", ()))
    explicit = _ORIGINAL_CONFIGURED_CAMPAIGNS(explicit_config, campaigns)

    campaign_by_id = {
        getattr(campaign, "id", None): campaign
        for campaign in campaigns
    }
    resumed = [
        campaign_by_id[campaign_id]
        for campaign_id in config.get("started_campaign_ids", ())
        if campaign_id in campaign_by_id
    ]

    ordered = []
    seen = set()
    for campaign in resumed + explicit:
        campaign_id = getattr(campaign, "id", None)
        if campaign_id in seen:
            continue
        seen.add(campaign_id)
        ordered.append(campaign)
    return ordered


def _refresh_with_started(twitch: Any, streamers: list[Any], campaigns: list[Any]) -> None:
    config = drop_games_patch._CONFIG.get(id(twitch))
    run_config = _RUN_CONFIG.get(id(twitch))
    if not config or run_config is None:
        return _ORIGINAL_REFRESH(twitch, streamers, campaigns)

    explicit_games = tuple(run_config.get("explicit_games", ()))
    resumed = (
        _started_unmonitored_campaigns(campaigns, explicit_games)
        if run_config.get("enabled")
        else []
    )
    campaign_ids = tuple(
        campaign_id
        for campaign_id in (getattr(campaign, "id", None) for campaign in resumed)
        if campaign_id is not None
    )
    combined_games = []
    seen_games = set()
    for game in (
        *(_game_name(campaign) for campaign in resumed),
        *explicit_games,
    ):
        if not game:
            continue
        normalized = drop_games_patch._normalize(game)
        if normalized in seen_games:
            continue
        seen_games.add(normalized)
        combined_games.append(game)

    config["explicit_games"] = explicit_games
    config["started_campaign_ids"] = campaign_ids
    config["games"] = tuple(combined_games)

    previous_ids = set(run_config.get("campaign_ids", ()))
    current_ids = set(campaign_ids)
    by_id = {getattr(campaign, "id", None): campaign for campaign in resumed}
    for campaign_id in current_ids - previous_ids:
        campaign = by_id.get(campaign_id)
        logger.info(
            "Finishing started unmonitored Drop campaign %s (%s)",
            getattr(campaign, "name", campaign_id),
            _game_name(campaign) or "unknown game",
        )
    run_config["campaign_ids"] = campaign_ids

    return _ORIGINAL_REFRESH(twitch, streamers, campaigns)


def get_explicit_drop_games(twitch: Any) -> tuple[str, ...]:
    """Return only games explicitly supplied through drop_games."""
    run_config = _RUN_CONFIG.get(id(twitch))
    if run_config is not None:
        return tuple(run_config.get("explicit_games", ()))
    return tuple(
        game
        for game in _ORIGINAL_GET_DROP_GAMES(twitch)
        if game != _SENTINEL_GAME
    )


def is_started_drop_resume(twitch: Any, campaign_id: Any) -> bool:
    """Return whether a campaign is currently included by the resume option."""
    run_config = _RUN_CONFIG.get(id(twitch)) or {}
    return campaign_id in set(run_config.get("campaign_ids", ()))


def apply_patch() -> None:
    """Install the opt-in started Drop resume configuration."""
    if getattr(TwitchChannelPointsMiner, _PATCH_MARKER, False):
        return

    original_run = TwitchChannelPointsMiner.run

    def run_with_started_drops(
        self: TwitchChannelPointsMiner,
        streamers: list[Any] = [],
        blacklist: list[str] = [],
        followers: bool = False,
        followers_order: FollowersOrder = FollowersOrder.ASC,
        drop_games: Any = None,
        drop_game_limit: int = 10,
        finish_started_drops: bool = False,
    ) -> Any:
        explicit_games = _clean_games(drop_games)
        enabled = bool(finish_started_drops)
        effective_games = explicit_games
        if enabled and not effective_games:
            effective_games = (_SENTINEL_GAME,)

        _RUN_CONFIG[id(self.twitch)] = {
            "enabled": enabled,
            "explicit_games": explicit_games,
            "campaign_ids": (),
        }
        try:
            return original_run(
                self,
                streamers=streamers,
                blacklist=blacklist,
                followers=followers,
                followers_order=followers_order,
                drop_games=effective_games,
                drop_game_limit=drop_game_limit,
            )
        finally:
            _RUN_CONFIG.pop(id(self.twitch), None)

    def mine_with_started_drops(
        self: TwitchChannelPointsMiner,
        streamers: list[Any] = [],
        blacklist: list[str] = [],
        followers: bool = False,
        followers_order: FollowersOrder = FollowersOrder.ASC,
        drop_games: Any = None,
        drop_game_limit: int = 10,
        finish_started_drops: bool = False,
    ) -> Any:
        return self.run(
            streamers=streamers,
            blacklist=blacklist,
            followers=followers,
            followers_order=followers_order,
            drop_games=drop_games,
            drop_game_limit=drop_game_limit,
            finish_started_drops=finish_started_drops,
        )

    global _ORIGINAL_CONFIGURED_CAMPAIGNS
    global _ORIGINAL_REFRESH
    global _ORIGINAL_GET_DROP_GAMES
    _ORIGINAL_CONFIGURED_CAMPAIGNS = (
        drop_game_main_list_preference_patch._configured_campaigns
    )
    _ORIGINAL_REFRESH = drop_games_patch._refresh_game_streamers
    _ORIGINAL_GET_DROP_GAMES = drop_games_patch.get_drop_games

    setattr(run_with_started_drops, _PATCH_MARKER, True)
    setattr(mine_with_started_drops, _PATCH_MARKER, True)
    TwitchChannelPointsMiner.run = run_with_started_drops
    TwitchChannelPointsMiner.mine = mine_with_started_drops
    drop_game_main_list_preference_patch._configured_campaigns = (
        _configured_campaigns_with_started
    )
    drop_games_patch._refresh_game_streamers = _refresh_with_started
    drop_games_patch.get_drop_games = get_explicit_drop_games
    setattr(TwitchChannelPointsMiner, _PATCH_MARKER, True)
