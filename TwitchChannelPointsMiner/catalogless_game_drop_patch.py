"""Farm explicitly configured Drop games when Twitch returns no campaign catalog."""

from __future__ import annotations

import logging
from typing import Any

from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_game_main_list_preference_patch as main_preference
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import explicit_game_drop_discovery_patch as discovery
from TwitchChannelPointsMiner import finish_started_drops_patch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_catalogless_game_drop_patch"
_CATALOGLESS_PREFIX = "__game_only_drop__:"
_CATALOGLESS_FALLBACK_ID = "__game_only_directory__"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _explicit_games(twitch: Any) -> tuple[str, ...]:
    return tuple(
        game
        for game in finish_started_drops_patch.get_explicit_drop_games(twitch)
        if str(game).strip()
    )


def _stream_game_values(streamer: Any) -> set[str]:
    game = getattr(getattr(streamer, "stream", None), "game", {}) or {}
    if not isinstance(game, dict):
        return {drop_games_patch._normalize(game)} if game else set()
    return {
        drop_games_patch._normalize(value)
        for value in (game.get("id"), game.get("name"), game.get("displayName"))
        if value not in (None, "")
    }


def _matching_catalog_campaigns(campaigns: list[Any], game_name: str) -> list[Any]:
    return list(drop_games_patch._matching_campaigns(campaigns, game_name))


def _refresh_catalogless_game_streamers(
    twitch: Any,
    streamers: list[Any],
    campaigns: list[Any],
) -> Any:
    config = drop_games_patch._CONFIG.get(id(twitch))
    if not config:
        return _ORIGINAL_MAIN_REFRESH(twitch, streamers, campaigns)

    previous_mapping = dict(config.get("catalogless_streamer_games", {}) or {})
    protected: dict[int, bool] = {}
    for streamer in streamers:
        if (
            streamer.username in previous_mapping
            and _source(streamer) == "game_drop"
        ):
            protected[id(streamer)] = bool(streamer.is_online)
            streamer.is_online = False

    try:
        result = _ORIGINAL_MAIN_REFRESH(twitch, streamers, campaigns)
    finally:
        for streamer in streamers:
            if id(streamer) in protected:
                streamer.is_online = protected[id(streamer)]

    known = {streamer.username: streamer for streamer in streamers}
    catalogless_mapping: dict[str, str] = {}
    generated_seen: set[str] = set()
    diagnostics: list[tuple[str, int]] = []

    for game_name in _explicit_games(twitch):
        if _matching_catalog_campaigns(campaigns, game_name):
            continue

        nodes = drop_games_patch._directory_nodes(twitch, game_name, config)
        diagnostics.append((game_name, len(nodes)))
        for node in nodes:
            broadcaster = node.get("broadcaster") or {}
            login = str(broadcaster.get("login") or "").lower().strip()
            if not login:
                continue

            catalogless_mapping[login] = game_name
            streamer = known.get(login)
            if streamer is None:
                streamer = drop_games_patch.Streamer(
                    login,
                    settings=drop_games_patch._settings(),
                    source="game_drop",
                )
                streamer.channel_id = str(broadcaster.get("id") or "")
                streamer.fallback_campaign_ids = frozenset({_CATALOGLESS_FALLBACK_ID})
                streamers.append(streamer)
                known[login] = streamer
                logger.info(
                    "Added game-directory-only Drop channel %s for %s",
                    login,
                    game_name,
                )
            elif _source(streamer) == "game_drop":
                streamer.fallback_campaign_ids = frozenset({_CATALOGLESS_FALLBACK_ID})

            if _source(streamer) == "game_drop":
                generated_seen.add(login)

            try:
                twitch.check_streamer_online(streamer)
            except Exception as exc:
                logger.warning(
                    "Unable to refresh game-directory-only Drop channel %s: %s",
                    login,
                    exc,
                )

    config["catalogless_streamer_games"] = catalogless_mapping
    signature = tuple(diagnostics)
    if config.get("catalogless_directory_diagnostic") != signature:
        config["catalogless_directory_diagnostic"] = signature
        for game_name, count in diagnostics:
            if count:
                logger.info(
                    "Campaign catalog unavailable for explicit Drop game %s; using %s DROPS_ENABLED directory candidate(s)",
                    game_name,
                    count,
                )
            else:
                logger.info(
                    "Campaign catalog unavailable for explicit Drop game %s and no DROPS_ENABLED directory channel was found",
                    game_name,
                )

    for streamer in streamers:
        if _source(streamer) != "game_drop":
            continue
        if streamer.username in generated_seen:
            continue
        if streamer.username not in previous_mapping:
            continue
        streamer.fallback_campaign_ids = frozenset()
        if streamer.is_online:
            streamer.set_offline()

    return result


