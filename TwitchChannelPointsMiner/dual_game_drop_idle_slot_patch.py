"""Use an otherwise idle second watch slot for a Drop from another game."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import catalogless_game_drop_runtime_patch as runtime
from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import finish_started_drops_patch
from TwitchChannelPointsMiner import game_drop_progress_verification_patch as verification
from TwitchChannelPointsMiner import status_dashboard_drop_reason_patch as drop_reason
from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_dual_game_drop_idle_slot_patch"
_DASHBOARD_MARKER = "_dual_game_drop_idle_slot_dashboard_patch"
_REASON_MARKER = "_dual_game_drop_idle_slot_reason_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}
_SECONDARY_SELECTION_KEY = "secondary_game_drop_selection"
_SECONDARY_PROGRESS_KEY = "secondary_game_drop_progress_state"


def _normalize(value: Any) -> str:
    return drop_games_patch._normalize(value)


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _safe_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _campaign_game(campaign: Any) -> tuple[str, str]:
    game = getattr(campaign, "game", {}) or {}
    if isinstance(game, dict):
        label = str(game.get("displayName") or game.get("name") or game.get("id") or "")
    else:
        label = str(game or "")
    return _normalize(label), label


def _synthetic_game(campaign_id: Any) -> str:
    value = str(campaign_id or "")
    if not value.startswith(catalogless._CATALOGLESS_PREFIX):
        return ""
    return value[len(catalogless._CATALOGLESS_PREFIX):]


def _selection_game(
    config: dict[str, Any],
    campaign_id: Any,
    streamer: Any | None = None,
) -> tuple[str, str]:
    synthetic = _synthetic_game(campaign_id)
    if synthetic:
        mapping = config.get("catalogless_streamer_games", {}) or {}
        mapped = mapping.get(getattr(streamer, "username", "")) if streamer is not None else None
        label = str(mapped or synthetic)
        return synthetic, label

    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    if campaign is not None:
        return _campaign_game(campaign)

    if streamer is not None:
        mapping = config.get("catalogless_streamer_games", {}) or {}
        mapped = mapping.get(getattr(streamer, "username", ""))
        if mapped:
            return _normalize(mapped), str(mapped)
    return "", ""


def _has_online_list_streamer(streamers: list[Any]) -> bool:
    return any(
        _source(streamer) not in _FALLBACK_SOURCES
        and getattr(streamer, "is_online", False) is True
        for streamer in streamers
    )


def _rejection_keys(campaign_id: Any, game_key: str) -> tuple[str, ...]:
    keys = []
    if campaign_id not in (None, "") and not _synthetic_game(campaign_id):
        keys.append(f"campaign:{campaign_id}")
    keys.append(f"game:{game_key}")
    return tuple(dict.fromkeys(keys))


def _is_rejected(
    config: dict[str, Any],
    campaign_id: Any,
    game_key: str,
    username: str,
    now: float,
) -> bool:
    rejections = verification._cleanup_rejections(config, now)
    return any(
        _safe_number(rejections.get((key, username))) > now
        for key in _rejection_keys(campaign_id, game_key)
    )


def _catalogless_paused(config: dict[str, Any], game_key: str, now: float) -> bool:
    breakers = runtime._cleanup_breakers(config, now)
    return _safe_number(breakers.get(game_key)) > now


def _candidate_indexes_for_campaign(
    streamers: list[Any],
    campaign: Any,
    campaign_id: Any,
    game_key: str,
    excluded_index: int,
    config: dict[str, Any],
    now: float,
) -> list[int]:
    candidates = [
        index
        for index, streamer in enumerate(streamers)
        if index != excluded_index
        and configured._eligible_for_campaign(streamer, campaign)
        and not _is_rejected(
            config,
            campaign_id,
            game_key,
            str(getattr(streamer, "username", "")),
            now,
        )
    ]
    candidates.sort(
        key=lambda index: (
            _source(streamers[index]) in _FALLBACK_SOURCES,
            index,
        )
    )
    return candidates


def _real_candidate_pool(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    primary_index: int,
    primary_game: str,
    now: float,
    *,
    include_completion: bool,
) -> list[dict[str, Any]]:
    campaigns = config.get("campaigns_by_id", {}) or {}
    explicit_ids, completion_ids = priority_order._explicit_first_campaigns(twitch, config)
    groups = [("game_drop", explicit_ids)]
    if include_completion:
        groups.append(("drop_completion", completion_ids))

    result: list[dict[str, Any]] = []
    seen_games: set[str] = set()
    for kind, campaign_ids in groups:
        for campaign_id in priority_order._prefer_existing_target(
            twitch,
            config,
            campaign_ids,
        ):
            campaign = campaigns.get(campaign_id)
            if campaign is None:
                continue
            game_key, game_label = _campaign_game(campaign)
            if not game_key or game_key == primary_game or game_key in seen_games:
                continue
            indexes = _candidate_indexes_for_campaign(
                streamers,
                campaign,
                campaign_id,
                game_key,
                primary_index,
                config,
                now,
            )
            if not indexes:
                continue
            result.append(
                {
                    "campaign_id": campaign_id,
                    "index": indexes[0],
                    "kind": kind,
                    "game_key": game_key,
                    "game": game_label or game_key,
                    "synthetic": False,
                }
            )
            seen_games.add(game_key)
    return result


def _catalogless_candidate_pool(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    primary_index: int,
    primary_game: str,
    now: float,
) -> list[dict[str, Any]]:
    mapping = config.get("catalogless_streamer_games", {}) or {}
    if not mapping:
        return []

    result: list[dict[str, Any]] = []
    seen_games: set[str] = set()
    for raw_game in finish_started_drops_patch.get_explicit_drop_games(twitch):
        game_label = str(raw_game or "").strip()
        game_key = _normalize(game_label)
        if (
            not game_key
            or game_key == primary_game
            or game_key in seen_games
            or _catalogless_paused(config, game_key, now)
        ):
            continue

        candidates = []
        for index, streamer in enumerate(streamers):
            if index == primary_index:
                continue
            mapped = mapping.get(getattr(streamer, "username", ""))
            if mapped is None or _normalize(mapped) != game_key:
                continue
            if not configured._watchable(streamer):
                continue
            if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
                continue
            stream_games = catalogless._stream_game_values(streamer)
            if stream_games and game_key not in stream_games:
                continue
            campaign_id = f"{catalogless._CATALOGLESS_PREFIX}{game_key}"
            if _is_rejected(
                config,
                campaign_id,
                game_key,
                str(getattr(streamer, "username", "")),
                now,
            ):
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
        result.append(
            {
                "campaign_id": f"{catalogless._CATALOGLESS_PREFIX}{game_key}",
                "index": candidates[0],
                "kind": "game_drop",
                "game_key": game_key,
                "game": game_label or game_key,
                "synthetic": True,
            }
        )
        seen_games.add(game_key)
    return result


def _secondary_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    primary_index: int,
    primary_game: str,
    now: float,
) -> dict[str, Any] | None:
    # Prefer another explicit configured game. A known campaign is stronger
    # evidence than the directory-only fallback, then started completion is a
    # final use of an otherwise idle slot.
    real_explicit = _real_candidate_pool(
        twitch,
        streamers,
        config,
        primary_index,
        primary_game,
        now,
        include_completion=False,
    )
    if real_explicit:
        return real_explicit[0]

    catalogless_candidates = _catalogless_candidate_pool(
        twitch,
        streamers,
        config,
        primary_index,
        primary_game,
        now,
    )
    if catalogless_candidates:
        return catalogless_candidates[0]

    real_with_completion = _real_candidate_pool(
        twitch,
        streamers,
        config,
        primary_index,
        primary_game,
        now,
        include_completion=True,
    )
    completion_only = [candidate for candidate in real_with_completion if candidate["kind"] == "drop_completion"]
    return completion_only[0] if completion_only else None


def _secondary_metadata(config: dict[str, Any]) -> dict[str, Any] | None:
    value = config.get(_SECONDARY_SELECTION_KEY)
    return value if isinstance(value, dict) else None


def _secondary_for_streamer(
    twitch: Any,
    streamer: Any,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    config = drop_games_patch._CONFIG.get(id(twitch)) or {}
    selection = _secondary_metadata(config)
    if selection is None:
        return None
    if selection.get("streamer") != getattr(streamer, "username", None):
        return None
    return config, selection


def _upgrade_previous_catalogless(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    selection: dict[str, Any],
    index: int,
    primary_game: str,
    now: float,
) -> dict[str, Any] | None:
    game_key = str(selection.get("game_key") or "")
    if not game_key or game_key == primary_game:
        return None
    campaigns = config.get("campaigns_by_id", {}) or {}
    explicit_ids, _ = priority_order._explicit_first_campaigns(twitch, config)
    for campaign_id in priority_order._prefer_existing_target(twitch, config, explicit_ids):
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            continue
        campaign_game, game_label = _campaign_game(campaign)
        if campaign_game != game_key:
            continue
        streamer = streamers[index]
        if not configured._eligible_for_campaign(streamer, campaign):
            continue
        if _is_rejected(config, campaign_id, game_key, streamer.username, now):
            continue
        return {
            "campaign_id": campaign_id,
            "index": index,
            "kind": "game_drop",
            "game_key": game_key,
            "game": game_label or selection.get("game") or game_key,
            "synthetic": False,
        }
    return None


def _sticky_secondary_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    primary_index: int,
    primary_game: str,
    now: float,
) -> dict[str, Any] | None:
    selection = _secondary_metadata(config)
    if selection is None:
        return None
    username = str(selection.get("streamer") or "")
    game_key = str(selection.get("game_key") or "")
    campaign_id = selection.get("campaign_id")
    kind = str(selection.get("kind") or "")
    if not username or not game_key or game_key == primary_game:
        return None
    index = next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )
    if index is None or index == primary_index:
        return None
    streamer = streamers[index]

    if selection.get("synthetic"):
        upgraded = _upgrade_previous_catalogless(
            twitch,
            streamers,
            config,
            selection,
            index,
            primary_game,
            now,
        )
        if upgraded is not None:
            return upgraded
        if _catalogless_paused(config, game_key, now):
            return None
        if _is_rejected(config, campaign_id, game_key, username, now):
            return None
        if not configured._watchable(streamer):
            # Directory pagination can mark a still-live fallback channel
            # offline. Confirm the active secondary directly before replacing
            # it solely because the latest page omitted it.
            if not runtime._refresh_stream_directly(twitch, streamer):
                return None
        stream_games = catalogless._stream_game_values(streamer)
        if stream_games and game_key not in stream_games:
            return None
        if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
            return None
        config.setdefault("catalogless_streamer_games", {})[username] = str(
            selection.get("game") or game_key
        )
        if _source(streamer) == "game_drop":
            streamer.fallback_campaign_ids = frozenset({catalogless._CATALOGLESS_FALLBACK_ID})
        return {
            **selection,
            "index": index,
        }

    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    if campaign is None or kind not in {"game_drop", "drop_completion"}:
        return None
    if not configured._eligible_for_campaign(streamer, campaign):
        return None
    if _is_rejected(config, campaign_id, game_key, username, now):
        return None
    return {
        **selection,
        "index": index,
    }


def _progress_context(
    config: dict[str, Any],
    streamers: list[Any],
    selection: dict[str, Any],
) -> dict[str, Any] | None:
    if selection.get("kind") != "game_drop":
        return None
    index = int(selection.get("index", -1))
    if not (0 <= index < len(streamers)):
        username = selection.get("streamer")
        index = next(
            (
                position
                for position, streamer in enumerate(streamers)
                if getattr(streamer, "username", None) == username
            ),
            -1,
        )
    if not (0 <= index < len(streamers)):
        return None
    streamer = streamers[index]
    if _source(streamer) not in _FALLBACK_SOURCES:
        return None

    campaign_id = selection.get("campaign_id")
    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    game_key = str(selection.get("game_key") or "")
    if not game_key:
        return None
    specific_key = (
        f"campaign:{campaign_id}"
        if not selection.get("synthetic") and campaign_id not in (None, "")
        else f"game:{game_key}"
    )
    game_label = str(selection.get("game") or game_key)
    label = (
        f"{game_label} / {getattr(campaign, 'name', campaign_id)}"
        if campaign is not None
        else f"{game_label} / game directory"
    )
    return {
        "campaign_id": campaign_id,
        "campaign": campaign,
        "game": game_label,
        "game_key": game_key,
        "specific_key": specific_key,
        "wildcard_key": f"game:{game_key}",
        "index": index,
        "kind": "game_drop",
        "streamer": streamer,
        "username": streamer.username,
        "label": label,
        "synthetic": bool(selection.get("synthetic")),
    }


def _state_matches(state: dict[str, Any], context: dict[str, Any]) -> bool:
    return (
        state.get("username") == context["username"]
        and state.get("game_key") == context["game_key"]
    )


def _start_secondary_state(
    twitch: Any,
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    timeout = verification._settings(twitch)["timeout"]
    now_epoch = time.time()
    metric = verification._progress_metric(context)
    state = {
        "username": context["username"],
        "campaign_id": context["campaign_id"],
        "game": context["game"],
        "game_key": context["game_key"],
        "specific_key": context["specific_key"],
        "wildcard_key": context["wildcard_key"],
        "label": context["label"],
        "synthetic": context["synthetic"],
        "started_at": time.monotonic(),
        "started_epoch": int(now_epoch),
        "deadline_epoch": int(now_epoch + timeout),
        "baseline": metric,
        "last_metric": metric,
        "verified": False,
    }
    config[_SECONDARY_PROGRESS_KEY] = state
    if timeout > 0:
        logger.info(
            "Verifying secondary Drop progress for %s on %s; rotating after %s seconds without progress",
            context["username"],
            context["label"],
            timeout,
        )
    return state


def _ensure_secondary_state(
    twitch: Any,
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    state = config.get(_SECONDARY_PROGRESS_KEY)
    if isinstance(state, dict) and _state_matches(state, context):
        state.update(
            {
                "campaign_id": context["campaign_id"],
                "game": context["game"],
                "specific_key": context["specific_key"],
                "wildcard_key": context["wildcard_key"],
                "label": context["label"],
                "synthetic": context["synthetic"],
            }
        )
        return state
    return _start_secondary_state(twitch, config, context)


def _open_secondary_breaker(
    twitch: Any,
    config: dict[str, Any],
    game_key: str,
    game_label: str,
    failed: set[str],
    now: float,
) -> None:
    breakers = runtime._cleanup_breakers(config, now)
    if _safe_number(breakers.get(game_key)) > now:
        return
    cooldown = runtime._settings(twitch)["retry_cooldown"]
    deadline = now + cooldown if cooldown > 0 else float("inf")
    breakers[game_key] = deadline
    config.setdefault("catalogless_game_runtime_retry_logged", set()).discard(game_key)
    if cooldown > 0:
        logger.info(
            "Pausing catalogless Drop discovery for %s for %s seconds after %s different DROPS_ENABLED channels produced no Twitch-reported progress",
            game_label or game_key,
            cooldown,
            len(failed),
        )
    else:
        logger.info(
            "Disabling catalogless Drop discovery for %s for the rest of this miner session after %s different DROPS_ENABLED channels produced no Twitch-reported progress",
            game_label or game_key,
            len(failed),
        )


def _reject_secondary(
    twitch: Any,
    config: dict[str, Any],
    state: dict[str, Any],
) -> None:
    now = time.time()
    cooldown = verification._settings(twitch)["cooldown"]
    rejections = verification._cleanup_rejections(config, now)
    key = str(state.get("specific_key") or state.get("wildcard_key") or "")
    username = str(state.get("username") or "")
    if key and username:
        rejections[(key, username)] = now + cooldown
    logger.info(
        "No Drop progress detected for secondary slot %s on %s; excluding this channel for %s seconds and trying another game-Drop candidate",
        username or "unknown channel",
        state.get("label") or state.get("game") or "unknown Drop",
        cooldown,
    )

    if state.get("synthetic"):
        game_key = str(state.get("game_key") or "")
        if game_key:
            failed = runtime._failure_usernames(config, game_key, now)
            limit = runtime._settings(twitch)["failure_limit"]
            if limit > 0 and len(failed) >= limit:
                _open_secondary_breaker(
                    twitch,
                    config,
                    game_key,
                    str(state.get("game") or game_key),
                    failed,
                    now,
                )

    config.pop(_SECONDARY_PROGRESS_KEY, None)
    config.pop(_SECONDARY_SELECTION_KEY, None)


def _observe_secondary(twitch: Any, streamers: list[Any], config: dict[str, Any]) -> None:
    selection = _secondary_metadata(config)
    if selection is None:
        config.pop(_SECONDARY_PROGRESS_KEY, None)
        return
    context = _progress_context(config, streamers, selection)
    if context is None:
        config.pop(_SECONDARY_PROGRESS_KEY, None)
        return
    timeout = verification._settings(twitch)["timeout"]
    if timeout <= 0:
        config.pop(_SECONDARY_PROGRESS_KEY, None)
        return
    state = _ensure_secondary_state(twitch, config, context)
    if state.get("verified"):
        return

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
            "Verified secondary Drops-enabled channel %s for %s after Twitch reported progress",
            context["username"],
            context["label"],
        )
        return

    if time.monotonic() - _safe_number(state.get("started_at")) >= timeout:
        _reject_secondary(twitch, config, state)


def _clear_secondary(config: dict[str, Any]) -> None:
    config.pop(_SECONDARY_SELECTION_KEY, None)
    config.pop(_SECONDARY_PROGRESS_KEY, None)


def _set_secondary(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    streamer = streamers[candidate["index"]]
    selection = {
        "campaign_id": candidate["campaign_id"],
        "streamer": streamer.username,
        "kind": candidate["kind"],
        "game_key": candidate["game_key"],
        "game": candidate["game"],
        "synthetic": bool(candidate.get("synthetic")),
        "index": candidate["index"],
    }
    previous = _secondary_metadata(config)
    if previous != selection:
        label = "configured game Drop" if candidate["kind"] == "game_drop" else "started Drop completion"
        logger.info(
            "Using idle second watch slot for %s %s via %s because no configured-list streamer is online",
            candidate["game"],
            label,
            streamer.username,
        )
        config[_SECONDARY_SELECTION_KEY] = selection
    else:
        config[_SECONDARY_SELECTION_KEY] = selection

    context = _progress_context(config, streamers, selection)
    if context is not None and verification._settings(twitch)["timeout"] > 0:
        _ensure_secondary_state(twitch, config, context)
    elif candidate["kind"] != "game_drop" or _source(streamer) not in _FALLBACK_SOURCES:
        config.pop(_SECONDARY_PROGRESS_KEY, None)


def _primary_game(
    config: dict[str, Any],
    streamers: list[Any],
    selected: list[int],
) -> tuple[int, str] | None:
    if str(config.get("active_selection_kind") or "") != "game_drop":
        return None
    username = str(config.get("active_selection_streamer") or "")
    index = next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )
    if index is None:
        index = selected[0] if selected else None
    if index is None or not (0 <= index < len(streamers)):
        return None
    game_key, _ = _selection_game(
        config,
        config.get("active_selection_campaign_id"),
        streamers[index],
    )
    if not game_key:
        return None
    return index, game_key


def _install_selector() -> None:
    current_select = Twitch._select_streamers_to_watch
    if getattr(current_select, _PATCH_MARKER, False):
        return

    def select_with_second_game_drop_when_idle(
        self: Twitch,
        streamers: list[Any],
        priority: list[Any],
        max_watch_amount: int = 2,
    ) -> list[int]:
        config_before = drop_games_patch._CONFIG.get(id(self))
        if config_before:
            _observe_secondary(self, streamers, config_before)

        selected = list(current_select(self, streamers, priority, max_watch_amount))
        config = drop_games_patch._CONFIG.get(id(self))
        if not config:
            return selected[:max_watch_amount]
        if max_watch_amount < 2 or len(selected) >= 2:
            _clear_secondary(config)
            return selected[:max_watch_amount]
        if _has_online_list_streamer(streamers):
            _clear_secondary(config)
            return selected[:max_watch_amount]

        primary = _primary_game(config, streamers, selected)
        if primary is None:
            _clear_secondary(config)
            return selected[:max_watch_amount]
        primary_index, primary_game = primary
        now = time.time()

        candidate = _sticky_secondary_candidate(
            self,
            streamers,
            config,
            primary_index,
            primary_game,
            now,
        )
        if candidate is None:
            candidate = _secondary_candidate(
                self,
                streamers,
                config,
                primary_index,
                primary_game,
                now,
            )
        if candidate is None:
            _clear_secondary(config)
            return selected[:max_watch_amount]

        secondary_index = int(candidate["index"])
        if secondary_index in selected:
            _clear_secondary(config)
            return selected[:max_watch_amount]

        _set_secondary(self, streamers, config, candidate)
        return (selected + [secondary_index])[:max_watch_amount]

    setattr(select_with_second_game_drop_when_idle, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_second_game_drop_when_idle


def _install_reason_support() -> None:
    current_drop_selection = drop_reason._selection
    if not getattr(current_drop_selection, _REASON_MARKER, False):

        def selection_with_secondary(twitch: Any, streamer: Any):
            selected = current_drop_selection(twitch, streamer)
            if selected is not None:
                return selected
            secondary = _secondary_for_streamer(twitch, streamer)
            if secondary is None:
                return None
            config, selection = secondary
            if selection.get("synthetic"):
                return None
            campaign = (config.get("campaigns_by_id", {}) or {}).get(selection.get("campaign_id"))
            kind = str(selection.get("kind") or "")
            if campaign is None or kind not in {"game_drop", "drop_completion"}:
                return None
            return config, campaign, kind

        setattr(selection_with_secondary, _REASON_MARKER, True)
        drop_reason._selection = selection_with_secondary

    current_catalogless_selection = catalogless._catalogless_selection
    if not getattr(current_catalogless_selection, _REASON_MARKER, False):

        def catalogless_selection_with_secondary(twitch: Any, streamer: Any):
            selected = current_catalogless_selection(twitch, streamer)
            if selected is not None:
                return selected
            secondary = _secondary_for_streamer(twitch, streamer)
            if secondary is None:
                return None
            config, selection = secondary
            if not selection.get("synthetic"):
                return None
            return config, str(selection.get("game") or selection.get("game_key") or "")

        setattr(catalogless_selection_with_secondary, _REASON_MARKER, True)
        catalogless._catalogless_selection = catalogless_selection_with_secondary


def _install_dashboard_verification() -> None:
    current_slot_snapshot = dashboard._slot_snapshot
    if getattr(current_slot_snapshot, _DASHBOARD_MARKER, False):
        return

    def slot_snapshot_with_secondary_verification(twitch: Any, streamer: Any) -> dict[str, Any]:
        slot = current_slot_snapshot(twitch, streamer)
        config = drop_games_patch._CONFIG.get(id(twitch)) or {}
        state = config.get(_SECONDARY_PROGRESS_KEY)
        if isinstance(state, dict) and state.get("username") == getattr(streamer, "username", None):
            slot["drop_progress_verification"] = {
                "verified": bool(state.get("verified")),
                "deadline_epoch": state.get("deadline_epoch"),
            }
        return slot

    setattr(slot_snapshot_with_secondary_verification, _DASHBOARD_MARKER, True)
    dashboard._slot_snapshot = slot_snapshot_with_secondary_verification


def apply_patch() -> None:
    """Fill an idle second slot with a different game's farmable Drop."""
    if getattr(Twitch, _PATCH_MARKER, False):
        return
    _install_selector()
    _install_reason_support()
    _install_dashboard_verification()
    setattr(Twitch, _PATCH_MARKER, True)
