"""Watch-transport diagnostics and HLS HEAD policy for Twitch Drop sessions.

Spade HTTP 204 and successful HLS requests prove only that the transport
requests worked, NOT that Twitch awarded another Drop minute.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_MAX_SEGMENTS = 15
_WARNING_INTERVAL = 5 * 60
_HEALTH_INTERVAL = 15 * 60
_LOCK = threading.RLock()
_WARNED: dict[tuple[int, str, str], float] = {}
_HEALTHY: dict[tuple[int, str], float] = {}
_DROP_KINDS = {"game_drop", "drop_completion"}


def is_drop_watch(config: dict[str, Any] | None, streamer: Any) -> bool:
    """Only the primary chosen Drop watch slot gets full HLS probing in auto mode."""
    if not isinstance(config, dict):
        return False
    return (
        config.get("active_selection_streamer") == getattr(streamer, "username", None)
        and str(config.get("active_selection_kind") or "") in _DROP_KINDS
    )


def use_full_playlist(config: dict[str, Any] | None, streamer: Any, mode: str | None = None) -> bool:
    """TWITCH_HLS_HEAD_MODE: auto (default), all, or single."""
    value = str(
        mode if mode is not None else os.environ.get("TWITCH_HLS_HEAD_MODE", "auto")
    ).strip().lower()
    if value == "all":
        return True
    if value == "single":
        return False
    # Invalid input falls back to auto, never to an unbounded number of calls.
    return is_drop_watch(config, streamer)


def head_segments(segments: list[str], *, full: bool) -> list[str]:
    if not segments:
        return []
    # Favor the freshest segment URLs. Unlike the old single HEAD probe,
    # this checks the entire advertised recent sequence for the Drop slot.
    return segments[-_MAX_SEGMENTS:] if full else segments[-1:]


def transport_failed(
    client: Any,
    streamer: Any,
    stage: str,
    *,
    http_status: int | None = None,
    exception_type: str | None = None,
) -> None:
    """Warn about silent HLS/Spade failures at most once per 5 min per stage."""
    username = str(getattr(streamer, "username", "unknown"))
    key = (id(client), username, stage)
    now = time.monotonic()
    with _LOCK:
        if now - _WARNED.get(key, -_WARNING_INTERVAL - 1) < _WARNING_INTERVAL:
            return
        _WARNED[key] = now
    if http_status is not None:
        detail = f"HTTP {int(http_status)}"
    elif exception_type:
        # Never log raw exceptions or signed playlist URLs / authentication data.
        detail = "request " + "".join(
            c for c in str(exception_type)[:40] if c.isalnum() or c == "_"
        )
    else:
        detail = "invalid or missing response"
    logger.warning(
        "Twitch watch transport failed for %s at %s (%s); Drops may not progress. "
        "Check Pi DNS/adblocking and connectivity to Twitch CDN / spade.twitch.tv",
        username, stage, detail,
    )


def transport_accepted(client: Any, streamer: Any, segment_count: int) -> None:
    """Periodic proof of HTTP transport success, not proof of Twitch Drop credit."""
    username = str(getattr(streamer, "username", "unknown"))
    key = (id(client), username)
    now = time.monotonic()
    with _LOCK:
        if now - _HEALTHY.get(key, -_HEALTH_INTERVAL - 1) < _HEALTH_INTERVAL:
            return
        _HEALTHY[key] = now
    logger.info(
        "Twitch watch transport accepted for Drop channel %s: %s HLS HEAD(s) "
        "and Spade HTTP 204; verify credited Drop minutes in Twitch inventory",
        username, segment_count,
    )
