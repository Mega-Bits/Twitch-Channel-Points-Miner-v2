"""Centralize Drop-slot ownership so catalogless verification cannot flap."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import catalogless_game_drop_runtime_patch as runtime
from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import dual_game_drop_idle_slot_patch as dual
from TwitchChannelPointsMiner import game_drop_progress_verification_patch as verification
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_drop_slot_state_machine_patch"
_SECONDARY_MARKER = "_drop_completion_secondary_isolation_patch"
_PRIMARY_LEASE_KEY = "catalogless_primary_drop_lease"


def _synthetic_game(campaign_id: Any) -> str:
    value = str(campaign_id or "")
    if not value.startswith(catalogless._CATALOGLESS_PREFIX):
        return ""
    return value[len(catalogless._CATALOGLESS_PREFIX):]


def _breaker_active(config: dict[str, Any], game_key: str, now: float) -> bool:
    breakers = runtime._cleanup_breakers(config, now)
    return runtime._safe_number(breakers.get(game_key)) > now


def _streamer_index(streamers: list[Any], username: str) -> int | None:
    return next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )


def _lease(config: dict[str, Any]) -> dict[str, Any] | None:
    value = config.get(_PRIMARY_LEASE_KEY)
    return value if isinstance(value, dict) else None


def _clear_lease(config: dict[str, Any]) -> None:
    config.pop(_PRIMARY_LEASE_KEY, None)


def _restore_active_selection(config: dict[str, Any], lease: dict[str, Any]) -> None:
    config["active_selection_campaign_id"] = lease["campaign_id"]
    config["active_selection_streamer"] = lease["username"]
    config["active_selection_kind"] = "game_drop"
    config["target_campaign_id"] = lease["campaign_id"]
    if lease.get("game"):
        config["active_catalogless_game"] = lease["game"]


def _capture_primary_lease(
    twitch: Any,
    config: dict[str, Any],
    streamers: list[Any],
) -> None:
    """Capture a synthetic primary selection without resetting its timer."""
    if str(config.get("active_selection_kind") or "") != "game_drop":
        _clear_lease(config)
        return

    campaign_id = config.get("active_selection_campaign_id")
    game_key = _synthetic_game(campaign_id)
    username = str(config.get("active_selection_streamer") or "")
    if not game_key or not username:
        # Real campaigns already have Twitch-backed campaign identity and do
        # not need the catalogless provisional ownership lease.
        _clear_lease(config)
        return

    index = _streamer_index(streamers, username)
    if index is None:
        _clear_lease(config)
        return

    state = config.get("game_drop_progress_state")
    if (
        not isinstance(state, dict)
        or state.get("username") != username
        or state.get("game_key") != game_key
    ):
        context = verification._context(
            config,
            streamers,
            campaign_id,
            index,
            "game_drop",
        )
        if context is not None:
            verification._ensure_state(twitch, config, context)

    mapping = config.get("catalogless_streamer_games", {}) or {}
    game = str(
        config.get("active_catalogless_game")
        or mapping.get(username)
        or game_key
    ).strip()
    config[_PRIMARY_LEASE_KEY] = {
        "campaign_id": campaign_id,
        "username": username,
        "game_key": game_key,
        "game": game or game_key,
    }


def _lease_context(
    config: dict[str, Any],
    streamers: list[Any],
    lease: dict[str, Any],
) -> dict[str, Any] | None:
    index = _streamer_index(streamers, str(lease.get("username") or ""))
    if index is None:
        return None
    return verification._context(
        config,
        streamers,
        lease.get("campaign_id"),
        index,
        "game_drop",
    )


def _stable_primary(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
) -> tuple[int, str] | None:
    """Return the leased catalogless primary while verification is active."""
    lease = _lease(config)
    if lease is None:
        return None

    game_key = str(lease.get("game_key") or "")
    username = str(lease.get("username") or "")
    if not game_key or not username or _breaker_active(config, game_key, time.time()):
        _clear_lease(config)
        return None

    index = _streamer_index(streamers, username)
    if index is None:
        _clear_lease(config)
        return None

    # The lease is authoritative for the provisional selection. Reassert it
    # before observing progress so a transient lower-layer selection cannot
    # make _observe_active() discard and restart the 240-second timer.
    _restore_active_selection(config, lease)
    verification._observe_active(twitch, streamers, config)

    state = config.get("game_drop_progress_state")
    if not isinstance(state, dict):
        # Timeout/rejection clears this state. The failure wrapper then counts
        # the attempt and opens the per-game 24-hour breaker on the limit.
        _clear_lease(config)
        return None
    if state.get("username") != username or state.get("game_key") != game_key:
        _clear_lease(config)
        return None

    context = _lease_context(config, streamers, lease)
    if context is None or verification._is_rejected(config, context, time.time()):
        _clear_lease(config)
        return None

    streamer = streamers[index]
    if not configured._watchable(streamer):
        # Directory pagination is not an eligibility change. Confirm the exact
        # leased channel directly before releasing ownership.
        if not runtime._refresh_stream_directly(twitch, streamer):
            _clear_lease(config)
            return None
    if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
        _clear_lease(config)
        return None

    games = catalogless._stream_game_values(streamer)
    if games and game_key not in games:
        _clear_lease(config)
        return None

    priority_order._set_selection(
        config,
        lease["campaign_id"],
        streamer,
        "game_drop",
    )
    return index, game_key


def _secondary_candidate_without_completion(*args: Any, **kwargs: Any):
    candidate = _ORIGINAL_SECONDARY_CANDIDATE(*args, **kwargs)
    if isinstance(candidate, dict) and candidate.get("kind") == "drop_completion":
        return None
    return candidate


def _sticky_secondary_without_completion(*args: Any, **kwargs: Any):
    candidate = _ORIGINAL_STICKY_SECONDARY(*args, **kwargs)
    if isinstance(candidate, dict) and candidate.get("kind") == "drop_completion":
        return None
    return candidate


def _stable_slots(
    twitch: Any,
    streamers: list[Any],
    priority: list[Any],
    max_watch_amount: int,
    config: dict[str, Any],
    primary_index: int,
    primary_game: str,
) -> list[int]:
    result = [primary_index]
    if max_watch_amount < 2:
        dual._clear_secondary(config)
        return result

    # A real configured-list streamer owns slot 2. The helper is patched by
    # the retry layer to require an actually watchable list streamer rather
    # than stale is_online state.
    if dual._has_online_list_streamer(streamers):
        dual._clear_secondary(config)
        normal_index = priority_order._normal_priority_index(
            streamers,
            priority,
            primary_index,
        )
        if normal_index is not None:
            result.append(normal_index)
        return result[:max_watch_amount]

    # No list streamer is watchable: slot 2 may farm another explicit game,
    # never started-Drop completion. Its verification state stays independent.
    dual._observe_secondary(twitch, streamers, config)
    now = time.time()
    candidate = dual._sticky_secondary_candidate(
        twitch,
        streamers,
        config,
        primary_index,
        primary_game,
        now,
    )
    if candidate is None:
        candidate = dual._secondary_candidate(
            twitch,
            streamers,
            config,
            primary_index,
            primary_game,
            now,
        )
    if candidate is None:
        dual._clear_secondary(config)
        return result

    secondary_index = int(candidate.get("index", -1))
    if secondary_index < 0 or secondary_index == primary_index:
        dual._clear_secondary(config)
        return result

    dual._set_secondary(twitch, streamers, config, candidate)
    result.append(secondary_index)
    return result[:max_watch_amount]


def _install_selector() -> None:
    current_select = Twitch._select_streamers_to_watch
    if getattr(current_select, _PATCH_MARKER, False):
        return

    def select_with_drop_slot_state_machine(
        self: Twitch,
        streamers: list[Any],
        priority: list[Any],
        max_watch_amount: int = 2,
    ) -> list[int]:
        config = drop_games_patch._CONFIG.get(id(self))
        if config:
            stable = _stable_primary(self, streamers, config)
            if stable is not None:
                primary_index, primary_game = stable
                return _stable_slots(
                    self,
                    streamers,
                    priority,
                    max_watch_amount,
                    config,
                    primary_index,
                    primary_game,
                )

        # Startup or a genuine state transition: let the existing discovery
        # stack choose one new owner. Once it chooses a synthetic game Drop,
        # capture it and stop re-running competing selectors until the lease
        # genuinely ends by progress timeout, breaker, offline, or game change.
        selected = list(current_select(self, streamers, priority, max_watch_amount))
        config = drop_games_patch._CONFIG.get(id(self))
        if config:
            _capture_primary_lease(self, config, streamers)
        return selected[:max_watch_amount]

    setattr(select_with_drop_slot_state_machine, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_drop_slot_state_machine


def apply_patch() -> None:
    """Install one owner-driven state machine for Drop watch slots."""
    if getattr(Twitch, _PATCH_MARKER, False):
        return

    # Slot 2 is for another explicit configured game only. Completion remains
    # a fallback for the primary Drop path after game-Drop discovery yields.
    current_secondary = dual._secondary_candidate
    if not getattr(current_secondary, _SECONDARY_MARKER, False):
        global _ORIGINAL_SECONDARY_CANDIDATE
        _ORIGINAL_SECONDARY_CANDIDATE = current_secondary
        setattr(_secondary_candidate_without_completion, _SECONDARY_MARKER, True)
        dual._secondary_candidate = _secondary_candidate_without_completion

    current_sticky = dual._sticky_secondary_candidate
    if not getattr(current_sticky, _SECONDARY_MARKER, False):
        global _ORIGINAL_STICKY_SECONDARY
        _ORIGINAL_STICKY_SECONDARY = current_sticky
        setattr(_sticky_secondary_without_completion, _SECONDARY_MARKER, True)
        dual._sticky_secondary_candidate = _sticky_secondary_without_completion

    _install_selector()
    setattr(Twitch, _PATCH_MARKER, True)
