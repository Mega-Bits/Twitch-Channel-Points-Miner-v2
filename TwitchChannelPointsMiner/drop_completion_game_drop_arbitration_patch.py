"""Own Drop watch slots in one controller instead of stacked selector wrappers."""

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
from TwitchChannelPointsMiner import finish_started_drops_patch
from TwitchChannelPointsMiner import game_drop_progress_verification_patch as verification
from TwitchChannelPointsMiner.classes.Settings import Priority
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_central_drop_slot_controller_patch"
_MACHINE_KEY = "drop_slot_controller_v2"
_PRIMARY = "primary"
_SECONDARY = "secondary"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}


def _normalize(value: Any) -> str:
    return drop_games_patch._normalize(value)


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _safe_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _machine(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get(_MACHINE_KEY)
    if not isinstance(value, dict):
        value = {"slots": {}}
        config[_MACHINE_KEY] = value
    value.setdefault("slots", {})
    return value


def _slot(config: dict[str, Any], name: str) -> dict[str, Any] | None:
    value = _machine(config)["slots"].get(name)
    return value if isinstance(value, dict) else None


def _set_slot(config: dict[str, Any], name: str, state: dict[str, Any]) -> None:
    _machine(config)["slots"][name] = state


def _clear_slot(config: dict[str, Any], name: str) -> None:
    _machine(config)["slots"].pop(name, None)
    if name == _PRIMARY:
        config.pop("game_drop_progress_state", None)
    else:
        config.pop(dual._SECONDARY_PROGRESS_KEY, None)
        config.pop(dual._SECONDARY_SELECTION_KEY, None)


def _streamer_index(streamers: list[Any], username: str) -> int | None:
    return next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )


def _campaign_game(campaign: Any) -> tuple[str, str]:
    game = getattr(campaign, "game", {}) or {}
    if isinstance(game, dict):
        label = str(game.get("displayName") or game.get("name") or game.get("id") or "")
    else:
        label = str(game or "")
    return _normalize(label), label


def _explicit_games(twitch: Any) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in finish_started_drops_patch.get_explicit_drop_games(twitch):
        label = str(raw or "").strip()
        key = _normalize(label)
        if not key or key in seen or label == finish_started_drops_patch._SENTINEL_GAME:
            continue
        seen.add(key)
        result.append((key, label))
    return result


def _breaker_active(config: dict[str, Any], game_key: str, now: float) -> bool:
    breakers = runtime._cleanup_breakers(config, now)
    return runtime._safe_number(breakers.get(game_key)) > now


def _campaign_ids_for_game(
    twitch: Any,
    config: dict[str, Any],
    game_key: str,
    *,
    explicit: bool,
) -> list[Any]:
    explicit_ids, completion_ids = priority_order._explicit_first_campaigns(twitch, config)
    source = explicit_ids if explicit else completion_ids
    campaigns = config.get("campaigns_by_id", {}) or {}
    result: list[Any] = []
    for campaign_id in priority_order._prefer_existing_target(twitch, config, source):
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            continue
        key, _ = _campaign_game(campaign)
        if game_key and key != game_key:
            continue
        result.append(campaign_id)
    return result


def _real_game_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    game_key: str,
    game_label: str,
    excluded: set[int],
) -> dict[str, Any] | None:
    campaigns = config.get("campaigns_by_id", {}) or {}
    for campaign_id in _campaign_ids_for_game(
        twitch,
        config,
        game_key,
        explicit=True,
    ):
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            continue
        indexes = [
            index
            for index, streamer in enumerate(streamers)
            if index not in excluded and configured._eligible_for_campaign(streamer, campaign)
        ]
        if not indexes:
            continue
        indexes.sort(key=lambda index: (_source(streamers[index]) in _FALLBACK_SOURCES, index))
        runtime._clear_game_runtime(config, game_key)
        return {
            "kind": "game_drop",
            "campaign_id": campaign_id,
            "game_key": game_key,
            "game": game_label or _campaign_game(campaign)[1] or game_key,
            "username": streamers[indexes[0]].username,
            "index": indexes[0],
            "synthetic": False,
        }
    return None


