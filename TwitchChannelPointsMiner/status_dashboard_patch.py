"""Persistent Discord status dashboard for the miner.

The dashboard is a single webhook message that is created once and edited when
watch slots, inventory state, claims, or relevant events change. Discord's
native timestamp syntax is used so every viewer sees local time automatically.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from TwitchChannelPointsMiner import discord_format_patch
from TwitchChannelPointsMiner.classes.Discord import Discord
from TwitchChannelPointsMiner.classes.Settings import Events, Settings
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_status_dashboard_patch"
_STATE_LOCK = threading.RLock()
_STATES: dict[int, "DashboardState"] = {}
_HANDLER: "DashboardEventHandler | None" = None

_POINTS_EVENTS = {
    Events.GAIN_FOR_RAID,
    Events.GAIN_FOR_CLAIM,
    Events.GAIN_FOR_WATCH,
    Events.GAIN_FOR_WATCH_STREAK,
}
_IGNORED_LAST_EVENTS = {Events.DROP_STATUS, Events.STARTUP_STATUS}


def _discord_timestamp(value: int | float | None, style: str = "R") -> str:
    if not value:
        return "`pending`"
    return f"<t:{int(value)}:{style}>"


def _datetime_timestamp(value: Any) -> int | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return int(value.timestamp())
    return int(calendar.timegm(value.utctimetuple()))


def _channel(username: str) -> str:
    return f"[{username}](https://twitch.tv/{username})"


def _name(value: Any) -> str:
    return getattr(value, "name", str(value))


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list"))


def _event_value(value: Any) -> Events | None:
    if isinstance(value, Events):
        return value
    return Events.get(value)


def _trim(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 14)].rstrip() + "\n… truncated"


def _webhook_message_url(webhook_api: str, message_id: str) -> str:
    parts = urlsplit(webhook_api)
    path = f"{parts.path.rstrip('/')}/messages/{message_id}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _webhook_fingerprint(webhook_api: str) -> str:
    return hashlib.sha256(webhook_api.encode("utf-8")).hexdigest()


def _drop_snapshot(drop: Any) -> dict[str, Any]:
    return {
        "name": str(getattr(drop, "name", "Unknown drop")),
        "reward": str(getattr(drop, "benefit", "")),
        "current": getattr(drop, "current_minutes_watched", 0) or 0,
        "required": getattr(drop, "minutes_required", 0) or 0,
        "percent": getattr(drop, "percentage_progress", 0) or 0,
    }


def _campaign_snapshot(campaign: Any, streamers: list[Any], locked_id: Any) -> dict[str, Any]:
    game = getattr(campaign, "game", {}) or {}
    game_name = (
        game.get("displayName") or game.get("name") or "Unknown game"
        if isinstance(game, dict)
        else str(game)
    )
    eligible: list[dict[str, str]] = []
    for streamer in streamers:
        stream = getattr(streamer, "stream", None)
        campaign_ids = getattr(stream, "campaigns_ids", []) or []
        settings = getattr(streamer, "settings", None)
        if (
            getattr(streamer, "is_online", False) is True
            and getattr(settings, "claim_drops", False) is True
            and getattr(campaign, "id", None) in campaign_ids
        ):
            eligible.append({"username": streamer.username, "source": _source(streamer)})

    return {
        "id": getattr(campaign, "id", None),
        "name": str(getattr(campaign, "name", "Unknown campaign")),
        "game": str(game_name),
        "end_at": _datetime_timestamp(getattr(campaign, "end_at", None)),
        "locked": getattr(campaign, "id", None) == locked_id,
        "drops": [_drop_snapshot(drop) for drop in (getattr(campaign, "drops", []) or [])],
        "eligible": eligible,
    }


def _slot_snapshot(twitch: Twitch, streamer: Any) -> dict[str, Any]:
    reason = "Priority"
    campaign_data = None
    locked_id = getattr(twitch, "locked_drop_campaign_id", None)
    campaigns = list(getattr(getattr(streamer, "stream", None), "campaigns", []) or [])
    if locked_id is not None:
        campaigns.sort(key=lambda campaign: getattr(campaign, "id", None) != locked_id)
    for campaign in campaigns:
        drops = [
            drop
            for drop in (getattr(campaign, "drops", []) or [])
            if not getattr(drop, "is_claimed", False)
        ]
        if drops:
            reason = "Drop"
            game = getattr(campaign, "game", {}) or {}
            campaign_data = {
                "id": getattr(campaign, "id", None),
                "name": str(getattr(campaign, "name", "Unknown campaign")),
                "game": (
                    game.get("displayName") or game.get("name") or "Unknown game"
                    if isinstance(game, dict)
                    else str(game)
                ),
                "drop": _drop_snapshot(drops[0]),
            }
            break
    if reason != "Drop" and (
        getattr(getattr(streamer, "settings", None), "watch_streak", False) is True
        and getattr(getattr(streamer, "stream", None), "watch_streak_missing", False) is True
        and getattr(getattr(streamer, "stream", None), "minute_watched", 0) < 7
    ):
        reason = "Watch streak"

    return {
        "username": streamer.username,
        "source": _source(streamer),
        "reason": reason,
        "campaign": campaign_data,
    }


class DashboardState:
    def __init__(self, twitch: Twitch, streamers: list[Any], priority: list[Any]) -> None:
        self.twitch = twitch
        self.streamers = streamers
        self.priority = priority
        self.started_at = int(time.time())
        self.stopped_at: int | None = None
        self.last_inventory_sync: int | None = None
        self.watch_slots: list[dict[str, Any]] = []
        self.campaigns: list[dict[str, Any]] = []
        self.recent_claims: deque[dict[str, Any]] = deque(maxlen=5)
        self.last_points_event: dict[str, Any] | None = None
        self.last_event: dict[str, Any] | None = None
        self.message_id: str | None = None
        self._stored_webhook_fingerprint: str | None = None
        self._lock = threading.RLock()
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._last_publish = 0.0
        self._thread = threading.Thread(
            target=self._worker,
            name="Discord status dashboard",
            daemon=True,
        )
        self._state_path = self._resolve_state_path()
        self._load()

    def _resolve_state_path(self) -> Path:
        cookies_file = Path(getattr(self.twitch, "cookies_file", "miner.pkl"))
        cookies_file.parent.mkdir(parents=True, exist_ok=True)
        return cookies_file.with_name(f"{cookies_file.stem}.discord-dashboard.json")

    def _load(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        self.message_id = data.get("message_id") or None
        self._stored_webhook_fingerprint = data.get("webhook_fingerprint") or None
        for claim in data.get("recent_claims", []):
            if isinstance(claim, dict):
                self.recent_claims.append(claim)
        points = data.get("last_points_event")
        event = data.get("last_event")
        self.last_points_event = points if isinstance(points, dict) else None
        self.last_event = event if isinstance(event, dict) else None

    def _save(self, webhook_api: str | None = None) -> None:
        fingerprint = (
            _webhook_fingerprint(webhook_api)
            if webhook_api
            else self._stored_webhook_fingerprint
        )
        data = {
            "message_id": self.message_id,
            "webhook_fingerprint": fingerprint,
            "recent_claims": list(self.recent_claims),
            "last_points_event": self.last_points_event,
            "last_event": self.last_event,
        }
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self._state_path)
            self._stored_webhook_fingerprint = fingerprint
        except OSError:
            logger.warning("Unable to persist Discord dashboard state", exc_info=True)

    def start(self) -> None:
        self._thread.start()
        self.mark_dirty()

    def stop(self) -> None:
        self.stopped_at = int(time.time())
        self.mark_dirty()
        time.sleep(0.2)
        self._stop.set()
        self._dirty.set()
        self._thread.join(timeout=5)

    def mark_dirty(self) -> None:
        self._dirty.set()

    def update_inventory(self, streamers: list[Any], campaigns: list[Any]) -> None:
        locked_id = getattr(self.twitch, "locked_drop_campaign_id", None)
        snapshots = [
            _campaign_snapshot(campaign, streamers, locked_id)
            for campaign in campaigns
            if getattr(campaign, "drops", [])
        ]
        snapshots.sort(
            key=lambda item: (
                not item["locked"],
                -max((drop["percent"] for drop in item["drops"]), default=0),
            )
        )
        with self._lock:
            self.last_inventory_sync = int(time.time())
            self.campaigns = snapshots
        self.mark_dirty()

    def update_watch(self, streamers: list[Any], selected_indexes: list[int]) -> None:
        slots = [
            _slot_snapshot(self.twitch, streamers[index])
            for index in selected_indexes
            if 0 <= index < len(streamers)
        ]
        with self._lock:
            if slots == self.watch_slots:
                return
            self.watch_slots = slots
        self.mark_dirty()

    def record_event(self, event: Events, message: str, created: float) -> None:
        formatted = discord_format_patch._details(message, event)
        item = {
            "event": str(event),
            "message": _trim(formatted, 900),
            "timestamp": int(created),
        }
        with self._lock:
            if event == Events.DROP_CLAIM:
                self.recent_claims.appendleft(item)
            if event in _POINTS_EVENTS:
                self.last_points_event = item
            if event not in _IGNORED_LAST_EVENTS:
                self.last_event = item
        self._save()
        self.mark_dirty()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.stopped_at is None,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "last_inventory_sync": self.last_inventory_sync,
                "priority": [_name(item) for item in self.priority],
                "watch_slots": list(self.watch_slots),
                "campaigns": list(self.campaigns),
                "recent_claims": list(self.recent_claims),
                "last_points_event": self.last_points_event,
                "last_event": self.last_event,
            }

    def _discord(self) -> Discord | None:
        logger_settings = getattr(Settings, "logger", None)
        discord = getattr(logger_settings, "discord", None) if logger_settings else None
        if discord is None or not getattr(discord, "webhook_api", ""):
            return None
        return discord

    def _worker(self) -> None:
        while not self._stop.is_set():
            self._dirty.wait(timeout=60)
            self._dirty.clear()
            if not self.streamers and self.stopped_at is None:
                continue
            elapsed = time.monotonic() - self._last_publish
            if elapsed < 5:
                self._stop.wait(5 - elapsed)
            self._publish()
        if self.stopped_at is not None:
            self._publish()

    def _publish(self) -> None:
        discord = self._discord()
        if discord is None:
            return
        webhook_api = discord.webhook_api
        fingerprint = _webhook_fingerprint(webhook_api)
        if self._stored_webhook_fingerprint not in (None, fingerprint):
            self.message_id = None
        payload = self._payload()
        try:
            if self.message_id:
                response = requests.patch(
                    _webhook_message_url(webhook_api, self.message_id),
                    params={"with_components": "true"},
                    json=payload,
                    timeout=20,
                )
                if response.status_code == 404:
                    self.message_id = None
                elif response.ok:
                    self._last_publish = time.monotonic()
                    self._save(webhook_api)
                    return
                else:
                    response.raise_for_status()

            response = requests.post(
                webhook_api,
                params={"wait": "true", "with_components": "true"},
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            self.message_id = str(response.json()["id"])
            self._last_publish = time.monotonic()
            self._save(webhook_api)
        except (requests.RequestException, ValueError, KeyError):
            logger.warning("Unable to update Discord status dashboard", exc_info=True)

    def _payload(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        components = [
            {
                "type": Discord.COMPONENT_TEXT_DISPLAY,
                "content": "### 📊 Miner status",
            },
            {
                "type": Discord.COMPONENT_TEXT_DISPLAY,
                "content": _trim(self._render_overview(snapshot), 3500),
            },
            {
                "type": Discord.COMPONENT_TEXT_DISPLAY,
                "content": _trim(self._render_campaigns(snapshot), 3500),
            },
            {
                "type": Discord.COMPONENT_TEXT_DISPLAY,
                "content": _trim(self._render_activity(snapshot), 3500),
            },
        ]
        return {
            "username": "Twitch Channel Points Miner",
            "avatar_url": "https://i.imgur.com/X9fEkhT.png",
            "allowed_mentions": {"parse": []},
            "flags": Discord.IS_COMPONENTS_V2,
            "components": [
                {
                    "type": Discord.COMPONENT_CONTAINER,
                    "accent_color": 0x5865F2 if snapshot["running"] else 0x64748B,
                    "components": components,
                }
            ],
        }

    def _render_overview(self, snapshot: dict[str, Any]) -> str:
        status = "running" if snapshot["running"] else "stopped"
        lines = [
            f"**Status:** `{status}`",
            f"**Started:** {_discord_timestamp(snapshot['started_at'], 'F')} · {_discord_timestamp(snapshot['started_at'])}",
            f"**Last inventory sync:** {_discord_timestamp(snapshot['last_inventory_sync'])}",
            f"**Priority:** `{' > '.join(snapshot['priority']) or 'none'}`",
            f"**Watch slots:** `{len(snapshot['watch_slots'])}/2`",
            "",
            "**Currently watching**",
        ]
        if not snapshot["watch_slots"]:
            lines.append("No stream selected.")
        for position, slot in enumerate(snapshot["watch_slots"], start=1):
            lines.append(
                f"{position}. {_channel(slot['username'])} · `{slot['reason']}` · `{slot['source']}`"
            )
            campaign = slot.get("campaign")
            if campaign:
                drop = campaign["drop"]
                lines.append(
                    f"   {campaign['game']} · {drop['name']} · `{drop['current']}/{drop['required']} min` · `{drop['percent']}%`"
                )
        return "\n".join(lines)

    def _render_campaigns(self, snapshot: dict[str, Any]) -> str:
        lines = ["**Drops and queue**"]
        if not snapshot["campaigns"]:
            lines.append("No open Drop campaign is currently tracked.")
            return "\n".join(lines)

        for campaign in snapshot["campaigns"][:6]:
            marker = "active" if campaign["locked"] else "queued"
            lines.append(
                f"**{campaign['game']} — {campaign['name']}** · `{marker}` · ends {_discord_timestamp(campaign['end_at'])}"
            )
            for drop in campaign["drops"][:3]:
                lines.append(
                    f"- {drop['name']} · `{drop['current']}/{drop['required']} min` · `{drop['percent']}%`"
                )
            if len(campaign["drops"]) > 3:
                lines.append(f"- `+{len(campaign['drops']) - 3} more Drops`")
            candidates = campaign["eligible"][:4]
            if candidates:
                rendered = ", ".join(
                    f"{_channel(item['username'])} (`{item['source']}`)"
                    for item in candidates
                )
                lines.append(f"Candidates: {rendered}")
            else:
                lines.append("Candidates: `searching`")
        if len(snapshot["campaigns"]) > 6:
            lines.append(f"`+{len(snapshot['campaigns']) - 6} more campaigns`")
        return "\n".join(lines)

    def _render_activity(self, snapshot: dict[str, Any]) -> str:
        lines = ["**Recent activity**"]
        points = snapshot.get("last_points_event")
        if points:
            lines.append(
                f"**Last points event:** {points['message']} · {_discord_timestamp(points['timestamp'])}"
            )
        else:
            lines.append("**Last points event:** `none`")

        event = snapshot.get("last_event")
        if event:
            lines.append(
                f"**Last event:** `{event['event']}` · {event['message']} · {_discord_timestamp(event['timestamp'])}"
            )
        else:
            lines.append("**Last event:** `none`")

        lines.append("**Recent Drop claims**")
        if not snapshot["recent_claims"]:
            lines.append("No Drop has been claimed in the retained dashboard history.")
        for claim in snapshot["recent_claims"]:
            compact = claim["message"].replace("\n", " · ")
            lines.append(f"- {compact} · {_discord_timestamp(claim['timestamp'])}")
        return "\n".join(lines)


class DashboardEventHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        event = _event_value(getattr(record, "event", None))
        if event is None:
            return
        try:
            message = record.getMessage()
            with _STATE_LOCK:
                states = list(_STATES.values())
            for state in states:
                state.record_event(event, message, record.created)
        except Exception:
            self.handleError(record)


def get_status_snapshot(twitch: Twitch | None = None) -> dict[str, Any]:
    """Return a JSON-safe status snapshot for later integrations."""
    with _STATE_LOCK:
        if twitch is not None:
            state = _STATES.get(id(twitch))
            return state.snapshot() if state else {}
        if len(_STATES) == 1:
            return next(iter(_STATES.values())).snapshot()
        return {str(key): state.snapshot() for key, state in _STATES.items()}


def request_dashboard_refresh(twitch: Twitch | None = None) -> bool:
    with _STATE_LOCK:
        if twitch is not None:
            state = _STATES.get(id(twitch))
            if state is None:
                return False
            state.mark_dirty()
            return True
        for state in _STATES.values():
            state.mark_dirty()
        return bool(_STATES)


def apply_patch() -> None:
    """Install dashboard lifecycle and state hooks once."""
    global _HANDLER
    if _HANDLER is None:
        _HANDLER = DashboardEventHandler(level=logging.INFO)
        logging.getLogger().addHandler(_HANDLER)

    select = getattr(Twitch, "_select_streamers_to_watch", None)
    if select is not None and not getattr(select, _PATCH_MARKER, False):
        def select_with_dashboard(self, streamers, priority, max_watch_amount=2):
            selected = list(select(self, streamers, priority, max_watch_amount))
            with _STATE_LOCK:
                state = _STATES.get(id(self))
            if state is not None:
                state.update_watch(streamers, selected)
            return selected

        setattr(select_with_dashboard, _PATCH_MARKER, True)
        Twitch._select_streamers_to_watch = select_with_dashboard

    sync_name = "_Twitch__sync_drop_campaign_state"
    sync_state = getattr(Twitch, sync_name, None)
    if sync_state is not None and not getattr(sync_state, _PATCH_MARKER, False):
        def sync_with_dashboard(self, streamers, campaigns):
            result = sync_state(self, streamers, campaigns)
            with _STATE_LOCK:
                state = _STATES.get(id(self))
            if state is not None:
                state.update_inventory(streamers, campaigns)
            return result

        setattr(sync_with_dashboard, _PATCH_MARKER, True)
        setattr(Twitch, sync_name, sync_with_dashboard)

    run = TwitchChannelPointsMiner.run
    if not getattr(run, _PATCH_MARKER, False):
        def run_with_dashboard(self, *args, **kwargs):
            state = DashboardState(self.twitch, self.streamers, self.priority)
            with _STATE_LOCK:
                previous = _STATES.pop(id(self.twitch), None)
                _STATES[id(self.twitch)] = state
            if previous is not None:
                previous.stop()
            state.start()
            try:
                return run(self, *args, **kwargs)
            finally:
                state.stop()
                with _STATE_LOCK:
                    _STATES.pop(id(self.twitch), None)

        setattr(run_with_dashboard, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.run = run_with_dashboard
