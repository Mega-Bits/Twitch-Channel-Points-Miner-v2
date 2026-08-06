"""Latch unverifiable catalogless game Drops for the current process."""

from __future__ import annotations

import logging
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_runtime_patch as runtime
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_catalogless_game_drop_session_latch_patch"
_DEFAULT_RETRY_COOLDOWN = 0
_SESSION_SETTINGS: dict[int, dict[str, int]] = {}


def _settings_with_session_latch(twitch: Any) -> dict[str, int]:
    settings = dict(_ORIGINAL_SETTINGS(twitch))
    override = _SESSION_SETTINGS.get(id(twitch))
    if override is not None:
        settings["retry_cooldown"] = override["retry_cooldown"]
    return settings


def _failure_history(config: dict[str, Any]) -> dict[str, set[str]]:
    return config.setdefault("catalogless_game_runtime_failure_history", {})


def _failure_usernames_with_history(
    config: dict[str, Any],
    game_key: str,
    now: float,
) -> set[str]:
    current = set(_ORIGINAL_FAILURE_USERNAMES(config, game_key, now))
    history = _failure_history(config).setdefault(game_key, set())
    history.update(current)
    return set(history)


def _cleanup_breakers_with_history(
    config: dict[str, Any],
    now: float,
) -> dict[str, float]:
    before = {
        game_key: runtime._safe_number(deadline)
        for game_key, deadline in (
            config.get("catalogless_game_runtime_breakers", {}) or {}
        ).items()
    }
    breakers = _ORIGINAL_CLEANUP_BREAKERS(config, now)
    history = _failure_history(config)
    for game_key, deadline in before.items():
        if deadline <= now and game_key not in breakers:
            history.pop(game_key, None)
    return breakers


def _clear_game_runtime_with_history(
    config: dict[str, Any],
    game_key: str,
) -> None:
    _ORIGINAL_CLEAR_GAME_RUNTIME(config, game_key)
    _failure_history(config).pop(game_key, None)


def _open_breaker_with_session_latch(
    twitch: Any,
    config: dict[str, Any],
    game_key: str,
    game_name: str,
    failed: set[str],
    now: float,
) -> float:
    cooldown = runtime._settings(twitch)["retry_cooldown"]
    if cooldown > 0:
        return _ORIGINAL_OPEN_BREAKER(
            twitch,
            config,
            game_key,
            game_name,
            failed,
            now,
        )

    breakers = runtime._cleanup_breakers(config, now)
    previous = runtime._safe_number(breakers.get(game_key))
    if previous > now:
        return previous

    deadline = float("inf")
    breakers[game_key] = deadline
    config.setdefault("catalogless_game_runtime_retry_logged", set()).discard(game_key)
    config.pop("game_drop_progress_state", None)
    config.pop("sticky_game_drop_handoff", None)
    if runtime._synthetic_game(config.get("active_selection_campaign_id")) == game_key:
        config.pop("active_catalogless_game", None)

    logger.info(
        "Disabling catalogless Drop discovery for %s for the rest of this "
        "miner session after %s different DROPS_ENABLED channels produced "
        "no Twitch-reported progress; a real Twitch campaign or a miner "
        "restart enables the game again, and no progress or completion "
        "state is written to disk",
        game_name or game_key,
        len(failed),
    )
    return deadline


def _install_runtime_options() -> None:
    current_run = TwitchChannelPointsMiner.run
    if not getattr(current_run, _PATCH_MARKER, False):

        def run_with_session_latch(
            self: TwitchChannelPointsMiner,
            *args: Any,
            drop_game_retry_cooldown: int = _DEFAULT_RETRY_COOLDOWN,
            **kwargs: Any,
        ) -> Any:
            retry_cooldown = max(
                0,
                min(int(drop_game_retry_cooldown), 6 * 60 * 60),
            )
            _SESSION_SETTINGS[id(self.twitch)] = {
                "retry_cooldown": retry_cooldown,
            }
            # The older wrapper clamps its own value to at least 60 seconds.
            # Pass a harmless valid value there; runtime._settings is replaced
            # above and supplies the actual zero-or-positive session setting.
            inner_cooldown = retry_cooldown if retry_cooldown > 0 else 60
            try:
                return current_run(
                    self,
                    *args,
                    drop_game_retry_cooldown=inner_cooldown,
                    **kwargs,
                )
            finally:
                _SESSION_SETTINGS.pop(id(self.twitch), None)

        setattr(run_with_session_latch, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.run = run_with_session_latch

    current_mine = TwitchChannelPointsMiner.mine
    if not getattr(current_mine, _PATCH_MARKER, False):

        def mine_with_session_latch(
            self: TwitchChannelPointsMiner,
            *args: Any,
            drop_game_retry_cooldown: int = _DEFAULT_RETRY_COOLDOWN,
            **kwargs: Any,
        ) -> Any:
            return self.run(
                *args,
                drop_game_retry_cooldown=drop_game_retry_cooldown,
                **kwargs,
            )

        setattr(mine_with_session_latch, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.mine = mine_with_session_latch


def apply_patch() -> None:
    """Make a failed catalogless game stay disabled until a positive signal."""
    if getattr(runtime, _PATCH_MARKER, False):
        return

    global _ORIGINAL_SETTINGS
    global _ORIGINAL_FAILURE_USERNAMES
    global _ORIGINAL_CLEANUP_BREAKERS
    global _ORIGINAL_CLEAR_GAME_RUNTIME
    global _ORIGINAL_OPEN_BREAKER
    _ORIGINAL_SETTINGS = runtime._settings
    _ORIGINAL_FAILURE_USERNAMES = runtime._failure_usernames
    _ORIGINAL_CLEANUP_BREAKERS = runtime._cleanup_breakers
    _ORIGINAL_CLEAR_GAME_RUNTIME = runtime._clear_game_runtime
    _ORIGINAL_OPEN_BREAKER = runtime._open_breaker
    runtime._settings = _settings_with_session_latch
    runtime._failure_usernames = _failure_usernames_with_history
    runtime._cleanup_breakers = _cleanup_breakers_with_history
    runtime._clear_game_runtime = _clear_game_runtime_with_history
    runtime._open_breaker = _open_breaker_with_session_latch
    runtime._DEFAULT_RETRY_COOLDOWN = _DEFAULT_RETRY_COOLDOWN
    _install_runtime_options()
    setattr(runtime, _PATCH_MARKER, True)
