"""Attach the campaign game to Drop claim events and dashboard history."""

from __future__ import annotations

import copy
import re
import threading
from typing import Any

from TwitchChannelPointsMiner import discord_format_patch
from TwitchChannelPointsMiner.classes.Settings import Events
from TwitchChannelPointsMiner.classes.Twitch import Twitch, logger as twitch_logger
from TwitchChannelPointsMiner.classes.entities.Campaign import Campaign
from TwitchChannelPointsMiner.constants import GQLOperations

_PATCH_MARKER = "_drop_claim_game_patch"
_LOCK = threading.RLock()
_DROP_GAMES: dict[str, str] = {}
_GAME_SUFFIX = re.compile(r"(?:\s*·\s*)?Game:\s*(?P<game>.+?)\s*$", re.IGNORECASE)


def _game_name(campaign: Any) -> str | None:
    if campaign is None:
        return None
    if isinstance(campaign, dict):
        game = campaign.get("game")
        fallback = campaign.get("gameName") or campaign.get("gameDisplayName")
    else:
        game = getattr(campaign, "game", None)
        fallback = None

    if isinstance(game, dict):
        value = game.get("displayName") or game.get("name")
    elif game:
        value = game
    else:
        value = fallback

    rendered = str(value or "").strip()
    return rendered or None


def _drop_keys(drop: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(drop, dict):
        values = (
            drop.get("id"),
            drop.get("dropInstanceID"),
            drop.get("drop_instance_id"),
        )
        progress = drop.get("self")
        if isinstance(progress, dict):
            values += (progress.get("dropInstanceID"),)
    else:
        values = (
            getattr(drop, "id", None),
            getattr(drop, "drop_instance_id", None),
        )

    for value in values:
        if value not in (None, ""):
            keys.add(str(value))
    return keys


def _remember_drop_game(drop: Any, game: str | None) -> None:
    if not game:
        return
    keys = _drop_keys(drop)
    if not keys:
        return
    with _LOCK:
        for key in keys:
            _DROP_GAMES[key] = game


def _lookup_drop_game(drop: Any) -> str | None:
    with _LOCK:
        for key in _drop_keys(drop):
            game = _DROP_GAMES.get(key)
            if game:
                return game
    return None


def _remember_inventory(inventory: Any) -> None:
    if not isinstance(inventory, dict):
        return
    campaigns = inventory.get("dropCampaignsInProgress") or []
    if not isinstance(campaigns, list):
        return
    for campaign in campaigns:
        game = _game_name(campaign)
        if not game or not isinstance(campaign, dict):
            continue
        for drop in campaign.get("timeBasedDrops") or []:
            _remember_drop_game(drop, game)


def _format_drop_claim_with_game(message: str) -> str | None:
    formatted = _ORIGINAL_DROP_FORMATTER(message)
    if not formatted:
        return None
    match = _GAME_SUFFIX.search(message)
    if not match:
        return formatted
    game = match.group("game").strip()
    if not game:
        return formatted
    return f"{formatted}\n**Game:** {game}"


def _claim_drop_with_game(self: Twitch, drop: Any) -> bool:
    game = _lookup_drop_game(drop)
    suffix = f" · Game: {game}" if game else ""
    twitch_logger.info(
        f"Claim {drop}{suffix}",
        extra={"emoji": ":package:", "event": Events.DROP_CLAIM},
    )

    json_data = copy.deepcopy(GQLOperations.DropsPage_ClaimDropRewards)
    json_data["variables"] = {
        "input": {"dropInstanceID": drop.drop_instance_id}
    }
    response = self.post_gql_request(json_data)
    try:
        if ("claimDropRewards" in response["data"]) and (
            response["data"]["claimDropRewards"] is None
        ):
            return False
        if ("errors" in response["data"]) and response["data"]["errors"] != []:
            return False
        if ("claimDropRewards" in response["data"]) and (
            response["data"]["claimDropRewards"]["status"]
            in ["ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED"]
        ):
            return True
        return False
    except (ValueError, KeyError, TypeError):
        return False


def apply_patch() -> None:
    """Install Drop-to-game tracking and game-aware claim formatting."""
    if getattr(Twitch, _PATCH_MARKER, False):
        return

    original_campaign_init = Campaign.__init__

    def campaign_init_with_game(self: Campaign, data: dict[str, Any]) -> None:
        original_campaign_init(self, data)
        game = _game_name(self)
        for drop in self.drops:
            _remember_drop_game(drop, game)

    original_sync_drops = Campaign.sync_drops

    def sync_drops_with_game(
        self: Campaign,
        drops: list[dict[str, Any]],
        callback: Any,
    ) -> Any:
        game = _game_name(self)

        def callback_with_game(drop: Any) -> Any:
            _remember_drop_game(drop, game)
            return callback(drop)

        return original_sync_drops(self, drops, callback_with_game)

    inventory_name = "_Twitch__get_inventory"
    original_get_inventory = getattr(Twitch, inventory_name)

    def get_inventory_with_games(self: Twitch) -> Any:
        inventory = original_get_inventory(self)
        _remember_inventory(inventory)
        return inventory

    global _ORIGINAL_DROP_FORMATTER
    _ORIGINAL_DROP_FORMATTER = discord_format_patch._drop_claim
    discord_format_patch._drop_claim = _format_drop_claim_with_game
    Campaign.__init__ = campaign_init_with_game
    Campaign.sync_drops = sync_drops_with_game
    setattr(Twitch, inventory_name, get_inventory_with_games)
    Twitch.claim_drop = _claim_drop_with_game
    setattr(Twitch, _PATCH_MARKER, True)
