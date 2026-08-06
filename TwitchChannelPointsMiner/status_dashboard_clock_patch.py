"""Align Discord dashboard timestamps with Discord's server clock.

The miner normally uses the container clock for local events. If that clock is
skewed, Discord renders relative timestamps in the future. This patch measures
the dedicated dashboard webhook's HTTP Date header and applies the offset only
to locally-created times.
"""

from __future__ import annotations

import logging
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from TwitchChannelPointsMiner import status_dashboard_patch as dashboard

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_status_dashboard_clock_patch"
_CLOCK_SYNC_SECONDS = 60 * 60
_MAX_CLOCK_SKEW_SECONDS = 7 * 24 * 60 * 60


def _response_clock_offset(
    response: Any,
    started_at: float,
    finished_at: float,
) -> float | None:
    """Return server minus local epoch seconds using an HTTP Date header."""
    date_header = getattr(response, "headers", {}).get("Date")
    if not date_header:
        return None
    try:
        server_time = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, OverflowError):
        return None
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)
    local_midpoint = (started_at + finished_at) / 2
    offset = server_time.timestamp() - local_midpoint
    if abs(offset) > _MAX_CLOCK_SKEW_SECONDS:
        logger.warning(
            "Ignoring implausible Discord clock offset of %.1f seconds",
            offset,
        )
        return None
    return offset


def _sync_discord_clock(state: Any, *, force: bool = False) -> None:
    now_monotonic = time.monotonic()
    checked_at = getattr(state, "_discord_clock_checked_at", 0.0)
    if not force and now_monotonic - checked_at < _CLOCK_SYNC_SECONDS:
        return
    state._discord_clock_checked_at = now_monotonic

    discord = state._discord()
    if discord is None:
        return

    started_at = time.time()
    try:
        response = requests.get(discord.webhook_api, timeout=10)
    except requests.RequestException:
        logger.warning(
            "Unable to synchronize the Discord dashboard clock",
            exc_info=True,
        )
        return
    finished_at = time.time()
    offset = _response_clock_offset(response, started_at, finished_at)
    if offset is not None:
        state._discord_clock_offset = offset


def _correct_epoch(value: Any, offset: float) -> Any:
    if value in (None, 0):
        return value
    try:
        return int(float(value) + offset)
    except (TypeError, ValueError, OverflowError):
        return value


def _correct_event(item: Any, offset: float) -> Any:
    if not isinstance(item, dict):
        return item
    corrected = dict(item)
    corrected["timestamp"] = _correct_epoch(
        corrected.get("timestamp"),
        offset,
    )
    return corrected


def _correct_watch_slot(item: Any, offset: float) -> Any:
    if not isinstance(item, dict):
        return item
    corrected = dict(item)
    verification = corrected.get("drop_progress_verification")
    if isinstance(verification, dict):
        verification = dict(verification)
        verification["deadline_epoch"] = _correct_epoch(
            verification.get("deadline_epoch"),
            offset,
        )
        corrected["drop_progress_verification"] = verification
    return corrected


def apply_patch() -> None:
    """Install dashboard clock correction."""
    state_class = dashboard.DashboardState

    original_start = state_class.start
    if not getattr(original_start, _PATCH_MARKER, False):

        def start_with_clock_sync(self):
            _sync_discord_clock(self, force=True)
            return original_start(self)

        setattr(start_with_clock_sync, _PATCH_MARKER, True)
        state_class.start = start_with_clock_sync

    original_publish = state_class._publish
    if not getattr(original_publish, _PATCH_MARKER, False):

        def publish_with_clock_sync(self):
            _sync_discord_clock(self)
            return original_publish(self)

        setattr(publish_with_clock_sync, _PATCH_MARKER, True)
        state_class._publish = publish_with_clock_sync

    original_snapshot = state_class.snapshot
    if not getattr(original_snapshot, _PATCH_MARKER, False):

        def snapshot_with_corrected_clock(self):
            snapshot = original_snapshot(self)
            offset = float(
                getattr(self, "_discord_clock_offset", 0.0)
            )
            for key in (
                "started_at",
                "stopped_at",
                "last_inventory_sync",
            ):
                snapshot[key] = _correct_epoch(snapshot.get(key), offset)
            snapshot["watch_slots"] = [
                _correct_watch_slot(item, offset)
                for item in snapshot.get("watch_slots", [])
            ]
            snapshot["recent_claims"] = [
                _correct_event(item, offset)
                for item in snapshot.get("recent_claims", [])
            ]
            snapshot["last_points_event"] = _correct_event(
                snapshot.get("last_points_event"),
                offset,
            )
            snapshot["last_non_points_event"] = _correct_event(
                snapshot.get("last_non_points_event"),
                offset,
            )
            snapshot["last_event"] = _correct_event(
                snapshot.get("last_event"),
                offset,
            )
            return snapshot

        setattr(snapshot_with_corrected_clock, _PATCH_MARKER, True)
        state_class.snapshot = snapshot_with_corrected_clock