def _catalogless_rejected(
    config: dict[str, Any],
    game_key: str,
    username: str,
    now: float,
) -> bool:
    rejections = verification._cleanup_rejections(config, now)
    return _safe_number(rejections.get((f"game:{game_key}", username))) > now


def _catalogless_candidate(
    streamers: list[Any],
    config: dict[str, Any],
    game_key: str,
    game_label: str,
    excluded: set[int],
    now: float,
) -> dict[str, Any] | None:
    if _breaker_active(config, game_key, now):
        return None

    mapping = config.get("catalogless_streamer_games", {}) or {}
    indexes: list[int] = []
    for index, streamer in enumerate(streamers):
        if index in excluded:
            continue
        mapped = mapping.get(getattr(streamer, "username", ""))
        if mapped is None or _normalize(mapped) != game_key:
            continue
        if not configured._watchable(streamer):
            continue
        if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
            continue
        games = catalogless._stream_game_values(streamer)
        if games and game_key not in games:
            continue
        if _catalogless_rejected(config, game_key, streamer.username, now):
            continue
        indexes.append(index)

    if not indexes:
        return None
    indexes.sort(key=lambda index: (_source(streamers[index]) in _FALLBACK_SOURCES, index))
    index = indexes[0]
    return {
        "kind": "game_drop",
        "campaign_id": f"{catalogless._CATALOGLESS_PREFIX}{game_key}",
        "game_key": game_key,
        "game": game_label or str(mapping.get(streamers[index].username) or game_key),
        "username": streamers[index].username,
        "index": index,
        "synthetic": True,
    }


def _explicit_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    excluded_games: set[str],
    excluded_indexes: set[int],
) -> dict[str, Any] | None:
    now = time.time()
    for game_key, game_label in _explicit_games(twitch):
        if game_key in excluded_games:
            continue
        real = _real_game_candidate(
            twitch,
            streamers,
            config,
            game_key,
            game_label,
            excluded_indexes,
        )
        if real is not None:
            return real
        synthetic = _catalogless_candidate(
            streamers,
            config,
            game_key,
            game_label,
            excluded_indexes,
            now,
        )
        if synthetic is not None:
            return synthetic
    return None


def _completion_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    excluded_indexes: set[int],
) -> dict[str, Any] | None:
    campaigns = config.get("campaigns_by_id", {}) or {}
    _, completion_ids = priority_order._explicit_first_campaigns(twitch, config)
    for campaign_id in priority_order._prefer_existing_target(twitch, config, completion_ids):
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            continue
        indexes = [
            index
            for index, streamer in enumerate(streamers)
            if index not in excluded_indexes and configured._eligible_for_campaign(streamer, campaign)
        ]
        if not indexes:
            continue
        indexes.sort(key=lambda index: (_source(streamers[index]) in _FALLBACK_SOURCES, index))
        game_key, game_label = _campaign_game(campaign)
        index = indexes[0]
        return {
            "kind": "drop_completion",
            "campaign_id": campaign_id,
            "game_key": game_key,
            "game": game_label or game_key,
            "username": streamers[index].username,
            "index": index,
            "synthetic": False,
        }
    return None


