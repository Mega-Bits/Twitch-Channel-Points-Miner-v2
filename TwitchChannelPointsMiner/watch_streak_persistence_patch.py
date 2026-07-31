"""Persist per-broadcast Watch Streak progress across miner restarts."""

import json
import logging
import os
import time
from pathlib import Path
from threading import Lock

from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.entities.Stream import Stream
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_persistent_watch_streak_patch"
_STATE_LOCK = Lock()
_STATE_CACHE = None
_STREAMERS = {}
_STREAMS = {}
_MAX_AGE_SECONDS = 45 * 24 * 60 * 60


def _state_path():
    path = Path().absolute() / "cookies" / "watch_streak_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_state():
    global _STATE_CACHE
    with _STATE_LOCK:
        if _STATE_CACHE is not None:
            return _STATE_CACHE
        path = _state_path()
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
                _STATE_CACHE = data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            _STATE_CACHE = {}
        _prune_locked()
        return _STATE_CACHE


def _prune_locked():
    if _STATE_CACHE is None:
        return
    cutoff = time.time() - _MAX_AGE_SECONDS
    for account in list(_STATE_CACHE):
        channels = _STATE_CACHE.get(account)
        if not isinstance(channels, dict):
            _STATE_CACHE.pop(account, None)
            continue
        for channel in list(channels):
            entry = channels.get(channel)
            if not isinstance(entry, dict) or entry.get("updated_at", 0) < cutoff:
                channels.pop(channel, None)
        if not channels:
            _STATE_CACHE.pop(account, None)


def _save_state():
    with _STATE_LOCK:
        _prune_locked()
        path = _state_path()
        temp_path = path.with_suffix(".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(_STATE_CACHE or {}, handle, indent=2, sort_keys=True)
            os.replace(temp_path, path)
        except OSError as exc:
            logger.warning("Unable to persist Watch Streak state: %s", exc)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _account_key(twitch):
    return Path(twitch.cookies_file).stem


def _broadcast_id(streamer):
    value = getattr(streamer.stream, "broadcast_id", None)
    return "" if value is None else str(value)


def _entry_for(twitch, streamer, create=True):
    broadcast_id = _broadcast_id(streamer)
    if not broadcast_id:
        return None
    state = _load_state()
    account = state.setdefault(_account_key(twitch), {})
    entry = account.get(streamer.username)
    if not isinstance(entry, dict) or entry.get("broadcast_id") != broadcast_id:
        if not create:
            return None
        entry = {
            "broadcast_id": broadcast_id,
            "minute_watched": 0,
            "watch_streak_missing": True,
            "updated_at": time.time(),
        }
        account[streamer.username] = entry
    return entry


def _restore(twitch, streamer):
    settings = getattr(streamer, "settings", None)
    if settings is None or getattr(settings, "watch_streak", False) is not True:
        return
    entry = _entry_for(twitch, streamer)
    if entry is None:
        return
    streamer.stream.minute_watched = max(
        float(getattr(streamer.stream, "minute_watched", 0) or 0),
        float(entry.get("minute_watched", 0) or 0),
    )
    streamer.stream.watch_streak_missing = bool(
        entry.get("watch_streak_missing", True)
    )
    registration = (twitch, streamer)
    _STREAMERS[id(streamer)] = registration
    _STREAMS[id(streamer.stream)] = registration


def _persist_streamer(twitch, streamer):
    entry = _entry_for(twitch, streamer)
    if entry is None:
        return
    entry.update(
        {
            "minute_watched": float(
                getattr(streamer.stream, "minute_watched", 0) or 0
            ),
            "watch_streak_missing": bool(
                getattr(streamer.stream, "watch_streak_missing", True)
            ),
            "updated_at": time.time(),
        }
    )
    _save_state()


def apply_patch():
    """Install persistence hooks once."""
    check_online = Twitch.check_streamer_online
    if not getattr(check_online, _PATCH_MARKER, False):
        def check_online_with_restore(self, streamer):
            result = check_online(self, streamer)
            if streamer.is_online is True:
                _restore(self, streamer)
            return result

        setattr(check_online_with_restore, _PATCH_MARKER, True)
        Twitch.check_streamer_online = check_online_with_restore

    update_history = Streamer.update_history
    if not getattr(update_history, _PATCH_MARKER, False):
        def update_history_with_persistence(self, reason_code, earned, counter=1):
            result = update_history(self, reason_code, earned, counter)
            registration = _STREAMERS.get(id(self))
            if registration is not None and reason_code == "WATCH_STREAK":
                twitch, streamer = registration
                _persist_streamer(twitch, streamer)
            return result

        setattr(update_history_with_persistence, _PATCH_MARKER, True)
        Streamer.update_history = update_history_with_persistence

    update_minute = Stream.update_minute_watched
    if not getattr(update_minute, _PATCH_MARKER, False):
        def update_minute_with_persistence(self):
            result = update_minute(self)
            registration = _STREAMS.get(id(self))
            if registration is not None:
                twitch, streamer = registration
                _persist_streamer(twitch, streamer)
            return result

        setattr(update_minute_with_persistence, _PATCH_MARKER, True)
        Stream.update_minute_watched = update_minute_with_persistence
