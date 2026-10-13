"""Attach the campaign game to Drop claim events and dashboard history."""

from __future__ import annotations

import copy
import re
import threading
import time
from typing import Any

from TwitchChannelPointsMiner import discord_format_patch
from TwitchChannelPointsMiner.classes.Settings import Events
from TwitchChannelPointsMiner.classes.Twitch import Twitch, logger as twitch_logger
from TwitchChannelPointsMiner.classes.entities.Campaign import Campaign
from TwitchChannelPointsMiner.constants import GQLOperations

_PATCH_MARKER = "_drop_claim_game_patch"
_LOCK = threading.RLock()
_DROP_GAMES: dict[str, str] = {}
# Claimed status and retry throttling are scoped to a Twitch client and a
# specific Drop instance, never to the game (which may get a new campaign).
_CLAIM_ATTEMPTS: dict[tuple[int, str], dict[str, Any]] = {}
_RETRY_DELAYS = (5 * 60, 15 * 60, 30 * 60, 60 * 60)
_CONFIRMED_HOLD_SECONDS = 6 * 60 * 60
_IN_FLIGHT_SECONDS = 2 * 60
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


def _ready_to_claim(drop: Any) -> bool:
    """Do not attempt claims for started, locked or already claimed rewards."""
    if getattr(drop, "is_claimed", False) is True:
        return False
    if getattr(drop, "has_preconditions_met", None) is not True:
        return False
    if not getattr(drop, "drop_instance_id", None):
        return False
    try:
        return (
            float(getattr(drop, "current_minutes_watched", -1))
            >= float(getattr(drop, "minutes_required", 0))
        )
    except (ValueError, TypeError, OverflowError):
        return False


def _safe_code(value: Any) -> str:
    """Keep logs useful without ever logging a full Twitch API response."""
    text = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "UNKNOWN"))
    return text[:60] or "UNKNOWN"


def _graphql_error_codes(errors: Any) -> str:
    if not isinstance(errors, list):
        errors = [errors]
    codes = []
    for error in errors[:3]:
        if isinstance(error, dict):
            extensions = error.get("extensions")
            extensions = extensions if isinstance(extensions, dict) else {}
            code = extensions.get("code") or error.get("errorCode") or "GRAPHQL_ERROR"
        else:
            code = "GRAPHQL_ERROR"
        codes.append(_safe_code(code))
    return ",".join(codes) or "GRAPHQL_ERROR"


def _claim_response_status(response: Any) -> tuple[str | None, str]:
    if not isinstance(response, dict):
        return None, "no valid Twitch response"
    if response.get("errors"):
        return None, "GraphQL errors=" + _graphql_error_codes(response["errors"])
    data = response.get("data")
    if not isinstance(data, dict):
        return None, "missing GraphQL data"
    if data.get("errors"):
        return None, "GraphQL errors=" + _graphql_error_codes(data["errors"])
    claim = data.get("claimDropRewards")
    if not isinstance(claim, dict):
        return None, "missing claimDropRewards"
    status = claim.get("status")
    status = _safe_code(status) if status else None
    if status in ("ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED"):
        return status, status
    return None, ("Twitch status=" + status) if status else "missing claim status"


def _reserve_claim(client: Any, instance: str) -> tuple[bool, bool, int]:
    """Reserve exactly one request across claim threads; honor previous result."""
    key = (id(client), instance)
    now = time.monotonic()
    with _LOCK:
        state = _CLAIM_ATTEMPTS.get(key)
        if state and now < state["next_attempt"]:
            return False, bool(state["successful"]), int(state["failures"])
        failures = int(state["failures"]) if state else 0
        _CLAIM_ATTEMPTS[key] = {
            "next_attempt": now + _IN_FLIGHT_SECONDS,
            "successful": False,
            "failures": failures,
        }
    return True, False, failures


def _finish_claim(
    client: Any, instance: str, status: str | None, failures: int
) -> int:
    success = status is not None
    retries = 0 if success else failures + 1
    delay = (
        _CONFIRMED_HOLD_SECONDS if success else
        _RETRY_DELAYS[min(retries - 1, len(_RETRY_DELAYS) - 1)]
    )
    with _LOCK:
        _CLAIM_ATTEMPTS[(id(client), instance)] = {
            "next_attempt": time.monotonic() + delay,
            "successful": success,
            "failures": retries,
        }
    return delay


def _claim_drop_with_game(self: Twitch, drop: Any) -> bool:
    if not _ready_to_claim(drop):
        return False

    instance = str(drop.drop_instance_id)
    should_request, previous_success, failures = _reserve_claim(self, instance)
    if not should_request:
        return previous_success

    game = _lookup_drop_game(drop)
    suffix = f" · Game: {game}" if game else ""
    json_data = copy.deepcopy(GQLOperations.DropsPage_ClaimDropRewards)
    json_data["variables"] = {"input": {"dropInstanceID": instance}}

    try:
        response = self.post_gql_request(json_data)
        status, reason = _claim_response_status(response)
    except Exception as exc:
        # Twitch requests can fail for reasons other than HTTP connectivity.
        # Never expose an exception's content (which might contain auth data).
        status, reason = None, "request error=" + type(exc).__name__

    delay = _finish_claim(self, instance, status, failures)
    if status == "ELIGIBLE_FOR_ALL":
        # Emit the Discord DROP_CLAIM event only after Twitch accepts the claim.
        twitch_logger.info(
            f"Claim {drop}{suffix}",
            extra={"emoji": ":package:", "event": Events.DROP_CLAIM},
        )
        twitch_logger.info(
            "Twitch accepted Drop claim for %s%s (status=%s)",
            getattr(drop, "name", "unknown"), suffix, status,
        )
        return True
    if status == "DROP_INSTANCE_ALREADY_CLAIMED":
        twitch_logger.info(
            "Twitch confirms Drop already claimed: %s%s (status=%s)",
            getattr(drop, "name", "unknown"), suffix, status,
        )
        return True

    twitch_logger.warning(
        "Twitch Drop claim not confirmed for %s%s: %s; next attempt in %s minutes",
        getattr(drop, "name", "unknown"),
        suffix,
        reason,
        delay // 60,
    )
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
        # Inventory confirmation clears per-instance suppression. A new Drop
        # instance is a different key and can always be claimed normally.
        if isinstance(inventory, dict):
            for campaign in inventory.get("dropCampaignsInProgress") or []:
                if not isinstance(campaign, dict):
                    continue
                for inventory_drop in campaign.get("timeBasedDrops") or []:
                    if not isinstance(inventory_drop, dict):
                        continue
                    progress = inventory_drop.get("self")
                    if not isinstance(progress, dict) or progress.get("isClaimed") is not True:
                        continue
                    instance = progress.get("dropInstanceID")
                    if instance:
                        with _LOCK:
                            _CLAIM_ATTEMPTS.pop((id(self), str(instance)), None)
        return inventory

    global _ORIGINAL_DROP_FORMATTER
    _ORIGINAL_DROP_FORMATTER = discord_format_patch._drop_claim
    discord_format_patch._drop_claim = _format_drop_claim_with_game
    Campaign.__init__ = campaign_init_with_game
    Campaign.sync_drops = sync_drops_with_game
    setattr(Twitch, inventory_name, get_inventory_with_games)
    Twitch.claim_drop = _claim_drop_with_game
    setattr(Twitch, _PATCH_MARKER, True)
