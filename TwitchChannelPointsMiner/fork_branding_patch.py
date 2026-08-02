"""Use the Mega-Bits fork for runtime branding and update checks."""

from __future__ import annotations

import logging

from TwitchChannelPointsMiner import constants, utils

_REPOSITORY_URL = "https://github.com/Mega-Bits/Twitch-Channel-Points-Miner-v2"
_RAW_REPOSITORY_URL = (
    "https://raw.githubusercontent.com/Mega-Bits/"
    "Twitch-Channel-Points-Miner-v2/master"
)
_OLD_REPOSITORY_URL = "https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2"
_PATCH_MARKER = "_mega_bits_fork_branding_patch"


class _ForkBrandingFilter(logging.Filter):
    """Rewrite only the legacy runtime identity messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True

        rewritten = message.replace("(fork by rdavydov)", "(Mega-Bits fork)")
        rewritten = rewritten.replace(_OLD_REPOSITORY_URL, _REPOSITORY_URL)
        if rewritten != message:
            record.msg = rewritten
            record.args = ()
        return True


def apply_patch() -> None:
    """Point update checks at this fork and install its runtime branding."""
    constants.BRANCH = "master"
    constants.GITHUB_url = _RAW_REPOSITORY_URL
    # utils imports GITHUB_url by value, so update its module global as well.
    utils.GITHUB_url = _RAW_REPOSITORY_URL

    logger = logging.getLogger("TwitchChannelPointsMiner.TwitchChannelPointsMiner")
    if any(getattr(item, _PATCH_MARKER, False) for item in logger.filters):
        return

    branding_filter = _ForkBrandingFilter()
    setattr(branding_filter, _PATCH_MARKER, True)
    logger.addFilter(branding_filter)
