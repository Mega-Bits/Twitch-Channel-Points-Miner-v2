"""Stabilize explicit game-Drop handoff and quiet internal directory candidates."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import explicit_game_drop_discovery_patch as discovery
from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_game_drop_handoff_patch"
_STREAM_TRANSITION_MARKER = "_quiet_directory_stream_transition"
_DASHBOARD_MARKER = "_catalogless_dashboard_slot_detail"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _campaign_game_values(campaign: Any) -> set[str]:
    game = getattr(campaign, "game", {}) or {}
    if not isinstance(game, dict):
        return {drop_games_patch._normalize(game)} if game else set()
    return {
        drop_games_patch._normalize(value)
        for value in (game.get("id"), game.get("name"), game.get("displayName"))
        if value not in (None, "")
    }


def _same_game(campaign: Any, game_name: Any) -> bool:
    target = drop_games_patch._normalize(game_name)
    return bool(target) and target in _campaign_game_values(campaign)


def _sticky_handoff_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    candidate: tuple[Any, int, str] | None,
    previous_username: str | None,
    previous_game: str | None,
) -> tuple[Any, int, str] | None:
    if (
        candidate is None
        or candidate[2] != "game_drop"
        or not previous_username
        or not previous_game
    ):
        return candidate

    campaign_id, selected_index, kind = candidate
    if str(campaign_id).startswith(catalogless._CATALOGLESS_PREFIX):
        return candidate

    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    if campaign is None or not _same_game(campaign, previous_game):
        return candidate

    previous_index = next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == previous_username
        ),
        None,
    )
    if previous_index is None:
        return candidate

    previous_streamer = streamers[previous_index]
    if not configured._eligible_for_campaign(previous_streamer, campaign):
        return candidate

    if previous_index != selected_index:
        signature = (campaign_id, previous_username)
        if config.get("sticky_game_drop_handoff") != signature:
            logger.info(
                "Keeping %s while the explicit Drop game transitions to campaign %s",
                previous_username,
                getattr(campaign, "name", campaign_id),
            )
            config["sticky_game_drop_handoff"] = signature
    return campaign_id, previous_index, kind


def _install_sticky_selector() -> None:
    current = priority_order._drop_candidate
    if getattr(current, _PATCH_MARKER, False):
        return

    def select_with_sticky_game_handoff(
        twitch: Any,
        streamers: list[Any],
        config: dict[str, Any],
    ):
        previous_username = config.get("active_selection_streamer")
        previous_game = config.get("active_catalogless_game")
        candidate = current(twitch, streamers, config)
        return _sticky_handoff_candidate(
            twitch,
            streamers,
            config,
            candidate,
            previous_username,
            previous_game,
        )

    setattr(select_with_sticky_game_handoff, _PATCH_MARKER, True)
    priority_order._drop_candidate = select_with_sticky_game_handoff


def _set_directory_online_silently(streamer: Any) -> None:
    if streamer.is_online is not True:
        streamer.online_at = time.time()
        streamer.is_online = True
        try:
            streamer.stream.init_watch_streak()
        except (AttributeError, TypeError):
            pass
    streamer.toggle_chat()


def _set_directory_offline_silently(streamer: Any) -> None:
    if streamer.is_online is True:
        streamer.offline_at = time.time()
        streamer.is_online = False
    streamer.toggle_chat()


def _patch_streamer_transitions(streamer_class: Any) -> None:
    current_online = streamer_class.set_online
    if not getattr(current_online, _STREAM_TRANSITION_MARKER, False):

        def set_online_without_directory_event(self: Any):
            if _source(self) in _FALLBACK_SOURCES:
                _set_directory_online_silently(self)
                return None
            return current_online(self)

        setattr(set_online_without_directory_event, _STREAM_TRANSITION_MARKER, True)
        streamer_class.set_online = set_online_without_directory_event

    current_offline = streamer_class.set_offline
    if not getattr(current_offline, _STREAM_TRANSITION_MARKER, False):

        def set_offline_without_directory_event(self: Any):
            if _source(self) in _FALLBACK_SOURCES:
                _set_directory_offline_silently(self)
                return None
            return current_offline(self)

        setattr(set_offline_without_directory_event, _STREAM_TRANSITION_MARKER, True)
        streamer_class.set_offline = set_offline_without_directory_event


def _install_quiet_directory_transitions() -> None:
    seen: set[int] = set()
    for streamer_class in (Streamer, getattr(drop_games_patch, "Streamer", Streamer)):
        if id(streamer_class) in seen:
            continue
        seen.add(id(streamer_class))
        _patch_streamer_transitions(streamer_class)


def _install_quiet_directory_diagnostics() -> None:
    current = discovery._log_directory_assignment
    if getattr(current, _PATCH_MARKER, False):
        return

    def quiet_directory_assignment(streamer: Any, campaign: Any) -> None:
        # Selection itself is logged by drop_priority_order_patch._set_selection.
        # Logging every eligible directory candidate creates dozens of lines per sync.
        return None

    setattr(quiet_directory_assignment, _PATCH_MARKER, True)
    discovery._log_directory_assignment = quiet_directory_assignment


def _install_dashboard_slot_detail() -> None:
    current = dashboard.DashboardState._render_overview
    if getattr(current, _DASHBOARD_MARKER, False):
        return

    def render_catalogless_detail_under_streamer(
        self: Any,
        snapshot: dict[str, Any],
    ) -> str:
        rendered = current(self, snapshot)
        lines = [
            line
            for line in rendered.splitlines()
            if "Drop directory selected; waiting for Twitch to expose campaign progress"
            not in line
        ]

        for position, slot in enumerate(snapshot.get("watch_slots", ()), start=1):
            game = str(slot.get("catalogless_game") or "").strip()
            username = str(slot.get("username") or "").strip()
            if not game or not username:
                continue

            detail = (
                f"   Game: `{game}` · Drop: `waiting for Twitch campaign data`"
            )
            insert_at = next(
                (
                    index + 1
                    for index, line in enumerate(lines)
                    if line.startswith(f"{position}. ")
                    and f"https://twitch.tv/{username}" in line
                ),
                None,
            )
            if insert_at is None:
                lines.append(detail)
            else:
                lines.insert(insert_at, detail)
        return "\n".join(lines)

    setattr(render_catalogless_detail_under_streamer, _DASHBOARD_MARKER, True)
    dashboard.DashboardState._render_overview = render_catalogless_detail_under_streamer


def apply_patch() -> None:
    """Install stable campaign handoff, quiet candidate checks, and slot rendering."""
    if getattr(priority_order, _PATCH_MARKER, False):
        return

    _install_sticky_selector()
    _install_quiet_directory_transitions()
    _install_quiet_directory_diagnostics()
    _install_dashboard_slot_detail()
    setattr(priority_order, _PATCH_MARKER, True)
