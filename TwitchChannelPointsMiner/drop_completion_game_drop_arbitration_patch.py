"""Keep started-Drop completion from fighting active explicit game-Drop discovery."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import catalogless_game_drop_runtime_patch as runtime
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import dual_game_drop_idle_slot_patch as dual
from TwitchChannelPointsMiner import finish_started_drops_patch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_drop_completion_game_drop_arbitration_patch"
_SECONDARY_MARKER = "_drop_completion_secondary_isolation_patch"
_DIAGNOSTIC_KEY = "drop_completion_game_drop_hold_diagnostic"


def _normalize(value: Any) -> str:
    return drop_games_patch._normalize(value)


def _synthetic_game(campaign_id: Any) -> str:
    value = str(campaign_id or "")
    if not value.startswith(catalogless._CATALOGLESS_PREFIX):
        return ""
    return value[len(catalogless._CATALOGLESS_PREFIX):]


def _breaker_active(config: dict[str, Any], game_key: str, now: float) -> bool:
    breakers = runtime._cleanup_breakers(config, now)
    return runtime._safe_number(breakers.get(game_key)) > now


def _pending_catalogless_game(
    twitch: Any,
    config: dict[str, Any],
    now: float,
) -> tuple[str, str, int] | None:
    """Return an explicit game that is still being verified by directory discovery."""
    mapping = config.get("catalogless_streamer_games", {}) or {}
    explicit_games = finish_started_drops_patch.get_explicit_drop_games(twitch)

    for game_name in explicit_games:
        label = str(game_name or "").strip()
        game_key = _normalize(label)
        if not game_key or _breaker_active(config, game_key, now):
            continue

        count = sum(1 for mapped in mapping.values() if _normalize(mapped) == game_key)
        if count:
            return game_key, label or game_key, count

        # During the exact cycle in which progress verification rejects a
        # directory channel, active_selection still points to that explicit
        # game until the outer selector applies its next result. Treat that as
        # pending too so Drop completion cannot occupy the slot for one cycle
        # and immediately be displaced again by the next directory candidate.
        if (
            str(config.get("active_selection_kind") or "") == "game_drop"
            and _synthetic_game(config.get("active_selection_campaign_id")) == game_key
        ):
            return game_key, label or game_key, 0

    return None


def _drop_candidate_with_stable_fallback(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    candidate = _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)
    if candidate is None or len(candidate) < 3 or candidate[2] != "drop_completion":
        if candidate is not None and len(candidate) >= 3 and candidate[2] == "game_drop":
            config.pop(_DIAGNOSTIC_KEY, None)
        return candidate

    pending = _pending_catalogless_game(twitch, config, time.time())
    if pending is None:
        config.pop(_DIAGNOSTIC_KEY, None)
        return candidate

    game_key, game_label, count = pending
    signature = (game_key, count)
    if config.get(_DIAGNOSTIC_KEY) != signature:
        config[_DIAGNOSTIC_KEY] = signature
        logger.info(
            "Holding started Drop completion while explicit game Drop %s is still being verified%s; completion remains fallback after the game is paused or no Drops-enabled candidate remains",
            game_label,
            f" across {count} DROPS_ENABLED candidate(s)" if count else "",
        )

    # A temporary gap between generated game-Drop candidates must not cause a
    # START/STOP cycle into the started-Drop completer. The normal priority
    # path may use the watch slot while the next game candidate warms up.
    return None


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


def apply_patch() -> None:
    """Make started-Drop completion a true fallback instead of a competing slot."""
    current = priority_order._drop_candidate
    if not getattr(current, _PATCH_MARKER, False):
        global _ORIGINAL_DROP_CANDIDATE
        _ORIGINAL_DROP_CANDIDATE = current
        setattr(_drop_candidate_with_stable_fallback, _PATCH_MARKER, True)
        priority_order._drop_candidate = _drop_candidate_with_stable_fallback

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
