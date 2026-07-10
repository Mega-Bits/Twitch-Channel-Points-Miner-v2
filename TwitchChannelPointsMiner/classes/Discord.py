from textwrap import dedent

import requests

from TwitchChannelPointsMiner.classes.Settings import Events


class Discord(object):
    __slots__ = ["webhook_api", "events"]

    IS_COMPONENTS_V2 = 1 << 15
    COMPONENT_TEXT_DISPLAY = 10
    COMPONENT_CONTAINER = 17

    EVENT_STYLES = {
        Events.STREAMER_ONLINE: ("🟢", "Streamer online", 0x36B37E),
        Events.STREAMER_OFFLINE: ("⚫", "Streamer offline", 0x6B7280),
        Events.GAIN_FOR_RAID: ("🎭", "Raid points", 0x8B5CF6),
        Events.GAIN_FOR_CLAIM: ("🎁", "Bonus claimed", 0xF59E0B),
        Events.GAIN_FOR_WATCH: ("👀", "Watch points", 0x45C1FF),
        Events.GAIN_FOR_WATCH_STREAK: ("🔥", "Watch streak", 0xF97316),
        Events.BET_WIN: ("✅", "Prediction won", 0x22C55E),
        Events.BET_LOSE: ("❌", "Prediction lost", 0xEF4444),
        Events.BET_REFUND: ("↩️", "Prediction refunded", 0x94A3B8),
        Events.BET_FILTERS: ("🔎", "Prediction filtered", 0x64748B),
        Events.BET_GENERAL: ("🍀", "Prediction", 0x22C55E),
        Events.BET_FAILED: ("⚠️", "Prediction failed", 0xF59E0B),
        Events.BET_START: ("🔧", "Prediction started", 0x3B82F6),
        Events.BONUS_CLAIM: ("🎁", "Bonus claim", 0xF59E0B),
        Events.MOMENT_CLAIM: ("📸", "Moment claimed", 0xA855F7),
        Events.JOIN_RAID: ("🎭", "Joined raid", 0x8B5CF6),
        Events.DROP_CLAIM: ("📦", "Drop claimed", 0x10B981),
        Events.DROP_STATUS: ("📦", "Drop progress", 0x06B6D4),
        Events.CHAT_MENTION: ("💬", "Chat mention", 0x60A5FA),
    }

    def __init__(self, webhook_api: str, events: list):
        self.webhook_api = webhook_api
        self.events = [str(e) for e in events]

    @classmethod
    def __format_message(cls, message: str, event: Events) -> dict:
        message = dedent(message).strip()
        emoji, title, accent_color = cls.EVENT_STYLES.get(
            event, ("🤖", str(event), 0x45C1FF)
        )
        details = message if message else "No details provided."

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
                        {
                            "type": cls.COMPONENT_TEXT_DISPLAY,
                            "content": f"### {emoji} {title}",
                        },
                        {
                            "type": cls.COMPONENT_TEXT_DISPLAY,
                            "content": details,
                        },
                    ],
                }
            ],
        }

    def send(self, message: str, event: Events) -> None:
        if str(event) in self.events:
            requests.post(
                url=self.webhook_api,
                params={"with_components": "true"},
                json=self.__format_message(message, event),
            )
