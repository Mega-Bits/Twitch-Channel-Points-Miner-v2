"""Persist per-broadcast Watch Streak progress across miner restarts."""

import json
import logging
import os
import time
from pathlib import Path
from threading import Lock, Timer

from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.WebSocketsPool import WebSocketsPool
from TwitchChannelPointsMiner.classes.entities.Message import Message
from TwitchChannelPointsMiner.classes.entities.Stream import Stream
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer
from TwitchChannelPointsMiner.utils import get_streamer_index

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_persistent_watch_streak_patch"
_STATE_LOCK = Lock()
_LIVE_REFRESH_LOCK = Lock()
_STATE_CACHE = None
_STREAMERS = {}
_STREAMS = {}
_LIVE_REFRESH_TIMERS = {}
_MAX_AGE_SECONDS = 45 * 24 * 60 * 60
_LIVE_REFRESH_DELAYS = (30, 75)


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


def _refresh_live_stream(twitch, streamer, key):
    try:
        twitch.check_streamer_online(streamer)
    except Exception as exc:
        logger.debug(
            "Unable to refresh %s after stream-up: %s",
            streamer.username,
            exc,
        )
    finally:
        with _LIVE_REFRESH_LOCK:
            _LIVE_REFRESH_TIMERS.pop(key, None)


def _schedule_live_refresh(twitch, streamer):
    for delay in _LIVE_REFRESH_DELAYS:
        key = (id(twitch), streamer.username, delay)
        with _LIVE_REFRESH_LOCK:
            previous = _LIVE_REFRESH_TIMERS.pop(key, None)
            if previous is not None:
                previous.cancel()
            timer = Timer(
                delay,
                _refresh_live_stream,
                args=(twitch, streamer, key),
            )
            timer.daemon = True
            _LIVE_REFRESH_TIMERS[key] = timer
            timer.start()


def _stream_up_streamer(ws, raw_message):
    try:
        response = json.loads(raw_message)
        if response.get("type") != "MESSAGE":
            return None
        message = Message(response["data"])
        if message.topic != "video-playback-by-id" or message.type != "stream-up":
            return None
        index = get_streamer_index(ws.streamers, message.channel_id)
        return ws.streamers[index] if index != -1 else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def apply_patch():
    """Install persistence and live-transition hooks once."""
    check_online = Twitch.check_streamer_online
    if not getattr(check_online, _PATCH_MARKER, False):

        def check_online_with_restore(self, streamer):
            result = check_online(self, streamer)
            if streamer.is_online is True:
                _restore(self, streamer)
            return result

        setattr(check_online_with_restore, _PATCH_MARKER, True)
        Twitch.check_streamer_online = check_online_with_restore

    update_stream = Stream.update
    if not getattr(update_stream, _PATCH_MARKER, False):

        def update_with_broadcast_reset(
            self,
            broadcast_id,
            title,
            game,
            tags,
            viewers_count,
        ):
            previous_broadcast = getattr(self, "broadcast_id", None)
            result = update_stream(
                self,
                broadcast_id,
                title,
                game,
                tags,
                viewers_count,
            )
            if (
                previous_broadcast not in (None, "")
                and broadcast_id not in (None, "")
                and str(previous_broadcast) != str(broadcast_id)
            ):
                self.init_watch_streak()
            return result

        setattr(update_with_broadcast_reset, _PATCH_MARKER, True)
        Stream.update = update_with_broadcast_reset

    on_message = WebSocketsPool.on_message
    if not getattr(on_message, _PATCH_MARKER, False):

        def on_message_with_live_refresh(ws, message):
            result = on_message(ws, message)
            streamer = _stream_up_streamer(ws, message)
            if streamer is not None:
                _schedule_live_refresh(ws.twitch, streamer)
            return result

        setattr(on_message_with_live_refresh, _PATCH_MARKER, True)
        WebSocketsPool.on_message = staticmethod(on_message_with_live_refresh)

    update_history = Streamer.update_history
    if not getattr(update_history, _PATCH_MARKER, False):

        def update_history_with_persistence(
            self,
            reason_code,
            earned,
            counter=1,
        ):
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