def _progress_context(
    streamers: list[Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    index = _streamer_index(streamers, str(state.get("username") or ""))
    if index is None:
        return None
    streamer = streamers[index]
    return {
        "campaign_id": state.get("campaign_id"),
        "campaign": None,
        "game": str(state.get("game") or state.get("game_key") or ""),
        "game_key": str(state.get("game_key") or ""),
        "specific_key": f"game:{state.get('game_key')}",
        "wildcard_key": f"game:{state.get('game_key')}",
        "index": index,
        "kind": "game_drop",
        "streamer": streamer,
        "username": streamer.username,
        "label": f"{state.get('game') or state.get('game_key')} / game directory",
    }


def _new_state(
    twitch: Any,
    streamers: list[Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    state = dict(candidate)
    if not candidate.get("synthetic"):
        return state

    context = _progress_context(streamers, state)
    timeout = verification._settings(twitch)["timeout"]
    now_epoch = time.time()
    now_monotonic = time.monotonic()
    metric = verification._progress_metric(context) if context is not None else None
    state.update(
        {
            "started_at": now_monotonic,
            "started_epoch": int(now_epoch),
            "deadline_epoch": int(now_epoch + timeout),
            "baseline": metric,
            "last_metric": metric,
            "verified": False,
        }
    )
    if timeout > 0:
        logger.info(
            "Verifying Drop progress for %s on %s / game directory; rotating after %s seconds without progress",
            state["username"],
            state["game"],
            timeout,
        )
    return state


def _mirror_primary(config: dict[str, Any], state: dict[str, Any] | None) -> None:
    if state is None:
        priority_order._clear_selection(config)
        config.pop("active_catalogless_game", None)
        config.pop("game_drop_progress_state", None)
        return

    config["active_selection_campaign_id"] = state.get("campaign_id")
    config["active_selection_streamer"] = state.get("username")
    config["active_selection_kind"] = state.get("kind")
    config["target_campaign_id"] = state.get("campaign_id")
    selection = (state.get("campaign_id"), state.get("username"), state.get("kind"))
    if config.get("priority_order_selection") != selection:
        label = "configured game Drop" if state.get("kind") == "game_drop" else "started Drop completion"
        logger.info(
            "Selecting %s campaign %s via %s",
            label,
            state.get("campaign_id"),
            state.get("username"),
        )
        config["priority_order_selection"] = selection

    if state.get("synthetic"):
        config["active_catalogless_game"] = state.get("game")
        config["game_drop_progress_state"] = state
    else:
        config.pop("active_catalogless_game", None)
        config.pop("game_drop_progress_state", None)


def _mirror_secondary(config: dict[str, Any], state: dict[str, Any] | None) -> None:
    if state is None:
        config.pop(dual._SECONDARY_SELECTION_KEY, None)
        config.pop(dual._SECONDARY_PROGRESS_KEY, None)
        return
    selection = {
        "campaign_id": state.get("campaign_id"),
        "streamer": state.get("username"),
        "kind": state.get("kind"),
        "game_key": state.get("game_key"),
        "game": state.get("game"),
        "synthetic": bool(state.get("synthetic")),
        "index": state.get("index"),
    }
    config[dual._SECONDARY_SELECTION_KEY] = selection
    if state.get("synthetic"):
        config[dual._SECONDARY_PROGRESS_KEY] = state
    else:
        config.pop(dual._SECONDARY_PROGRESS_KEY, None)


def _validate_synthetic_owner(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    state: dict[str, Any],
) -> tuple[bool, int | None]:
    game_key = str(state.get("game_key") or "")
    username = str(state.get("username") or "")
    if not game_key or not username or _breaker_active(config, game_key, time.time()):
        return False, None
    index = _streamer_index(streamers, username)
    if index is None:
        return False, None
    streamer = streamers[index]

    if _catalogless_rejected(config, game_key, username, time.time()):
        return False, index
    if not configured._watchable(streamer):
        if not runtime._refresh_stream_directly(twitch, streamer):
            return False, index
    if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
        return False, index
    games = catalogless._stream_game_values(streamer)
    if games and game_key not in games:
        return False, index
    state["index"] = index
    return True, index


def _upgrade_or_release_for_real_campaign(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    state: dict[str, Any],
) -> tuple[dict[str, Any] | None, int | None]:
    if not state.get("synthetic"):
        index = _streamer_index(streamers, str(state.get("username") or ""))
        return state, index

    game_key = str(state.get("game_key") or "")
    campaigns = config.get("campaigns_by_id", {}) or {}
    campaign_ids = _campaign_ids_for_game(twitch, config, game_key, explicit=True)
    if not campaign_ids:
        valid, index = _validate_synthetic_owner(twitch, streamers, config, state)
        return (state if valid else None), index

    runtime._clear_game_runtime(config, game_key)
    current_index = _streamer_index(streamers, str(state.get("username") or ""))
    if current_index is not None:
        for campaign_id in campaign_ids:
            campaign = campaigns.get(campaign_id)
            if campaign is not None and configured._eligible_for_campaign(streamers[current_index], campaign):
                state.update(
                    {
                        "campaign_id": campaign_id,
                        "synthetic": False,
                        "verified": True,
                        "index": current_index,
                    }
                )
                return state, current_index
    return None, current_index


def _observe_slot(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    name: str,
) -> tuple[dict[str, Any] | None, int | None]:
    state = _slot(config, name)
    if state is None:
        return None, None

    if state.get("kind") != "game_drop" or not state.get("synthetic"):
        index = _streamer_index(streamers, str(state.get("username") or ""))
        if index is None:
            _clear_slot(config, name)
            return None, None
        campaign = (config.get("campaigns_by_id", {}) or {}).get(state.get("campaign_id"))
        if campaign is None or not configured._eligible_for_campaign(streamers[index], campaign):
            _clear_slot(config, name)
            return None, None
        state["index"] = index
        return state, index

    state, index = _upgrade_or_release_for_real_campaign(
        twitch,
        streamers,
        config,
        state,
    )
    if state is None:
        _clear_slot(config, name)
        return None, None
    if not state.get("synthetic"):
        _set_slot(config, name, state)
        return state, index

    context = _progress_context(streamers, state)
    if context is None or index is None:
        _clear_slot(config, name)
        return None, None

    timeout = verification._settings(twitch)["timeout"]
    if timeout > 0 and not state.get("verified"):
        metric = verification._progress_metric(context)
        previous = state.get("last_metric")
        if metric is not None and previous is None:
            state["baseline"] = metric
            state["last_metric"] = metric
        elif metric is not None and previous is not None and metric > previous:
            state["last_metric"] = metric
            state["verified"] = True
            state["verified_epoch"] = int(time.time())
            logger.info(
                "Verified Drops-enabled channel %s for %s / game directory after Twitch reported progress",
                state["username"],
                state["game"],
            )
        elif time.monotonic() - _safe_number(state.get("started_at")) >= timeout:
            reject_state = {
                "username": state.get("username"),
                "campaign_id": state.get("campaign_id"),
                "game": state.get("game"),
                "game_key": state.get("game_key"),
                "specific_key": f"game:{state.get('game_key')}",
                "wildcard_key": f"game:{state.get('game_key')}",
                "label": f"{state.get('game')} / game directory",
            }
            if name == _SECONDARY:
                dual._reject_secondary(twitch, config, reject_state)
            else:
                verification._reject_state(twitch, config, reject_state)
            _clear_slot(config, name)
            return None, None

    _set_slot(config, name, state)
    return state, index


def _acquire_primary(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any] | None, int | None]:
    candidate = _explicit_candidate(twitch, streamers, config, set(), set())
    if candidate is None:
        candidate = _completion_candidate(twitch, streamers, config, set())
    if candidate is None:
        return None, None
    state = _new_state(twitch, streamers, candidate)
    _set_slot(config, _PRIMARY, state)
    return state, int(candidate["index"])


def _normal_candidates(streamers: list[Any], excluded: set[int]) -> list[int]:
    return [
        index
        for index, streamer in enumerate(streamers)
        if index not in excluded
        and _source(streamer) not in _FALLBACK_SOURCES
        and configured._watchable(streamer)
    ]


def _normal_order(
    streamers: list[Any],
    priority: list[Any],
    excluded: set[int],
    *,
    safe_with_drop: bool,
) -> list[int]:
    remaining = _normal_candidates(streamers, excluded)
    ordered: list[int] = []

    def add(items: list[int]) -> None:
        for index in items:
            if index in remaining and index not in ordered:
                ordered.append(index)

    add([index for index in remaining if configured._watch_streak_needed(streamers[index])])

    if safe_with_drop:
        add(
            [
                index
                for index in remaining
                if not configured._has_any_drop_eligibility(streamers[index])
            ]
        )
        return ordered

    for configured_priority in priority:
        if configured_priority == Priority.STREAK:
            add([index for index in remaining if configured._watch_streak_needed(streamers[index])])
        elif configured_priority == Priority.DROPS:
            add([index for index in remaining if configured._has_any_drop_eligibility(streamers[index])])
        elif configured_priority == Priority.ORDER:
            add(list(remaining))
        elif configured_priority == Priority.POINTS_ASCENDING:
            add(sorted(remaining, key=lambda index: int(getattr(streamers[index], "channel_points", 0) or 0)))
        elif configured_priority == Priority.POINTS_DESCENDING:
            add(sorted(remaining, key=lambda index: int(getattr(streamers[index], "channel_points", 0) or 0), reverse=True))
        elif configured_priority == Priority.SUBSCRIBED:
            add(
                sorted(
                    [index for index in remaining if streamers[index].viewer_has_points_multiplier()],
                    key=lambda index: streamers[index].total_points_multiplier(),
                    reverse=True,
                )
            )
    add(list(remaining))
    return ordered


def _acquire_secondary_game(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    primary: dict[str, Any],
    primary_index: int,
) -> tuple[dict[str, Any] | None, int | None]:
    if len(_explicit_games(twitch)) < 2:
        return None, None
    candidate = _explicit_candidate(
        twitch,
        streamers,
        config,
        {str(primary.get("game_key") or "")},
        {primary_index},
    )
    if candidate is None:
        return None, None
    state = _new_state(twitch, streamers, candidate)
    _set_slot(config, _SECONDARY, state)
    logger.info(
        "Using idle second watch slot for %s configured game Drop via %s because no configured-list streamer is watchable",
        state.get("game"),
        state.get("username"),
    )
    return state, int(candidate["index"])


def _select_streamers(
    twitch: Any,
    streamers: list[Any],
    priority: list[Any],
    max_watch_amount: int,
    config: dict[str, Any],
) -> list[int]:
    primary, primary_index = _observe_slot(twitch, streamers, config, _PRIMARY)

    if primary is not None and primary.get("kind") == "drop_completion":
        explicit = _explicit_candidate(twitch, streamers, config, set(), set())
        if explicit is not None:
            _clear_slot(config, _PRIMARY)
            primary = _new_state(twitch, streamers, explicit)
            _set_slot(config, _PRIMARY, primary)
            primary_index = int(explicit["index"])

    if primary is None:
        secondary, secondary_index = _observe_slot(twitch, streamers, config, _SECONDARY)
        if secondary is not None and secondary_index is not None:
            _clear_slot(config, _SECONDARY)
            primary = secondary
            primary_index = secondary_index
            _set_slot(config, _PRIMARY, primary)
        else:
            primary, primary_index = _acquire_primary(twitch, streamers, config)

    if primary is None or primary_index is None:
        _mirror_primary(config, None)
        _clear_slot(config, _SECONDARY)
        _mirror_secondary(config, None)
        return _normal_order(
            streamers,
            priority,
            set(),
            safe_with_drop=False,
        )[:max_watch_amount]

    _mirror_primary(config, primary)
    result = [primary_index]
    if max_watch_amount < 2:
        _clear_slot(config, _SECONDARY)
        _mirror_secondary(config, None)
        return result

    watchable_list = _normal_candidates(streamers, {primary_index})
    if watchable_list:
        normal = _normal_order(
            streamers,
            priority,
            {primary_index},
            safe_with_drop=True,
        )
        normal_index = normal[0] if normal else watchable_list[0]
        _clear_slot(config, _SECONDARY)
        _mirror_secondary(config, None)
        return (result + [normal_index])[:max_watch_amount]

    secondary, secondary_index = _observe_slot(twitch, streamers, config, _SECONDARY)
    if secondary is not None and str(secondary.get("game_key") or "") == str(primary.get("game_key") or ""):
        _clear_slot(config, _SECONDARY)
        secondary, secondary_index = None, None

    if secondary is None:
        secondary, secondary_index = _acquire_secondary_game(
            twitch,
            streamers,
            config,
            primary,
            primary_index,
        )

    if secondary is None or secondary_index is None or secondary_index == primary_index:
        _mirror_secondary(config, None)
        return result

    _mirror_secondary(config, secondary)
    return (result + [secondary_index])[:max_watch_amount]


def apply_patch() -> None:
    """Install one authoritative Drop-slot controller as the final selector."""
    current = getattr(Twitch, "_select_streamers_to_watch", None)
    if current is None or getattr(current, _PATCH_MARKER, False):
        return

    def select_with_central_drop_controller(
        self: Twitch,
        streamers: list[Any],
        priority: list[Any],
        max_watch_amount: int = 2,
    ) -> list[int]:
        config = drop_games_patch._CONFIG.get(id(self))
        if not config:
            return list(current(self, streamers, priority, max_watch_amount))[:max_watch_amount]
        return _select_streamers(
            self,
            streamers,
            priority,
            max_watch_amount,
            config,
        )

    setattr(select_with_central_drop_controller, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_central_drop_controller