def _catalogless_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
) -> tuple[str, int, str] | None:
    mapping = dict(config.get("catalogless_streamer_games", {}) or {})
    if not mapping:
        return None

    for game_name in _explicit_games(twitch):
        target = drop_games_patch._normalize(game_name)
        candidates: list[int] = []
        for index, streamer in enumerate(streamers):
            mapped_game = mapping.get(streamer.username)
            if mapped_game is None or drop_games_patch._normalize(mapped_game) != target:
                continue
            if not configured._watchable(streamer):
                continue
            if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
                continue
            stream_games = _stream_game_values(streamer)
            if stream_games and target not in stream_games:
                continue
            candidates.append(index)

        if not candidates:
            continue
        candidates.sort(
            key=lambda index: (
                _source(streamers[index]) in _FALLBACK_SOURCES,
                index,
            )
        )
        config["active_catalogless_game"] = game_name
        campaign_id = f"{_CATALOGLESS_PREFIX}{target}"
        return campaign_id, candidates[0], "game_drop"

    config.pop("active_catalogless_game", None)
    return None


def _drop_candidate_with_catalogless_game(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    real_candidate = discovery._ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)
    if real_candidate is not None and real_candidate[2] == "game_drop":
        config.pop("active_catalogless_game", None)
        return real_candidate

    catalogless = _catalogless_candidate(twitch, streamers, config)
    if catalogless is not None:
        config.pop("explicit_drop_diagnostic", None)
        return catalogless

    config.pop("active_catalogless_game", None)
    return _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)


def _catalogless_selection(twitch: Any, streamer: Any) -> tuple[dict[str, Any], str] | None:
    config = drop_games_patch._CONFIG.get(id(twitch)) or {}
    if config.get("active_selection_streamer") != getattr(streamer, "username", None):
        return None
    campaign_id = str(config.get("active_selection_campaign_id") or "")
    game = str(config.get("active_catalogless_game") or "").strip()
    if not campaign_id.startswith(_CATALOGLESS_PREFIX) or not game:
        return None
    return config, game


def _install_dashboard_support() -> None:
    from TwitchChannelPointsMiner import status_dashboard_enhancements_patch as enhancements
    from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
    from TwitchChannelPointsMiner import watch_notifications_patch

    current_slot_snapshot = dashboard._slot_snapshot

    def slot_snapshot_with_catalogless_game(twitch: Any, streamer: Any) -> dict[str, Any]:
        slot = current_slot_snapshot(twitch, streamer)
        selected = _catalogless_selection(twitch, streamer)
        if selected is not None:
            _, game = selected
            slot["reason"] = "Game drop"
            slot["catalogless_game"] = game
            slot["campaign"] = None
        return slot

    dashboard._slot_snapshot = slot_snapshot_with_catalogless_game

    current_render_overview = dashboard.DashboardState._render_overview

    def render_overview_with_catalogless_game(self: Any, snapshot: dict[str, Any]) -> str:
        text = current_render_overview(self, snapshot)
        extra_lines = []
        for position, slot in enumerate(snapshot.get("watch_slots", ()), start=1):
            game = slot.get("catalogless_game")
            if game:
                extra_lines.append(
                    f"{position}. `{game}` Drop directory selected; waiting for Twitch to expose campaign progress"
                )
        if not extra_lines:
            return text
        return text + "\n" + "\n".join(extra_lines)

    dashboard.DashboardState._render_overview = render_overview_with_catalogless_game

    current_watch_reason = watch_notifications_patch._watch_reason

    def watch_reason_with_catalogless_game(twitch: Any, streamer: Any):
        selected = _catalogless_selection(twitch, streamer)
        if selected is None:
            return current_watch_reason(twitch, streamer)
        _, game = selected
        return (
            "Game drop",
            f"**Game:** {game}\n"
            "**Campaign:** waiting for Twitch catalog/inventory data\n"
            "**Drop:** progress will appear after Twitch starts the session",
        )

    watch_notifications_patch._watch_reason = watch_reason_with_catalogless_game


def apply_patch() -> None:
    """Install game-directory-only farming below real explicit campaigns and above completion."""
    if getattr(priority_order, _PATCH_MARKER, False):
        return

    global _ORIGINAL_MAIN_REFRESH
    global _ORIGINAL_DROP_CANDIDATE
    _ORIGINAL_MAIN_REFRESH = finish_started_drops_patch._ORIGINAL_REFRESH
    _ORIGINAL_DROP_CANDIDATE = priority_order._drop_candidate

    finish_started_drops_patch._ORIGINAL_REFRESH = _refresh_catalogless_game_streamers
    priority_order._drop_candidate = _drop_candidate_with_catalogless_game
    _install_dashboard_support()
    setattr(priority_order, _PATCH_MARKER, True)
