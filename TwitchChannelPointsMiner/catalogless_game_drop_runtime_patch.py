"""Stabilize catalogless game Drops without persisting progress or completion."""

from __future__ import annotations

import logging
import time
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import finish_started_drops_patch
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Exceptions import StreamerIsOfflineException

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_catalogless_game_drop_runtime_patch"
_REFRESH_MARKER = "_catalogless_game_drop_runtime_refresh_patch"
_DEFAULT_FAILURE_LIMIT = 3
_DEFAULT_RETRY_COOLDOWN = 30 * 60
_RUNTIME_SETTINGS: dict[int, dict[str, int]] = {}


def _normalize(value: Any) -> str:
    return drop_games_patch._normalize(value)


def _safe_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _stream_game_values(streamer: Any) -> set[str]:
    game = getattr(getattr(streamer, "stream", None), "game", {}) or {}
    if not isinstance(game, dict):
        normalized = _normalize(game)
        return {normalized} if normalized else set()
    return {
        _normalize(value)
        for value in (game.get("id"), game.get("name"), game.get("displayName"))
        if value not in (None, "")
    }


def _synthetic_game(campaign_id: Any) -> str:
    value = str(campaign_id or "")
    if not value.startswith(catalogless._CATALOGLESS_PREFIX):
        return ""
    return value[len(catalogless._CATALOGLESS_PREFIX):]


def _settings(twitch: Any) -> dict[str, int]:
    return _RUNTIME_SETTINGS.get(
        id(twitch),
        {
            "failure_limit": _DEFAULT_FAILURE_LIMIT,
            "retry_cooldown": _DEFAULT_RETRY_COOLDOWN,
        },
    )


def _cleanup_breakers(config: dict[str, Any], now: float) -> dict[str, float]:
    breakers = config.setdefault("catalogless_game_runtime_breakers", {})
    for game_key, deadline in list(breakers.items()):
        if _safe_number(deadline) <= now:
            breakers.pop(game_key, None)
            rejections = config.get("game_drop_progress_rejections", {}) or {}
            for key in list(rejections):
                if (
                    isinstance(key, tuple)
                    and len(key) == 2
                    and key[0] == f"game:{game_key}"
                ):
                    rejections.pop(key, None)
            logged = config.setdefault("catalogless_game_runtime_retry_logged", set())
            if game_key not in logged:
                logger.info(
                    "Retrying catalogless Drop discovery for %s after the in-memory pause expired",
                    game_key,
                )
                logged.add(game_key)
    return breakers


def _clear_game_runtime(config: dict[str, Any], game_key: str) -> None:
    if not game_key:
        return
    config.setdefault("catalogless_game_runtime_breakers", {}).pop(game_key, None)
    config.setdefault("catalogless_game_runtime_retry_logged", set()).discard(game_key)
    rejections = config.get("game_drop_progress_rejections", {}) or {}
    for key in list(rejections):
        if isinstance(key, tuple) and len(key) == 2 and key[0] == f"game:{game_key}":
            rejections.pop(key, None)


def _failure_usernames(config: dict[str, Any], game_key: str, now: float) -> set[str]:
    rejections = config.get("game_drop_progress_rejections", {}) or {}
    failed: set[str] = set()
    for key, deadline in list(rejections.items()):
        if _safe_number(deadline) <= now:
            rejections.pop(key, None)
            continue
        if (
            isinstance(key, tuple)
            and len(key) == 2
            and key[0] == f"game:{game_key}"
            and str(key[1]).strip()
        ):
            failed.add(str(key[1]))
    return failed


def _open_breaker(
    twitch: Any,
    config: dict[str, Any],
    game_key: str,
    game_name: str,
    failed: set[str],
    now: float,
) -> float:
    cooldown = _settings(twitch)["retry_cooldown"]
    deadline = now + cooldown
    breakers = _cleanup_breakers(config, now)
    previous = _safe_number(breakers.get(game_key))
    if previous > now:
        return previous
    breakers[game_key] = deadline
    config.setdefault("catalogless_game_runtime_retry_logged", set()).discard(game_key)
    config.pop("game_drop_progress_state", None)
    config.pop("sticky_game_drop_handoff", None)
    if _synthetic_game(config.get("active_selection_campaign_id")) == game_key:
        config.pop("active_catalogless_game", None)
    logger.info(
        "Pausing catalogless Drop discovery for %s for %s seconds after %s different DROPS_ENABLED channels produced no Twitch-reported progress; no progress or completion state is written to disk",
        game_name or game_key,
        cooldown,
        len(failed),
    )
    return deadline


