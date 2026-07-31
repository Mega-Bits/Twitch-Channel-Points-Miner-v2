"""Startup and watch-selection notifications for the patched miner."""

import logging
from threading import Lock

from TwitchChannelPointsMiner.classes.Discord import Discord
from TwitchChannelPointsMiner.classes.Settings import Events, Settings
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_watch_notifications_patch"
_STATE_LOCK = Lock()
_WATCHING = {}
_STARTUP_SENT = set()
_NOTIFICATION_EVENTS = (
    Events.STARTUP_STATUS,
    Events.START_WATCHING,
    Events.STOP_WATCHING,
)


def _name(value):
    return getattr(value, "name", str(value))


def _enabled(value):
    return "on" if value is True else "off"


def _channel(username):
    return f"[{username}](https://twitch.tv/{username})"


def _enable_discord_events():
    logger_settings = getattr(Settings, "logger", None)
    discord = getattr(logger_settings, "discord", None) if logger_settings else None
    if discord is None or not hasattr(discord, "events"):
        return
    for event in _NOTIFICATION_EVENTS:
        event_name = str(event)
        if event_name not in discord.events:
            discord.events.append(event_name)


def _streamer_settings_line(streamer):
    settings = streamer.settings
    status = "online" if streamer.is_online else "offline"
    source = getattr(streamer, "source", "list")
    return (
        f"- {_channel(streamer.username)} · `{status}` · `{source}` · "
        f"drops `{_enabled(settings.claim_drops)}` · "
        f"streak `{_enabled(settings.watch_streak)}` · "
        f"predictions `{_enabled(settings.make_predictions)}` · "
        f"raid `{_enabled(settings.follow_raid)}` · "
        f"moments `{_enabled(settings.claim_moments)}` · "
        f"goals `{_enabled(settings.community_goals)}` · "
        f"chat `{_name(settings.chat)}`"
    )


def _startup_messages(streamers, priority, limit=3600):
    priority_text = " > ".join(_name(item) for item in priority) or "none"
    header = (
        f"**Priority:** `{priority_text}`\n"
        f"**Streamers:** `{len(streamers)}`\n"
    )
    messages = []
    current = header
    for streamer in streamers:
        line = _streamer_settings_line(streamer)
        candidate = f"{current}{line}\n"
        if len(candidate) > limit and current != header:
            messages.append(current.rstrip())
            current = "**Streamers continued:**\n"
        current += f"{line}\n"
    messages.append(current.rstrip())
    return messages


def _drop_details(twitch, streamer):
    campaigns = list(getattr(streamer.stream, "campaigns", []) or [])
    locked_id = getattr(twitch, "locked_drop_campaign_id", None)
    if locked_id is not None:
        campaigns.sort(key=lambda campaign: campaign.id != locked_id)

    for campaign in campaigns:
        drops = [drop for drop in campaign.drops if not getattr(drop, "is_claimed", False)]
        if not drops:
            continue
        drop = drops[0]
        current = getattr(drop, "current_minutes_watched", 0)
        required = getattr(drop, "minutes_required", 0)
        percent = getattr(drop, "percentage_progress", 0)
        return (
            "Drop",
            f"**Campaign:** {campaign.name}\n"
            f"**Drop:** {drop.name}\n"
            f"**Progress:** `{current}/{required}` · `{percent}%`",
        )
    return None, None


def _watch_reason(twitch, streamer):
    reason, details = _drop_details(twitch, streamer)
    if reason:
        return reason, details
    if (
        streamer.settings.watch_streak is True
        and streamer.stream.watch_streak_missing is True
        and streamer.stream.minute_watched < 7
    ):
        return "Watch streak", None
    return "Priority", None


def _start_message(twitch, streamer):
    reason, drop_details = _watch_reason(twitch, streamer)
    lines = [
        f"**Channel:** {_channel(streamer.username)}",
        f"**Reason:** `{reason}`",
    ]
    if drop_details:
        lines.append(drop_details)
    return "\n".join(lines), reason


def _stop_message(username, reason):
    return (
        f"**Channel:** {_channel(username)}\n"
        f"**Previous reason:** `{reason}`"
    )


def _emit_startup_once(twitch, streamers, priority):
    key = id(twitch)
    with _STATE_LOCK:
        if key in _STARTUP_SENT:
            return
        _STARTUP_SENT.add(key)
    _enable_discord_events()
    for message in _startup_messages(streamers, priority):
        logger.info(message, extra={"event": Events.STARTUP_STATUS})


def _sync_watch_notifications(twitch, streamers, selected_indexes):
    key = id(twitch)
    current = {}
    for index in selected_indexes:
        streamer = streamers[index]
        message, reason = _start_message(twitch, streamer)
        current[streamer.username] = {"message": message, "reason": reason}

    with _STATE_LOCK:
        previous = _WATCHING.get(key, {})
        _WATCHING[key] = current

    for username, previous_state in previous.items():
        if username not in current:
            logger.info(
                _stop_message(username, previous_state["reason"]),
                extra={"event": Events.STOP_WATCHING},
            )
    for username, current_state in current.items():
        if username not in previous:
            logger.info(
                current_state["message"],
                extra={"event": Events.START_WATCHING},
            )


def _stop_all(twitch):
    key = id(twitch)
    with _STATE_LOCK:
        previous = _WATCHING.pop(key, {})
        _STARTUP_SENT.discard(key)
    for username, previous_state in previous.items():
        logger.info(
            _stop_message(username, previous_state["reason"]),
            extra={"event": Events.STOP_WATCHING},
        )


def apply_patch():
    """Install notification wrappers once."""
    Discord.EVENT_STYLES.update(
        {
            Events.STARTUP_STATUS: ("🚀", "Miner started", 0x3B82F6),
            Events.START_WATCHING: ("▶️", "Start watching stream", 0x22C55E),
            Events.STOP_WATCHING: ("⏹️", "Stop watching stream", 0x64748B),
        }
    )

    select = getattr(Twitch, "_select_streamers_to_watch", None)
    if select is not None and not getattr(select, _PATCH_MARKER, False):
        def select_with_notifications(self, streamers, priority, max_watch_amount=2):
            selected = select(self, streamers, priority, max_watch_amount)
            _sync_watch_notifications(self, streamers, selected)
            return selected

        setattr(select_with_notifications, _PATCH_MARKER, True)
        Twitch._select_streamers_to_watch = select_with_notifications

    send = Twitch.send_minute_watched_events
    if not getattr(send, _PATCH_MARKER, False):
        def send_with_notifications(self, streamers, priority, chunk_size=3):
            _emit_startup_once(self, streamers, priority)
            try:
                return send(self, streamers, priority, chunk_size)
            finally:
                _stop_all(self)

        setattr(send_with_notifications, _PATCH_MARKER, True)
        Twitch.send_minute_watched_events = send_with_notifications
