"""Stop catalogless game-Drop farming when inventory proves the game is done."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import inventory_campaign_recovery_patch as inventory_recovery
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_completed_game_drop_guard_patch"
_TERMINAL_STATUSES = {"CLAIMED", "COMPLETED", "EXPIRED"}
_EXPIRY_GRACE_SECONDS = 10 * 60
_UNKNOWN_END_GRACE_SECONDS = 5 * 60
_MIN_FINISH_MARGIN_SECONDS = 60


def _normalize(value: Any) -> str:
    return drop_games_patch._normalize(value)


def _game_values(value: Any) -> set[str]:
    game = value or {}
    if not isinstance(game, dict):
        normalized = _normalize(game)
        return {normalized} if normalized else set()
    return {
        _normalize(item)
        for item in (game.get("id"), game.get("name"), game.get("displayName"))
        if item not in (None, "")
    }


def _game_label(value: Any, fallback: str) -> str:
    game = value or {}
    if isinstance(game, dict):
        return str(game.get("displayName") or game.get("name") or fallback)
    return str(game or fallback)


def _parse_epoch(value: Any) -> float | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _safe_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _drop_state(
    drop: dict[str, Any],
    campaign_end: float | None,
    now: float,
) -> dict[str, Any]:
    progress = drop.get("self") if isinstance(drop.get("self"), dict) else {}
    claimed = bool(progress.get("isClaimed"))
    current = _safe_number(progress.get("currentMinutesWatched"))
    required = _safe_number(drop.get("requiredMinutesWatched"))
    instance_id = progress.get("dropInstanceID")
    drop_end = _parse_epoch(drop.get("endAt"))
    ends = [value for value in (drop_end, campaign_end) if value is not None]
    effective_end = min(ends) if ends else None
    remaining_minutes = max(0.0, required - current)
    active = effective_end is None or now < effective_end
    finishable = (
        active
        and not claimed
        and remaining_minutes > 0
        and (
            effective_end is None
            or now + remaining_minutes * 60 + _MIN_FINISH_MARGIN_SECONDS
            < effective_end
        )
    )
    claimable = not claimed and remaining_minutes <= 0 and instance_id is not None
    return {
        "claimed": claimed,
        "claimable": claimable,
        "finishable": finishable,
        "remaining_minutes": remaining_minutes,
    }


def _inventory_state_for_game(
    twitch: Any,
    game_name: str,
    now: float,
) -> dict[str, Any] | None:
    inventory = inventory_recovery._LAST_INVENTORY.get(id(twitch), {})
    campaigns = inventory_recovery._inventory_campaigns(inventory)
    target = _normalize(game_name)
    matching = [
        campaign
        for campaign in campaigns
        if target in _game_values(campaign.get("game"))
    ]
    if not matching:
        return None

    terminal_ids: set[str] = set()
    open_ids: set[str] = set()
    terminal_reasons: list[str] = []
    blocked_until = now + _UNKNOWN_END_GRACE_SECONDS

    for campaign in matching:
        campaign_id = str(campaign.get("id") or "")
        label = str(campaign.get("name") or campaign_id or game_name)
        status = str(campaign.get("status") or "").upper()
        campaign_end = _parse_epoch(campaign.get("endAt"))
        if campaign_end is not None:
            blocked_until = max(
                blocked_until,
                campaign_end + _EXPIRY_GRACE_SECONDS,
            )

        drops = [
            drop
            for drop in (campaign.get("timeBasedDrops") or [])
            if isinstance(drop, dict)
        ]
        states = [_drop_state(drop, campaign_end, now) for drop in drops]
        farmable = any(state["finishable"] for state in states)
        if farmable and status not in _TERMINAL_STATUSES:
            if campaign_id:
                open_ids.add(campaign_id)
            continue

        if campaign_id:
            terminal_ids.add(campaign_id)
        if status in _TERMINAL_STATUSES:
            terminal_reasons.append(f"{label}: {status.lower()}")
        elif states and all(state["claimed"] for state in states):
            terminal_reasons.append(f"{label}: all rewards claimed")
        elif any(state["claimable"] for state in states):
            terminal_reasons.append(
                f"{label}: reward ready to claim; no watching required"
            )
        elif states and not any(state["finishable"] for state in states):
            remaining = min(
                (
                    state["remaining_minutes"]
                    for state in states
                    if not state["claimed"]
                ),
                default=0,
            )
            terminal_reasons.append(
                f"{label}: no reward can finish before the campaign/drop deadline"
                + (f" ({remaining:.0f} watch minutes remain)" if remaining else "")
            )
        else:
            terminal_reasons.append(f"{label}: no open reward")

    return {
        "game": _game_label(matching[0].get("game"), game_name),
        "game_key": target,
        "open_ids": open_ids,
        "terminal_ids": terminal_ids,
        "blocked": bool(terminal_ids) and not open_ids and now < blocked_until,
        "blocked_until": blocked_until,
        "reason": "; ".join(dict.fromkeys(terminal_reasons)),
    }


def _stream_campaign_ids(streamer: Any) -> set[str]:
    stream = getattr(streamer, "stream", None)
    return {
        str(campaign_id)
        for campaign_id in (getattr(stream, "campaigns_ids", None) or [])
        if campaign_id not in (None, "")
    }


def _candidate_game(config: dict[str, Any], campaign_id: Any) -> str:
    text = str(campaign_id or "")
    if text.startswith(catalogless._CATALOGLESS_PREFIX):
        return text[len(catalogless._CATALOGLESS_PREFIX):]
    return _normalize(config.get("active_catalogless_game") or "")


def _mapped_to_game(
    config: dict[str, Any],
    streamer: Any,
    game_key: str,
) -> bool:
    mapping = config.get("catalogless_streamer_games", {}) or {}
    mapped = mapping.get(getattr(streamer, "username", ""))
    return mapped is not None and _normalize(mapped) == game_key


def _rejected(
    config: dict[str, Any],
    game_key: str,
    username: str,
    now: float,
) -> bool:
    rejections = config.get("game_drop_progress_rejections", {}) or {}
    for key, deadline in rejections.items():
        if not isinstance(key, tuple) or len(key) != 2 or key[1] != username:
            continue
        if key[0] == f"game:{game_key}" and _safe_number(deadline) > now:
            return True
    return False


def _active_catalogless_candidate(
    streamers: list[Any],
    config: dict[str, Any],
    game_key: str,
    now: float,
) -> tuple[str, int, str] | None:
    active_id = str(config.get("active_selection_campaign_id") or "")
    username = str(config.get("active_selection_streamer") or "")
    if not active_id.startswith(catalogless._CATALOGLESS_PREFIX) or not username:
        return None
    if _candidate_game(config, active_id) != game_key:
        return None
    index = next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )
    if index is None:
        return None
    streamer = streamers[index]
    if not _mapped_to_game(config, streamer, game_key):
        return None
    if not configured._watchable(streamer):
        return None
    if _rejected(config, game_key, username, now):
        return None
    return active_id, index, "game_drop"


def _has_new_campaign(streamer: Any, terminal_ids: set[str]) -> bool:
    return bool(_stream_campaign_ids(streamer).difference(terminal_ids))


def _hide_terminal_only_channels(
    streamers: list[Any],
    config: dict[str, Any],
    state: dict[str, Any],
) -> dict[int, bool]:
    hidden: dict[int, bool] = {}
    for index, streamer in enumerate(streamers):
        if not _mapped_to_game(config, streamer, state["game_key"]):
            continue
        if _has_new_campaign(streamer, state["terminal_ids"]):
            continue
        hidden[index] = bool(getattr(streamer, "is_online", False))
        streamer.is_online = False
    return hidden


def _restore_online(streamers: list[Any], hidden: dict[int, bool]) -> None:
    for index, online in hidden.items():
        streamers[index].is_online = online


def _clear_catalogless_state(config: dict[str, Any]) -> None:
    config.pop("active_catalogless_game", None)
    config.pop("game_drop_progress_state", None)
    config.pop("sticky_game_drop_handoff", None)


def _log_guard(config: dict[str, Any], state: dict[str, Any]) -> None:
    signature = (
        state["game_key"],
        tuple(sorted(state["terminal_ids"])),
        state["reason"],
        int(state["blocked_until"]),
    )
    if config.get("completed_game_drop_guard_diagnostic") == signature:
        return
    config["completed_game_drop_guard_diagnostic"] = signature
    deadline = datetime.fromtimestamp(
        state["blocked_until"],
        timezone.utc,
    ).isoformat()
    logger.info(
        "Skipping game-directory Drop fallback for %s because inventory is "
        "terminal: %s. Rechecking automatically; this guard expires at %s "
        "and is bypassed immediately when Twitch advertises a different "
        "campaign ID",
        state["game"],
        state["reason"] or "no open reward",
        deadline,
    )


def _candidate_with_completed_inventory_guard(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    candidate = _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)
    if candidate is None or candidate[2] != "game_drop":
        return candidate

    campaign_id, index, _ = candidate
    game_key = _candidate_game(config, campaign_id)
    if (
        not str(campaign_id).startswith(catalogless._CATALOGLESS_PREFIX)
        or not game_key
    ):
        return candidate

    now = time.time()
    game_name = str(config.get("active_catalogless_game") or game_key)
    state = _inventory_state_for_game(twitch, game_name, now)

    # Directory ordering alone must not switch a provisional channel. The
    # progress-verification layer decides when a channel has actually failed.
    if state is None or not state["blocked"]:
        active = _active_catalogless_candidate(
            streamers,
            config,
            game_key,
            now,
        )
        return active or candidate

    # A completed old campaign must not hide a genuinely new overlapping
    # campaign. Per-channel campaign metadata bypasses the guard immediately.
    if _has_new_campaign(streamers[index], state["terminal_ids"]):
        config.pop("completed_game_drop_guard_diagnostic", None)
        return candidate

    hidden = _hide_terminal_only_channels(streamers, config, state)
    try:
        alternative = _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)
    finally:
        _restore_online(streamers, hidden)

    if alternative is not None and alternative[2] == "game_drop":
        alt_id, alt_index, _ = alternative
        if (
            str(alt_id).startswith(catalogless._CATALOGLESS_PREFIX)
            and _has_new_campaign(streamers[alt_index], state["terminal_ids"])
        ):
            config.pop("completed_game_drop_guard_diagnostic", None)
            return alternative

    _clear_catalogless_state(config)
    config["completed_game_drop_guard"] = {
        "game": state["game"],
        "reason": state["reason"],
        "until": int(state["blocked_until"]),
    }
    _log_guard(config, state)
    return alternative


def apply_patch() -> None:
    """Install inventory-terminal protection before progress verification."""
    current = priority_order._drop_candidate
    if getattr(current, _PATCH_MARKER, False):
        return

    global _ORIGINAL_DROP_CANDIDATE
    _ORIGINAL_DROP_CANDIDATE = current
    setattr(_candidate_with_completed_inventory_guard, _PATCH_MARKER, True)
    priority_order._drop_candidate = _candidate_with_completed_inventory_guard
