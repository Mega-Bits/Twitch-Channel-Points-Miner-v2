# -*- coding: utf-8 -*-
"""Configuration example for the Mega-Bits fork."""

import logging

from colorama import Fore

from TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Discord import Discord
from TwitchChannelPointsMiner.classes.Gotify import Gotify
from TwitchChannelPointsMiner.classes.Matrix import Matrix
from TwitchChannelPointsMiner.classes.Pushover import Pushover
from TwitchChannelPointsMiner.classes.Settings import Events, FollowersOrder, Priority
from TwitchChannelPointsMiner.classes.Telegram import Telegram
from TwitchChannelPointsMiner.classes.Webhook import Webhook
from TwitchChannelPointsMiner.classes.entities.Bet import (
    BetSettings,
    Condition,
    DelayMode,
    FilterCondition,
    OutcomeKeys,
    Strategy,
)
from TwitchChannelPointsMiner.classes.entities.Streamer import (
    Streamer,
    StreamerSettings,
)
from TwitchChannelPointsMiner.logger import ColorPalette, LoggerSettings


# Events used by the normal notification webhook. The persistent dashboard does
# not need an event entry because it uses dashboard_webhook_api directly.
DISCORD_EVENTS = [
    Events.START_WATCHING,
    Events.STOP_WATCHING,
    Events.STREAMER_ONLINE,
    Events.STREAMER_OFFLINE,
    Events.GAIN_FOR_WATCH_STREAK,
    Events.DROP_CLAIM,
    Events.DROP_STATUS,
    Events.BET_WIN,
    Events.BET_LOSE,
    Events.CHAT_MENTION,
]


twitch_miner = TwitchChannelPointsMiner(
    username="your-twitch-username",
    password="write-your-secure-password",  # Omit to enter it interactively.
    claim_drops_startup=False,
    priority=[
        Priority.STREAK,
        Priority.DROPS,
        Priority.ORDER,
    ],
    enable_analytics=False,
    disable_ssl_cert_verification=False,
    disable_at_in_nickname=False,
    logger_settings=LoggerSettings(
        save=True,
        console_level=logging.INFO,
        console_username=False,
        auto_clear=True,
        time_zone="Europe/Berlin",
        file_level=logging.DEBUG,
        emoji=True,
        less=False,
        colored=True,
        color_palette=ColorPalette(
            STREAMER_online="GREEN",
            streamer_offline="red",
            BET_wiN=Fore.MAGENTA,
        ),
        discord=Discord(
            # Normal event messages are posted here.
            webhook_api="https://discord.com/api/webhooks/EVENTS/WEBHOOK",
            events=DISCORD_EVENTS,
            # The single persistent status dashboard is posted and edited here.
            # Leave empty to disable the Discord dashboard completely.
            dashboard_webhook_api=(
                "https://discord.com/api/webhooks/DASHBOARD/WEBHOOK"
            ),
        ),
        telegram=Telegram(
            chat_id=123456789,
            token="123456789:replace-me",
            events=[
                Events.STREAMER_ONLINE,
                Events.STREAMER_OFFLINE,
                Events.DROP_CLAIM,
            ],
            disable_notification=True,
        ),
        webhook=Webhook(
            endpoint="https://example.com/webhook",
            method="POST",
            events=[Events.DROP_CLAIM, Events.START_WATCHING, Events.STOP_WATCHING],
        ),
        matrix=Matrix(
            username="twitch_miner",
            password="replace-me",
            homeserver="matrix.org",
            room_id="replace-me",
            events=[Events.STREAMER_ONLINE, Events.STREAMER_OFFLINE],
        ),
        pushover=Pushover(
            userkey="YOUR-ACCOUNT-TOKEN",
            token="YOUR-APPLICATION-TOKEN",
            priority=0,
            sound="pushover",
            events=[Events.CHAT_MENTION, Events.DROP_CLAIM],
        ),
        gotify=Gotify(
            endpoint="https://example.com/message?token=TOKEN",
            priority=8,
            events=[Events.DROP_CLAIM, Events.BET_LOSE],
        ),
    ),
    streamer_settings=StreamerSettings(
        make_predictions=True,
        follow_raid=True,
        claim_drops=True,
        claim_moments=True,
        watch_streak=True,
        community_goals=False,
        chat=ChatPresence.ONLINE,
        bet=BetSettings(
            strategy=Strategy.SMART,
            percentage=5,
            percentage_gap=20,
            max_points=50000,
            stealth_mode=True,
            delay_mode=DelayMode.FROM_END,
            delay=6,
            minimum_points=20000,
            filter_condition=FilterCondition(
                by=OutcomeKeys.TOTAL_USERS,
                where=Condition.LTE,
                value=800,
            ),
        ),
    ),
)


# Streamer-specific settings override the global streamer_settings above.
streamers = [
    Streamer(
        "otzdarva",
        settings=StreamerSettings(
            claim_drops=True,
            watch_streak=True,
            make_predictions=False,
        ),
    ),
    Streamer(
        "deadbydaylight",
        settings=StreamerSettings(
            claim_drops=True,
            watch_streak=True,
            make_predictions=False,
        ),
    ),
    "another-streamer",
]


twitch_miner.mine(
    streamers=streamers,
    followers=False,
    followers_order=FollowersOrder.ASC,
    # Campaigns for these games may use eligible streamers from the main list.
    # The Twitch game directory is queried only when no configured streamer is
    # eligible. Values are matched against the campaign game name.
    drop_games=[
        "Dead by Daylight",
        "Overwatch 2",
    ],
    # Maximum Drops-enabled directory candidates requested per game (1-30).
    drop_game_limit=10,
)
