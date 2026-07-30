# -*- coding: utf-8 -*-
__version__ = "2.0.5"

from .channel_points_context_patch import apply_patch as _apply_channel_points_context_patch

_apply_channel_points_context_patch()
del _apply_channel_points_context_patch

from .TwitchChannelPointsMiner import TwitchChannelPointsMiner

__all__ = [
    "TwitchChannelPointsMiner",
]
