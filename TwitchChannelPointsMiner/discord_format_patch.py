"""Compact Markdown formatting for Discord component notifications."""

import re
from textwrap import dedent

from TwitchChannelPointsMiner.classes.Discord import Discord
from TwitchChannelPointsMiner.classes.Settings import Events

_PATCH_MARKER = "_compact_markdown_notifications"

_EMOJI = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\u200d\ufe0f"
    "]+",
    re.UNICODE,
)
_STREAMER = re.compile(
    r"Streamer\(username=(?P<username>[^,\)]+),\s*"
    r"(?:source=[^,\)]+,\s*)?"
    r"channel_id=(?P<channel_id>[^,\)]+),\s*"
    r"channel_points=(?P<points>[^\)]+)\)"
)
_DROP = re.compile(
    r"Drop\(id=.*?,\s*name=(?P<name>.*?),\s*"
    r"benefit=(?P<benefit>.*?),\s*minutes_required=(?P<required>[\d.]+),\s*"
    r"has_preconditions_met=.*?,\s*current_minutes_watched=(?P<current>[\d.]+),\s*"
    r"percentage_progress=(?P<percent>.*?),\s*drop_instance_id=.*?,\s*"
    r"is_claimed=.*?\)",
    re.DOTALL,
)
_CAMPAIGN = re.compile(r"Campaign\(id=.*?,\s*name=(?P<name>.*?),\s*game=", re.DOTALL)


def _channel(username):
    username = username.strip()
    return f"[{username}](https://twitch.tv/{username})"


def _streamer(message):
    match = _STREAMER.search(message)
    return match.groupdict() if match else None


def _status(message):
    values = _streamer(message)
    if not values:
        return None
    return f"**Channel:** {_channel(values['username'])} · **Points:** `{values['points'].strip()}`"


def _points(message):
    values = _streamer(message)
    amount = re.search(r"(?P<amount>[+-]\s*[\d.,]+)", message)
    if not values or not amount:
        return None
    gain = amount.group("amount").replace(" ", "")
    return f"**Channel:** {_channel(values['username'])} · **Points:** `{gain}`"


def _raid(message):
    values = _streamer(message)
    target = re.search(r"\bto\s+(?P<target>[A-Za-z0-9_]+)!?\s*$", message)
    if not values or not target:
        return None
    return f"**From:** {_channel(values['username'])} → **To:** {_channel(target.group('target'))}"


def _drop_claim(message):
    match = _DROP.search(message)
    if not match:
        return None
    values = match.groupdict()
    return "\n".join(
        (
            f"**Drop:** {values['name'].strip()}",
            f"**Reward:** {values['benefit'].strip()}",
            f"**Progress:** `{values['current'].strip()}/{values['required'].strip()}` · `{values['percent'].strip()}`",
        )
    )


def _drop_status(message):
    streamer = _streamer(message)
    drop = _DROP.search(message)
    if not streamer or not drop:
        return None
    values = drop.groupdict()
    lines = [f"**Channel:** {_channel(streamer['username'])}"]
    campaign = _CAMPAIGN.search(message)
    if campaign:
        lines.append(f"**Campaign:** {campaign.group('name').strip()}")
    lines.append(
        f"**Drop:** {values['name'].strip()} · `{values['current'].strip()}/{values['required'].strip()}` · `{values['percent'].strip()}`"
    )
    return "\n".join(lines)


def _generic(message):
    message = _EMOJI.sub("", dedent(message)).strip()

    def replace_streamer(match):
        values = match.groupdict()
        return f"{_channel(values['username'])} (`{values['points'].strip()}` points)"

    message = _STREAMER.sub(replace_streamer, message)
    message = re.sub(r"[ \t]+", " ", message)
    message = re.sub(r"\n{3,}", "\n\n", message)
    return message.strip(" -\n") or "No details provided."


def _details(message, event):
    formatter = {
        Events.STREAMER_ONLINE: _status,
        Events.STREAMER_OFFLINE: _status,
        Events.GAIN_FOR_RAID: _points,
        Events.GAIN_FOR_CLAIM: _points,
        Events.GAIN_FOR_WATCH: _points,
        Events.GAIN_FOR_WATCH_STREAK: _points,
        Events.JOIN_RAID: _raid,
        Events.DROP_CLAIM: _drop_claim,
        Events.DROP_STATUS: _drop_status,
    }.get(event)
    formatted = formatter(message) if formatter else None
    return formatted or _generic(message)


def _format_message(cls, message, event):
    emoji, title, accent_color = cls.EVENT_STYLES.get(
        event, ("🤖", str(event), 0x45C1FF)
    )
    return {
        "username": "Twitch Channel Points Miner",
        "avatar_url": "https://i.imgur.com/X9fEkhT.png",
        "allowed_mentions": {"parse": []},
        "flags": cls.IS_COMPONENTS_V2,
        "components": [
            {
                "type": cls.COMPONENT_CONTAINER,
                "accent_color": accent_color,
                "components": [
                    {"type": cls.COMPONENT_TEXT_DISPLAY, "content": f"### {emoji} {title}"},
                    {"type": cls.COMPONENT_TEXT_DISPLAY, "content": _details(message, event)},
                ],
            }
        ],
    }


def apply_patch():
    """Install the compact formatter once."""
    current = Discord._Discord__format_message
    if getattr(current, _PATCH_MARKER, False):
        return
    setattr(_format_message, _PATCH_MARKER, True)
    Discord._Discord__format_message = classmethod(_format_message)
