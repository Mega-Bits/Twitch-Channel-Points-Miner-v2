"""Use the Mega-Bits fork for runtime branding and update checks."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

from TwitchChannelPointsMiner import constants, utils

_REPOSITORY_URL = "https://github.com/Mega-Bits/Twitch-Channel-Points-Miner-v2"
_RAW_REPOSITORY_URL = (
    "https://raw.githubusercontent.com/Mega-Bits/"
    "Twitch-Channel-Points-Miner-v2/master"
)
_OLD_REPOSITORY_URL = "https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2"
_VERSION_FILE = Path(__file__).with_name("VERSION")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_PATCH_MARKER = "_mega_bits_fork_branding_patch"


def _valid_version(value: str) -> str:
    value = str(value or "").strip()
    return value if _VERSION_PATTERN.fullmatch(value) else "0.0.0"


def _check_versions() -> tuple[str, str]:
    """Return local and current Mega-Bits master versions."""
    try:
        current_version = _valid_version(_VERSION_FILE.read_text(encoding="utf-8"))
    except OSError:
        current_version = "0.0.0"

    try:
        response = requests.get(
            f"{_RAW_REPOSITORY_URL}/TwitchChannelPointsMiner/VERSION",
            timeout=10,
        )
        response.raise_for_status()
        github_version = _valid_version(response.text)
    except requests.RequestException:
        github_version = "0.0.0"

    return current_version, github_version


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
    # utils imports GITHUB_url and check_versions by value.
    utils.GITHUB_url = _RAW_REPOSITORY_URL
    utils.check_versions = _check_versions

    logger = logging.getLogger("TwitchChannelPointsMiner.TwitchChannelPointsMiner")
    if any(getattr(item, _PATCH_MARKER, False) for item in logger.filters):
        return

    branding_filter = _ForkBrandingFilter()
    setattr(branding_filter, _PATCH_MARKER, True)
    logger.addFilter(branding_filter)