def _candidate_is_real_game_drop(candidate: Any) -> bool:
    return bool(
        candidate is not None
        and len(candidate) >= 3
        and candidate[2] == "game_drop"
        and not _synthetic_game(candidate[0])
    )


def _campaign_game(config: dict[str, Any], campaign_id: Any) -> str:
    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    game = getattr(campaign, "game", {}) or {}
    if isinstance(game, dict):
        return _normalize(game.get("displayName") or game.get("name") or game.get("id"))
    return _normalize(game)


def _candidate_game(config: dict[str, Any], candidate: Any) -> str:
    if candidate is None or len(candidate) < 3 or candidate[2] != "game_drop":
        return ""
    return _synthetic_game(candidate[0]) or _campaign_game(config, candidate[0])


def _watchable_for_game(streamer: Any, game_key: str) -> bool:
    if not configured._watchable(streamer):
        return False
    if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
        return False
    games = _stream_game_values(streamer)
    return not games or game_key in games


def _active_catalogless_candidate(
    streamers: list[Any],
    config: dict[str, Any],
    now: float,
) -> tuple[str, int, str] | None:
    campaign_id = str(config.get("active_selection_campaign_id") or "")
    game_key = _synthetic_game(campaign_id)
    username = str(config.get("active_selection_streamer") or "")
    kind = str(config.get("active_selection_kind") or "")
    if not game_key or not username or kind != "game_drop":
        return None
    if username in _failure_usernames(config, game_key, now):
        return None
    index = next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )
    if index is None or not _watchable_for_game(streamers[index], game_key):
        return None
    return campaign_id, index, "game_drop"


def _mapping_game(config: dict[str, Any], streamer: Any) -> str:
    mapping = config.get("catalogless_streamer_games", {}) or {}
    return _normalize(mapping.get(getattr(streamer, "username", "")))


def _without_catalogless_game(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    game_key: str,
):
    hidden: dict[int, bool] = {}
    for index, streamer in enumerate(streamers):
        mapped = _mapping_game(config, streamer)
        if mapped != game_key:
            continue
        hidden[index] = bool(getattr(streamer, "is_online", False))
        streamer.is_online = False
    try:
        return _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)
    finally:
        for index, online in hidden.items():
            streamers[index].is_online = online


def _candidate_with_runtime_breaker(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    now = time.time()
    active = _active_catalogless_candidate(streamers, config, now)
    candidate = _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)

    if _candidate_is_real_game_drop(candidate):
        game_key = _candidate_game(config, candidate)
        _clear_game_runtime(config, game_key)
        return candidate

    game_key = _synthetic_game(active[0]) if active is not None else _candidate_game(config, candidate)
    if not game_key:
        return candidate

    breakers = _cleanup_breakers(config, now)
    if _safe_number(breakers.get(game_key)) > now:
        return _without_catalogless_game(twitch, streamers, config, game_key)

    failed = _failure_usernames(config, game_key, now)
    limit = _settings(twitch)["failure_limit"]
    if limit > 0 and len(failed) >= limit:
        game_name = str(config.get("active_catalogless_game") or game_key)
        _open_breaker(twitch, config, game_key, game_name, failed, now)
        return _without_catalogless_game(twitch, streamers, config, game_key)

    # Directory pagination and viewer sorting are not eligibility changes. Keep
    # the active provisional channel until the progress verifier rejects it or
    # a fresh live check proves that it went offline or changed games.
    if active is not None:
        return active
    return candidate


def _refresh_stream_directly(twitch: Any, streamer: Any) -> bool:
    try:
        info = twitch.get_stream_info(streamer)
    except StreamerIsOfflineException:
        streamer.is_online = False
        return False
    except Exception as exc:
        logger.debug(
            "Unable to confirm active catalogless Drop channel %s: %s",
            getattr(streamer, "username", "unknown"),
            exc,
        )
        return bool(getattr(streamer, "is_online", False))
    if not isinstance(info, dict):
        return bool(getattr(streamer, "is_online", False))
    stream_info = info.get("stream") or {}
    broadcast = info.get("broadcastSettings") or {}
    if not stream_info:
        streamer.is_online = False
        return False
    try:
        streamer.stream.update(
            broadcast_id=stream_info.get("id"),
            title=str(broadcast.get("title") or ""),
            game=broadcast.get("game") or {},
            tags=stream_info.get("tags") or [],
            viewers_count=stream_info.get("viewersCount") or 0,
        )
    except Exception:
        return bool(getattr(streamer, "is_online", False))
    streamer.is_online = True
    return True


