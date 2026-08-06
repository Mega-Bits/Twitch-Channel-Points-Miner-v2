"""Verify generated game-Drop channels by observed progress and rotate failures."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_game_drop_progress_verification_patch"
_DASHBOARD_MARKER = "_game_drop_progress_dashboard_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}
_DEFAULT_TIMEOUT_SECONDS = 240
_DEFAULT_COOLDOWN_SECONDS = 900
_RUNTIME_SETTINGS: dict[int, dict[str, int]] = {}


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _safe_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _game_values(value: Any) -> set[str]:
    game = value or {}
    if not isinstance(game, dict):
        normalized = drop_games_patch._normalize(game)
        return {normalized} if normalized else set()
    return {
        drop_games_patch._normalize(item)
        for item in (game.get("id"), game.get("name"), game.get("displayName"))
        if item not in (None, "")
    }


def _campaign_game(campaign: Any) -> str:
    game = getattr(campaign, "game", {}) or {}
    if isinstance(game, dict):
        return str(game.get("displayName") or game.get("name") or game.get("id") or "")
    return str(game or "")


def _campaign_label(campaign: Any, campaign_id: Any, game: str) -> str:
    name = str(getattr(campaign, "name", "") or "").strip()
    if name:
        return f"{game or 'unknown game'} / {name}"
    if str(campaign_id).startswith(catalogless._CATALOGLESS_PREFIX):
        return f"{game or 'unknown game'} / game directory"
    return f"{game or 'unknown game'} / {campaign_id}"


def _context(
    config: dict[str, Any],
    streamers: list[Any],
    campaign_id: Any,
    index: int,
    kind: str,
) -> dict[str, Any] | None:
    if kind != "game_drop" or not (0 <= index < len(streamers)):
        return None
    streamer = streamers[index]
    if _source(streamer) not in _FALLBACK_SOURCES:
        return None

    campaigns = config.get("campaigns_by_id", {}) or {}
    campaign = campaigns.get(campaign_id)
    synthetic = str(campaign_id).startswith(catalogless._CATALOGLESS_PREFIX)
    if campaign is not None:
        game = _campaign_game(campaign)
    else:
        game = str(
            config.get("active_catalogless_game")
            or (config.get("catalogless_streamer_games", {}) or {}).get(streamer.username)
            or ""
        ).strip()
    game_key = drop_games_patch._normalize(game)
    if not game_key:
        return None

    specific_key = (
        f"campaign:{campaign_id}"
        if not synthetic and campaign_id not in (None, "")
        else f"game:{game_key}"
    )
    return {
        "campaign_id": campaign_id,
        "campaign": campaign,
        "game": game,
        "game_key": game_key,
        "specific_key": specific_key,
        "wildcard_key": f"game:{game_key}",
        "index": index,
        "kind": kind,
        "streamer": streamer,
        "username": streamer.username,
        "label": _campaign_label(campaign, campaign_id, game),
    }


def _active_context(config: dict[str, Any], streamers: list[Any]) -> dict[str, Any] | None:
    username = config.get("active_selection_streamer")
    if not username:
        return None
    index = next(
        (
            position
            for position, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )
    if index is None:
        return None
    return _context(
        config,
        streamers,
        config.get("active_selection_campaign_id"),
        index,
        str(config.get("active_selection_kind") or ""),
    )


def _campaigns_for_context(context: dict[str, Any]) -> list[Any]:
    campaign = context.get("campaign")
    if campaign is not None:
        return [campaign]

    target_game = context["game_key"]
    result: list[Any] = []
    seen: set[Any] = set()
    stream = getattr(context["streamer"], "stream", None)
    for item in list(getattr(stream, "campaigns", []) or []):
        item_id = getattr(item, "id", id(item))
        if item_id in seen:
            continue
        if target_game not in _game_values(getattr(item, "game", {}) or {}):
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _campaign_progress_metric(campaign: Any) -> tuple[int, float, float] | None:
    drops = list(getattr(campaign, "drops", []) or [])
    if not drops:
        return None
    claimed = sum(1 for drop in drops if getattr(drop, "is_claimed", False))
    minutes = sum(_safe_number(getattr(drop, "current_minutes_watched", 0)) for drop in drops)
    percent = sum(_safe_number(getattr(drop, "percentage_progress", 0)) for drop in drops)
    return claimed, minutes, percent


def _progress_metric(context: dict[str, Any]) -> tuple[int, float, float] | None:
    metrics = [
        metric
        for metric in (
            _campaign_progress_metric(campaign)
            for campaign in _campaigns_for_context(context)
        )
        if metric is not None
    ]
    return max(metrics) if metrics else None


def _settings(twitch: Any) -> dict[str, int]:
    return _RUNTIME_SETTINGS.get(
        id(twitch),
        {
            "timeout": _DEFAULT_TIMEOUT_SECONDS,
            "cooldown": _DEFAULT_COOLDOWN_SECONDS,
        },
    )


def _cleanup_rejections(config: dict[str, Any], now: float) -> dict[tuple[str, str], float]:
    rejections = config.setdefault("game_drop_progress_rejections", {})
    for key, deadline in list(rejections.items()):
        if _safe_number(deadline) <= now:
            rejections.pop(key, None)
    return rejections


def _rejection_keys(context: dict[str, Any]) -> tuple[str, ...]:
    keys = [context["specific_key"]]
    if context["wildcard_key"] not in keys:
        keys.append(context["wildcard_key"])
    return tuple(keys)


def _is_rejected(config: dict[str, Any], context: dict[str, Any], now: float) -> bool:
    rejections = _cleanup_rejections(config, now)
    username = context["username"]
    return any(rejections.get((key, username), 0) > now for key in _rejection_keys(context))


def _start_state(
    twitch: Any,
    config: dict[str, Any],
    context: dict[str, Any],
    now_monotonic: float,
    now_epoch: float,
) -> dict[str, Any]:
    timeout = _settings(twitch)["timeout"]
    metric = _progress_metric(context)
    state = {
        "username": context["username"],
        "campaign_id": context["campaign_id"],
        "game": context["game"],
        "game_key": context["game_key"],
        "specific_key": context["specific_key"],
        "wildcard_key": context["wildcard_key"],
        "label": context["label"],
        "started_at": now_monotonic,
        "started_epoch": int(now_epoch),
        "deadline_epoch": int(now_epoch + timeout),
        "baseline": metric,
        "last_metric": metric,
        "verified": False,
    }
    config["game_drop_progress_state"] = state
    if timeout > 0:
        logger.info(
            "Verifying Drop progress for %s on %s; rotating after %s seconds without progress",
            context["username"],
            context["label"],
            timeout,
        )
    return state


def _state_matches(state: dict[str, Any], context: dict[str, Any]) -> bool:
    if state.get("username") != context["username"]:
        return False
    if state.get("specific_key") == context["specific_key"]:
        return True
    # Preserve the timer during the catalogless -> real campaign handoff.
    return state.get("game_key") == context["game_key"]


def _ensure_state(twitch: Any, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    state = config.get("game_drop_progress_state")
    if isinstance(state, dict) and _state_matches(state, context):
        state.update(
            {
                "campaign_id": context["campaign_id"],
                "game": context["game"],
                "game_key": context["game_key"],
                "specific_key": context["specific_key"],
                "wildcard_key": context["wildcard_key"],
                "label": context["label"],
            }
        )
        return state
    return _start_state(twitch, config, context, time.monotonic(), time.time())


def _reject_state(twitch: Any, config: dict[str, Any], state: dict[str, Any]) -> None:
    now = time.time()
    cooldown = _settings(twitch)["cooldown"]
    rejections = _cleanup_rejections(config, now)
    key = str(state.get("specific_key") or state.get("wildcard_key") or "")
    username = str(state.get("username") or "")
    if key and username:
        rejections[(key, username)] = now + cooldown
    logger.info(
        "No Drop progress detected for %s on %s; excluding this channel for %s seconds and trying the next Drops-enabled channel",
        username or "unknown channel",
        state.get("label") or state.get("game") or "unknown Drop",
        cooldown,
    )
    config.pop("game_drop_progress_state", None)
    config.pop("sticky_game_drop_handoff", None)


def _observe_active(twitch: Any, streamers: list[Any], config: dict[str, Any]) -> None:
    context = _active_context(config, streamers)
    if context is None:
        config.pop("game_drop_progress_state", None)
        return

    timeout = _settings(twitch)["timeout"]
    if timeout <= 0:
        config.pop("game_drop_progress_state", None)
        return

    state = _ensure_state(twitch, config, context)
    if state.get("verified"):
        return

    metric = _progress_metric(context)
    previous = state.get("last_metric")
    if metric is not None and previous is None:
        # Campaign information appeared after selection. Capture it as the
        # baseline and require a later increase so old progress cannot verify a
        # new channel accidentally.
        state["baseline"] = metric
        state["last_metric"] = metric
    elif metric is not None and previous is not None and metric > previous:
        state["last_metric"] = metric
        state["verified"] = True
        state["verified_epoch"] = int(time.time())
        logger.info(
            "Verified Drops-enabled channel %s for %s after Twitch reported progress",
            context["username"],
            context["label"],
        )
        return

    if time.monotonic() - _safe_number(state.get("started_at")) >= timeout:
        _reject_state(twitch, config, state)


def _candidate_with_progress_rotation(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    _observe_active(twitch, streamers, config)
    current = _ORIGINAL_DROP_CANDIDATE
    hidden: dict[int, bool] = {}
    try:
        for _ in range(len(streamers) + 1):
            candidate = current(twitch, streamers, config)
            if candidate is None:
                config.pop("game_drop_progress_state", None)
                return None
            context = _context(config, streamers, *candidate)
            if context is None:
                config.pop("game_drop_progress_state", None)
                return candidate
            if not _is_rejected(config, context, time.time()):
                _ensure_state(twitch, config, context)
                return candidate

            index = context["index"]
            if index in hidden:
                return None
            hidden[index] = bool(streamers[index].is_online)
            streamers[index].is_online = False
        return None
    finally:
        for index, online in hidden.items():
            streamers[index].is_online = online


def _install_runtime_options() -> None:
    original_run = TwitchChannelPointsMiner.run
    if not getattr(original_run, _PATCH_MARKER, False):

        def run_with_progress_verification(
            self: TwitchChannelPointsMiner,
            *args: Any,
            drop_progress_timeout: int = _DEFAULT_TIMEOUT_SECONDS,
            drop_candidate_cooldown: int = _DEFAULT_COOLDOWN_SECONDS,
            **kwargs: Any,
        ):
            timeout = max(0, min(int(drop_progress_timeout), 1800))
            if 0 < timeout < 120:
                timeout = 120
            cooldown = max(60, min(int(drop_candidate_cooldown), 7200))
            _RUNTIME_SETTINGS[id(self.twitch)] = {
                "timeout": timeout,
                "cooldown": cooldown,
            }
            try:
                return original_run(self, *args, **kwargs)
            finally:
                _RUNTIME_SETTINGS.pop(id(self.twitch), None)

        setattr(run_with_progress_verification, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.run = run_with_progress_verification

    original_mine = TwitchChannelPointsMiner.mine
    if not getattr(original_mine, _PATCH_MARKER, False):

        def mine_with_progress_verification(
            self: TwitchChannelPointsMiner,
            *args: Any,
            drop_progress_timeout: int = _DEFAULT_TIMEOUT_SECONDS,
            drop_candidate_cooldown: int = _DEFAULT_COOLDOWN_SECONDS,
            **kwargs: Any,
        ):
            return self.run(
                *args,
                drop_progress_timeout=drop_progress_timeout,
                drop_candidate_cooldown=drop_candidate_cooldown,
                **kwargs,
            )

        setattr(mine_with_progress_verification, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.mine = mine_with_progress_verification


def _install_dashboard_status() -> None:
    current_slot_snapshot = dashboard._slot_snapshot
    if not getattr(current_slot_snapshot, _DASHBOARD_MARKER, False):

        def slot_snapshot_with_verification(twitch: Any, streamer: Any) -> dict[str, Any]:
            slot = current_slot_snapshot(twitch, streamer)
            config = drop_games_patch._CONFIG.get(id(twitch)) or {}
            state = config.get("game_drop_progress_state")
            if isinstance(state, dict) and state.get("username") == streamer.username:
                slot["drop_progress_verification"] = {
                    "verified": bool(state.get("verified")),
                    "deadline_epoch": state.get("deadline_epoch"),
                }
            return slot

        setattr(slot_snapshot_with_verification, _DASHBOARD_MARKER, True)
        dashboard._slot_snapshot = slot_snapshot_with_verification

    current_render = dashboard.DashboardState._render_overview
    if not getattr(current_render, _DASHBOARD_MARKER, False):

        def render_overview_with_verification(self: Any, snapshot: dict[str, Any]) -> str:
            rendered = current_render(self, snapshot)
            lines = rendered.splitlines()
            for position, slot in enumerate(snapshot.get("watch_slots", ()), start=1):
                verification = slot.get("drop_progress_verification")
                if not verification:
                    continue
                detail = (
                    "   Drop verification: `progress confirmed`"
                    if verification.get("verified")
                    else "   Drop verification: `waiting for progress` · rotates "
                    + dashboard._discord_timestamp(verification.get("deadline_epoch"))
                )
                username = str(slot.get("username") or "")
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
                    while insert_at < len(lines) and lines[insert_at].startswith("   "):
                        insert_at += 1
                    lines.insert(insert_at, detail)
            return "\n".join(lines)

        setattr(render_overview_with_verification, _DASHBOARD_MARKER, True)
        dashboard.DashboardState._render_overview = render_overview_with_verification


def apply_patch() -> None:
    """Install progress verification for generated Drops-enabled channels."""
    current = priority_order._drop_candidate
    if getattr(current, _PATCH_MARKER, False):
        return

    global _ORIGINAL_DROP_CANDIDATE
    _ORIGINAL_DROP_CANDIDATE = current
    priority_order._drop_candidate = _candidate_with_progress_rotation
    setattr(priority_order._drop_candidate, _PATCH_MARKER, True)
    _install_runtime_options()
    _install_dashboard_status()
