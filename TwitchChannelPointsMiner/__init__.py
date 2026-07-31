# -*- coding: utf-8 -*-
__version__ = "2.0.5"

from .channel_points_context_patch import apply_patch as _apply_channel_points_context_patch
from .discord_format_patch import apply_patch as _apply_discord_format_patch
from .playback_access_token_patch import apply_patch as _apply_playback_access_token_patch
from .watch_notifications_patch import apply_patch as _apply_watch_notifications_patch

_apply_channel_points_context_patch()
_apply_discord_format_patch()
_apply_playback_access_token_patch()
_apply_watch_notifications_patch()
del _apply_channel_points_context_patch
del _apply_discord_format_patch
del _apply_playback_access_token_patch
del _apply_watch_notifications_patch

from .TwitchChannelPointsMiner import TwitchChannelPointsMiner

__all__ = [
    "TwitchChannelPointsMiner",
]