def _install_refresh_stability() -> None:
    current = finish_started_drops_patch._ORIGINAL_REFRESH
    if getattr(current, _REFRESH_MARKER, False):
        return

    def refresh_with_active_catalogless_stability(
        twitch: Any,
        streamers: list[Any],
        campaigns: list[Any],
    ) -> Any:
        config = drop_games_patch._CONFIG.get(id(twitch)) or {}
        campaign_id = str(config.get("active_selection_campaign_id") or "")
        game_key = _synthetic_game(campaign_id)
        username = str(config.get("active_selection_streamer") or "")
        active = next(
            (
                streamer
                for streamer in streamers
                if getattr(streamer, "username", None) == username
            ),
            None,
        ) if game_key and username else None
        old_mapping = str(
            (config.get("catalogless_streamer_games", {}) or {}).get(username)
            or config.get("active_catalogless_game")
            or game_key
        )
        old_online_at = getattr(active, "online_at", 0) if active is not None else 0

        result = current(twitch, streamers, campaigns)
        if active is None or not game_key:
            return result

        mapping = config.setdefault("catalogless_streamer_games", {})
        if _normalize(mapping.get(username)) == game_key and getattr(active, "is_online", False):
            return result

        # The latest directory page can omit a still-live channel. Confirm it
        # directly before restoring its provisional assignment.
        if not _refresh_stream_directly(twitch, active):
            return result
        if game_key not in _stream_game_values(active):
            return result

        active.is_online = True
        if old_online_at:
            active.online_at = old_online_at
        mapping[username] = old_mapping
        if _source(active) == "game_drop":
            active.fallback_campaign_ids = frozenset({catalogless._CATALOGLESS_FALLBACK_ID})
        return result

    setattr(refresh_with_active_catalogless_stability, _REFRESH_MARKER, True)
    finish_started_drops_patch._ORIGINAL_REFRESH = refresh_with_active_catalogless_stability


def _install_runtime_options() -> None:
    current_run = TwitchChannelPointsMiner.run
    if not getattr(current_run, _PATCH_MARKER, False):

        def run_with_catalogless_runtime_control(
            self: TwitchChannelPointsMiner,
            *args: Any,
            drop_game_failure_limit: int = _DEFAULT_FAILURE_LIMIT,
            drop_game_retry_cooldown: int = _DEFAULT_RETRY_COOLDOWN,
            **kwargs: Any,
        ) -> Any:
            failure_limit = max(0, min(int(drop_game_failure_limit), 20))
            retry_cooldown = max(60, min(int(drop_game_retry_cooldown), 6 * 60 * 60))
            _RUNTIME_SETTINGS[id(self.twitch)] = {
                "failure_limit": failure_limit,
                "retry_cooldown": retry_cooldown,
            }
            try:
                return current_run(self, *args, **kwargs)
            finally:
                _RUNTIME_SETTINGS.pop(id(self.twitch), None)

        setattr(run_with_catalogless_runtime_control, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.run = run_with_catalogless_runtime_control

    current_mine = TwitchChannelPointsMiner.mine
    if not getattr(current_mine, _PATCH_MARKER, False):

        def mine_with_catalogless_runtime_control(
            self: TwitchChannelPointsMiner,
            *args: Any,
            drop_game_failure_limit: int = _DEFAULT_FAILURE_LIMIT,
            drop_game_retry_cooldown: int = _DEFAULT_RETRY_COOLDOWN,
            **kwargs: Any,
        ) -> Any:
            return self.run(
                *args,
                drop_game_failure_limit=drop_game_failure_limit,
                drop_game_retry_cooldown=drop_game_retry_cooldown,
                **kwargs,
            )

        setattr(mine_with_catalogless_runtime_control, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.mine = mine_with_catalogless_runtime_control


def apply_patch() -> None:
    """Install non-persistent catalogless Drop stability and circuit breaking."""
    current = priority_order._drop_candidate
    if getattr(current, _PATCH_MARKER, False):
        return

    global _ORIGINAL_DROP_CANDIDATE
    _ORIGINAL_DROP_CANDIDATE = current
    setattr(_candidate_with_runtime_breaker, _PATCH_MARKER, True)
    priority_order._drop_candidate = _candidate_with_runtime_breaker
    _install_refresh_stability()
    _install_runtime_options()
