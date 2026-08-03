"""Rate-limit-safe Discord dashboard updates and richer status details."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import unicodedata
from typing import Any

import requests

from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner.classes.Settings import Events

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_status_dashboard_enhancements_patch"
_MIN_PUBLISH_INTERVAL_SECONDS = 15.0
_BASE_RETRY_SECONDS = 15.0
_MAX_RETRY_SECONDS = 300.0
_MAX_RATE_LIMIT_RETRY_SECONDS = 900.0

_ORIGINAL_INIT = dashboard.DashboardState.__init__
_ORIGINAL_LOAD = dashboard.DashboardState._load
_ORIGINAL_SLOT_SNAPSHOT = dashboard._slot_snapshot
_ORIGINAL_CAMPAIGN_SNAPSHOT = dashboard._campaign_snapshot
_ORIGINAL_SNAPSHOT = dashboard.DashboardState.snapshot

_POINTS_EVENT_NAMES = {str(event) for event in dashboard._POINTS_EVENTS}
_IGNORED_NON_POINTS_EVENTS = {Events.STARTUP_STATUS}


def _normalize(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _configured_drop_games(twitch: Any) -> set[str]:
    return {
        _normalize(game)
        for game in drop_games_patch.get_drop_games(twitch)
        if _normalize(game)
    }


def _explicit_drop_game(twitch: Any, game_name: Any) -> bool:
    return _normalize(game_name) in _configured_drop_games(twitch)


def _points_value(streamer: Any) -> int | None:
    value = getattr(streamer, "channel_points", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _format_points(value: Any) -> str:
    try:
        return f"{int(value):,} pts"
    except (TypeError, ValueError, OverflowError):
        return "points pending"


def _explicit_label(value: bool) -> str:
    return "yes" if value else "no"


def _enhanced_slot_snapshot(twitch: Any, streamer: Any) -> dict[str, Any]:
    slot = _ORIGINAL_SLOT_SNAPSHOT(twitch, streamer)
    slot["points"] = _points_value(streamer)
    campaign = slot.get("campaign")
    if isinstance(campaign, dict):
        explicit = _explicit_drop_game(twitch, campaign.get("game"))
        campaign["explicit_drop_game"] = explicit
        drop = campaign.get("drop")
        if isinstance(drop, dict):
            drop["game"] = campaign.get("game") or "Unknown game"
            drop["explicit_drop_game"] = explicit
    return slot


def _enhanced_campaign_snapshot(
    twitch: Any,
    campaign: Any,
    streamers: list[Any],
    locked_id: Any,
) -> dict[str, Any]:
    snapshot = _ORIGINAL_CAMPAIGN_SNAPSHOT(campaign, streamers, locked_id)
    explicit = _explicit_drop_game(twitch, snapshot.get("game"))
    snapshot["explicit_drop_game"] = explicit
    for drop in snapshot.get("drops", []):
        if isinstance(drop, dict):
            drop["game"] = snapshot.get("game") or "Unknown game"
            drop["explicit_drop_game"] = explicit
    return snapshot


def _is_points_item(item: Any) -> bool:
    return isinstance(item, dict) and str(item.get("event", "")) in _POINTS_EVENT_NAMES


def _load_with_non_points_event(self: Any) -> None:
    _ORIGINAL_LOAD(self)
    self.last_non_points_event = None
    try:
        data = json.loads(self._state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}

    candidate = data.get("last_non_points_event")
    if not isinstance(candidate, dict):
        candidate = data.get("last_event")
    if isinstance(candidate, dict) and not _is_points_item(candidate):
        self.last_non_points_event = candidate

    # Keep the old attribute as a compatibility alias for existing consumers and
    # for the clock-correction patch, which already corrects this snapshot key.
    self.last_event = self.last_non_points_event


def _save_with_non_points_event(self: Any, webhook_api: str | None = None) -> None:
    fingerprint = (
        dashboard._webhook_fingerprint(webhook_api)
        if webhook_api
        else self._stored_webhook_fingerprint
    )
    data = {
        "message_id": self.message_id,
        "webhook_fingerprint": fingerprint,
        "recent_claims": list(self.recent_claims),
        "last_points_event": self.last_points_event,
        "last_non_points_event": self.last_non_points_event,
        # Retain the old field during migration. It now always means non-points.
        "last_event": self.last_non_points_event,
    }
    temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._state_path)
        self._stored_webhook_fingerprint = fingerprint
    except OSError:
        logger.warning("Unable to persist Discord dashboard state", exc_info=True)


def _init_with_rate_limit_state(
    self: Any,
    twitch: Any,
    streamers: list[Any],
    priority: list[Any],
) -> None:
    _ORIGINAL_INIT(self, twitch, streamers, priority)
    self._last_payload_hash: str | None = None
    self._next_publish_at = 0.0
    self._publish_backoff = 0.0


def _update_inventory_with_details(
    self: Any,
    streamers: list[Any],
    campaigns: list[Any],
) -> None:
    locked_id = getattr(self.twitch, "locked_drop_campaign_id", None)
    snapshots = [
        _enhanced_campaign_snapshot(self.twitch, campaign, streamers, locked_id)
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


def _record_event_split(
    self: Any,
    event: Events,
    message: str,
    created: float,
) -> None:
    formatted = dashboard.discord_format_patch._details(message, event)
    item = {
        "event": str(event),
        "message": dashboard._trim(formatted, 900),
        "timestamp": int(created),
    }
    with self._lock:
        if event == Events.DROP_CLAIM:
            self.recent_claims.appendleft(item)
        if event in dashboard._POINTS_EVENTS:
            self.last_points_event = item
        elif event not in _IGNORED_NON_POINTS_EVENTS:
            self.last_non_points_event = item
            self.last_event = item
    self._save()
    self.mark_dirty()


def _snapshot_with_non_points_event(self: Any) -> dict[str, Any]:
    snapshot = _ORIGINAL_SNAPSHOT(self)
    snapshot["last_non_points_event"] = self.last_non_points_event
    # The existing clock patch corrects last_event. The dashboard renderer uses
    # this compatibility key so local clock skew is handled consistently.
    snapshot["last_event"] = self.last_non_points_event
    return snapshot


def _render_overview_with_points(self: Any, snapshot: dict[str, Any]) -> str:
    status = "running" if snapshot["running"] else "stopped"
    lines = [
        f"**Status:** `{status}`",
        f"**Started:** {dashboard._discord_timestamp(snapshot['started_at'], 'F')} · {dashboard._discord_timestamp(snapshot['started_at'])}",
        f"**Last inventory sync:** {dashboard._discord_timestamp(snapshot['last_inventory_sync'])}",
        f"**Priority:** `{' > '.join(snapshot['priority']) or 'none'}`",
        f"**Watch slots:** `{len(snapshot['watch_slots'])}/2`",
        "",
        "**Currently watching**",
    ]
    if not snapshot["watch_slots"]:
        lines.append("No stream selected.")
    for position, slot in enumerate(snapshot["watch_slots"], start=1):
        lines.append(
            f"{position}. {dashboard._channel(slot['username'])} · `{slot['reason']}` · "
            f"`{slot['source']}` · `{_format_points(slot.get('points'))}`"
        )
        campaign = slot.get("campaign")
        if campaign:
            drop = campaign["drop"]
            lines.append(
                f"   Game: `{campaign['game']}` · Drop: {drop['name']} · "
                f"`{drop['current']}/{drop['required']} min` · `{drop['percent']}%` · "
                f"explicit game farming: `{_explicit_label(bool(campaign.get('explicit_drop_game')))}`"
            )
    return "\n".join(lines)


def _render_campaigns_with_game_details(
    self: Any,
    snapshot: dict[str, Any],
) -> str:
    lines = ["**Drops and queue**"]
    if not snapshot["campaigns"]:
        lines.append("No open Drop campaign is currently tracked.")
        return "\n".join(lines)

    for campaign in snapshot["campaigns"][:6]:
        marker = "active" if campaign["locked"] else "queued"
        explicit = bool(campaign.get("explicit_drop_game"))
        lines.append(
            f"**{campaign['name']}** · game: `{campaign['game']}` · `{marker}` · "
            f"explicit game farming: `{_explicit_label(explicit)}` · "
            f"ends {dashboard._discord_timestamp(campaign['end_at'])}"
        )
        for drop in campaign["drops"][:3]:
            lines.append(
                f"- `{drop.get('game') or campaign['game']}` · {drop['name']} · "
                f"`{drop['current']}/{drop['required']} min` · `{drop['percent']}%` · "
                f"explicit: `{_explicit_label(bool(drop.get('explicit_drop_game')))}`"
            )
        if len(campaign["drops"]) > 3:
            lines.append(f"- `+{len(campaign['drops']) - 3} more Drops`")
        candidates = campaign["eligible"][:4]
        if candidates:
            rendered = ", ".join(
                f"{dashboard._channel(item['username'])} (`{item['source']}`)"
                for item in candidates
            )
            lines.append(f"Candidates: {rendered}")
        else:
            lines.append("Candidates: `searching`")
    if len(snapshot["campaigns"]) > 6:
        lines.append(f"`+{len(snapshot['campaigns']) - 6} more campaigns`")
    return "\n".join(lines)


def _render_activity_split(self: Any, snapshot: dict[str, Any]) -> str:
    lines = ["**Recent activity**"]
    points = snapshot.get("last_points_event")
    if points:
        lines.append(
            f"**Last points event:** {points['message']} · "
            f"{dashboard._discord_timestamp(points['timestamp'])}"
        )
    else:
        lines.append("**Last points event:** `none`")

    # last_event is retained as the clock-corrected compatibility alias.
    event = snapshot.get("last_event")
    if event:
        lines.append(
            f"**Last non-points event:** `{event['event']}` · {event['message']} · "
            f"{dashboard._discord_timestamp(event['timestamp'])}"
        )
    else:
        lines.append("**Last non-points event:** `none`")

    lines.append("**Recent Drop claims**")
    if not snapshot["recent_claims"]:
        lines.append("No Drop has been claimed in the retained dashboard history.")
    for claim in snapshot["recent_claims"]:
        compact = claim["message"].replace("\n", " · ")
        lines.append(
            f"- {compact} · {dashboard._discord_timestamp(claim['timestamp'])}"
        )
    return "\n".join(lines)


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _retry_after_seconds(response: requests.Response) -> float:
    values: list[Any] = [response.headers.get("Retry-After")]
    try:
        body = response.json()
    except (ValueError, TypeError):
        body = None
    if isinstance(body, dict):
        values.insert(0, body.get("retry_after"))

    for value in values:
        try:
            seconds = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if seconds > 1000:
            seconds /= 1000
        return max(1.0, min(seconds, _MAX_RATE_LIMIT_RETRY_SECONDS))
    return _BASE_RETRY_SECONDS


def _schedule_retry(self: Any, delay: float) -> None:
    self._next_publish_at = max(
        self._next_publish_at,
        time.monotonic() + max(1.0, delay),
    )
    self._dirty.set()


def _handle_rate_limit(self: Any, response: requests.Response) -> bool:
    retry_after = _retry_after_seconds(response) + 0.5
    _schedule_retry(self, retry_after)
    logger.warning(
        "Discord dashboard rate limited; retrying in %.1f seconds",
        retry_after,
    )
    return False


def _handle_publish_failure(self: Any, exc: Exception) -> bool:
    self._publish_backoff = (
        _BASE_RETRY_SECONDS
        if self._publish_backoff <= 0
        else min(self._publish_backoff * 2, _MAX_RETRY_SECONDS)
    )
    _schedule_retry(self, self._publish_backoff)
    logger.warning(
        "Unable to update Discord status dashboard; retrying in %.0f seconds: %s",
        self._publish_backoff,
        exc,
    )
    return False


def _publish_rate_limit_safe(self: Any) -> bool:
    discord = self._discord()
    if discord is None:
        return True

    now = time.monotonic()
    earliest = max(
        self._next_publish_at,
        self._last_publish + _MIN_PUBLISH_INTERVAL_SECONDS,
    )
    if now < earliest:
        _schedule_retry(self, earliest - now)
        return False

    webhook_api = discord.webhook_api
    fingerprint = dashboard._webhook_fingerprint(webhook_api)
    if self._stored_webhook_fingerprint not in (None, fingerprint):
        self.message_id = None
        self._last_payload_hash = None

    payload = self._payload()
    current_hash = _payload_hash(payload)
    if self.message_id and current_hash == self._last_payload_hash:
        self._next_publish_at = 0.0
        self._publish_backoff = 0.0
        return True

    try:
        if self.message_id:
            response = requests.patch(
                dashboard._webhook_message_url(webhook_api, self.message_id),
                params={"with_components": "true"},
                json=payload,
                timeout=20,
            )
            if response.status_code == 404:
                self.message_id = None
            elif response.status_code == 429:
                return _handle_rate_limit(self, response)
            elif response.status_code >= 500:
                return _handle_publish_failure(
                    self,
                    requests.HTTPError(
                        f"Discord returned HTTP {response.status_code}",
                        response=response,
                    ),
                )
            elif response.ok:
                self._last_publish = time.monotonic()
                self._next_publish_at = 0.0
                self._publish_backoff = 0.0
                self._last_payload_hash = current_hash
                self._save(webhook_api)
                return True
            else:
                response.raise_for_status()

        response = requests.post(
            webhook_api,
            params={"wait": "true", "with_components": "true"},
            json=payload,
            timeout=20,
        )
        if response.status_code == 429:
            return _handle_rate_limit(self, response)
        if response.status_code >= 500:
            return _handle_publish_failure(
                self,
                requests.HTTPError(
                    f"Discord returned HTTP {response.status_code}",
                    response=response,
                ),
            )
        response.raise_for_status()
        self.message_id = str(response.json()["id"])
        self._last_publish = time.monotonic()
        self._next_publish_at = 0.0
        self._publish_backoff = 0.0
        self._last_payload_hash = current_hash
        self._save(webhook_api)
        return True
    except (requests.RequestException, ValueError, KeyError) as exc:
        return _handle_publish_failure(self, exc)


def _worker_coalesced(self: Any) -> None:
    while not self._stop.is_set():
        self._dirty.wait()
        if self._stop.is_set():
            break
        self._dirty.clear()
        if not self.streamers and self.stopped_at is None:
            continue

        while not self._stop.is_set():
            earliest = max(
                self._next_publish_at,
                self._last_publish + _MIN_PUBLISH_INTERVAL_SECONDS,
            )
            delay = max(0.0, earliest - time.monotonic())
            if delay and self._stop.wait(delay):
                break
            if self._publish():
                break

    # Never bypass a Discord cooldown during shutdown. A final update is made
    # only when it is already safe; otherwise the last successful dashboard
    # remains visible instead of generating another rate-limited request.
    if self.stopped_at is not None:
        earliest = max(
            self._next_publish_at,
            self._last_publish + _MIN_PUBLISH_INTERVAL_SECONDS,
        )
        if time.monotonic() >= earliest:
            self._publish()


def apply_patch() -> None:
    """Install dashboard detail and request-throttling improvements."""
    state_class = dashboard.DashboardState
    if getattr(state_class, _PATCH_MARKER, False):
        return

    dashboard._slot_snapshot = _enhanced_slot_snapshot
    state_class._load = _load_with_non_points_event
    state_class._save = _save_with_non_points_event
    state_class.__init__ = _init_with_rate_limit_state
    state_class.update_inventory = _update_inventory_with_details
    state_class.record_event = _record_event_split
    state_class.snapshot = _snapshot_with_non_points_event
    state_class._render_overview = _render_overview_with_points
    state_class._render_campaigns = _render_campaigns_with_game_details
    state_class._render_activity = _render_activity_split
    state_class._publish = _publish_rate_limit_safe
    state_class._worker = _worker_coalesced
    setattr(state_class, _PATCH_MARKER, True)
