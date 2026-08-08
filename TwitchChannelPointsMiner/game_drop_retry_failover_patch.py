"""Make catalogless Drop failure counting atomic and keep idle slot selection usable."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import catalogless_game_drop_runtime_patch as runtime
from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import dual_game_drop_idle_slot_patch as dual
from TwitchChannelPointsMiner import game_drop_progress_verification_patch as verification

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_game_drop_retry_failover_patch"
_SECONDARY_MARKER = "_secondary_game_drop_retry_failover_patch"
_LIST_MARKER = "_idle_slot_watchable_list_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}


def _synthetic_game(campaign_id: Any) -> str:
    value = str(campaign_id or "")
    if not value.startswith(catalogless._CATALOGLESS_PREFIX):
        return ""
    return value[len(catalogless._CATALOGLESS_PREFIX):]


def _failure_history(config: dict[str, Any]) -> dict[str, set[str]]:
    """Use the same in-memory history consumed by the 24-hour runtime latch."""
    history = config.setdefault("catalogless_game_runtime_failure_history", {})
    return history


def _record_catalogless_failure(
    twitch: Any,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    secondary: bool,
) -> bool:
    """Record one completed verification attempt and open the breaker immediately."""
    campaign_id = state.get("campaign_id")
    game_key = str(state.get("game_key") or _synthetic_game(campaign_id) or "")
    if not game_key or not _synthetic_game(campaign_id):
        return False

    username = str(state.get("username") or "").strip()
    if not username:
        return False

    failed = _failure_history(config).setdefault(game_key, set())
    before = len(failed)
    failed.add(username)
    count = len(failed)
    limit = int(runtime._settings(twitch).get("failure_limit", 0) or 0)

    if count != before and limit > 0 and count < limit:
        logger.info(
            "Catalogless Drop verification failed for %s via %s (%s/%s different channels); continuing with another candidate",
            state.get("game") or game_key,
            username,
            count,
            limit,
        )

    if limit <= 0 or count < limit:
        return False

    now = time.time()
    breakers = runtime._cleanup_breakers(config, now)
    if runtime._safe_number(breakers.get(game_key)) <= now:
        runtime._open_breaker(
            twitch,
            config,
            game_key,
            str(state.get("game") or game_key),
            set(failed),
            now,
        )
        logger.info(
            "Catalogless Drop failure limit reached for %s after %s different channels; skipping this game now so another explicit drop_games game can take the slot",
            state.get("game") or game_key,
            count,
        )

    # A secondary selection for the same game must not survive the newly opened
    # breaker. This is process memory only and does not persist Drop state.
    secondary_selection = config.get(dual._SECONDARY_SELECTION_KEY)
    if (
        isinstance(secondary_selection, dict)
        and str(secondary_selection.get("game_key") or "") == game_key
    ):
        config.pop(dual._SECONDARY_SELECTION_KEY, None)
        config.pop(dual._SECONDARY_PROGRESS_KEY, None)

    return True


def _reject_primary_with_atomic_failure(
    twitch: Any,
    config: dict[str, Any],
    state: dict[str, Any],
) -> None:
    snapshot = dict(state)
    _ORIGINAL_PRIMARY_REJECT(twitch, config, state)
    _record_catalogless_failure(
        twitch,
        config,
        snapshot,
        secondary=False,
    )


def _reject_secondary_with_atomic_failure(
    twitch: Any,
    config: dict[str, Any],
    state: dict[str, Any],
) -> None:
    snapshot = dict(state)
    _ORIGINAL_SECONDARY_REJECT(twitch, config, state)
    _record_catalogless_failure(
        twitch,
        config,
        snapshot,
        secondary=True,
    )


def _has_watchable_list_streamer(streamers: list[Any]) -> bool:
    """Do not let stale/non-watchable list state waste an otherwise idle slot."""
    return any(
        str(getattr(streamer, "source", "list")).strip().lower()
        not in _FALLBACK_SOURCES
        and configured._watchable(streamer)
        for streamer in streamers
    )


def apply_patch() -> None:
    """Install atomic failure accounting and stricter idle-slot availability."""
    current_primary = verification._reject_state
    if not getattr(current_primary, _PATCH_MARKER, False):
        global _ORIGINAL_PRIMARY_REJECT
        _ORIGINAL_PRIMARY_REJECT = current_primary
        setattr(_reject_primary_with_atomic_failure, _PATCH_MARKER, True)
        verification._reject_state = _reject_primary_with_atomic_failure

    current_secondary = dual._reject_secondary
    if not getattr(current_secondary, _SECONDARY_MARKER, False):
        global _ORIGINAL_SECONDARY_REJECT
        _ORIGINAL_SECONDARY_REJECT = current_secondary
        setattr(_reject_secondary_with_atomic_failure, _SECONDARY_MARKER, True)
        dual._reject_secondary = _reject_secondary_with_atomic_failure

    current_list_check = dual._has_online_list_streamer
    if not getattr(current_list_check, _LIST_MARKER, False):
        setattr(_has_watchable_list_streamer, _LIST_MARKER, True)
        dual._has_online_list_streamer = _has_watchable_list_streamer
