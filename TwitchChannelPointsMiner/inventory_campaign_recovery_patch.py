"""Recover active inventory campaigns missing from the Drops dashboard catalog."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.entities.Campaign import Campaign

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_inventory_campaign_recovery_patch"
_LAST_INVENTORY: dict[int, dict[str, Any]] = {}
_LOGGED_RECOVERIES: set[tuple[int, Any]] = set()


def _inventory_campaigns(inventory: Any) -> list[dict[str, Any]]:
    if not isinstance(inventory, dict):
        return []
    campaigns = inventory.get("dropCampaignsInProgress") or []
    return [campaign for campaign in campaigns if isinstance(campaign, dict)]


def _utc_active(start_at: Any, end_at: Any) -> bool:
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return True
    timezone = getattr(start_at, "tzinfo", None) or getattr(end_at, "tzinfo", None)
    now = datetime.now(timezone) if timezone is not None else datetime.utcnow()
    return start_at <= now < end_at


def _fix_active_windows(campaign: Campaign) -> None:
    campaign.dt_match = _utc_active(campaign.start_at, campaign.end_at)
    for drop in campaign.drops:
        drop.dt_match = _utc_active(drop.start_at, drop.end_at)


def _fallback_details(progress: dict[str, Any]) -> dict[str, Any] | None:
    drops = progress.get("timeBasedDrops") or []
    if not isinstance(drops, list) or not drops:
        return None

    starts = [
        drop.get("startAt")
        for drop in drops
        if isinstance(drop, dict) and drop.get("startAt")
    ]
    ends = [
        drop.get("endAt")
        for drop in drops
        if isinstance(drop, dict) and drop.get("endAt")
    ]
    start_at = progress.get("startAt") or (min(starts) if starts else None)
    end_at = progress.get("endAt") or (max(ends) if ends else None)
    if not start_at or not end_at:
        return None

    allow_value = progress.get("allow")
    allow = dict(allow_value) if isinstance(allow_value, dict) else {"channels": []}
    allow.setdefault("channels", [])

    return {
        "id": progress.get("id"),
        "game": progress.get("game") or {},
        "name": progress.get("name")
        or str(progress.get("id") or "Inventory campaign"),
        "status": progress.get("status") or "ACTIVE",
        "allow": allow,
        "startAt": start_at,
        "endAt": end_at,
        "timeBasedDrops": drops,
    }


def _details_by_id(
    twitch: Twitch,
    missing: list[dict[str, Any]],
) -> dict[Any, dict[str, Any]]:
    details_method = getattr(twitch, "_Twitch__get_campaigns_details", None)
    details: list[Any] = []
    if callable(details_method):
        try:
            details = details_method(
                [
                    {"id": campaign.get("id")}
                    for campaign in missing
                    if campaign.get("id")
                ]
            )
        except Exception as exc:
            logger.warning(
                "Unable to load details for inventory-only Drop campaigns: %s",
                exc,
            )

    by_id = {
        detail.get("id"): detail
        for detail in details
        if isinstance(detail, dict) and detail.get("id") is not None
    }
    for progress in missing:
        campaign_id = progress.get("id")
        if campaign_id not in by_id:
            fallback = _fallback_details(progress)
            if fallback is not None:
                by_id[campaign_id] = fallback
    return by_id


def _recover_campaign(
    twitch: Twitch,
    details: dict[str, Any],
    progress: dict[str, Any],
) -> Campaign | None:
    try:
        campaign = Campaign(details)
        _fix_active_windows(campaign)
        if campaign.dt_match is not True:
            return None
        campaign.in_inventory = True
        campaign.sync_drops(
            progress.get("timeBasedDrops") or [],
            twitch.claim_drop,
        )
        _fix_active_windows(campaign)
        campaign.clear_drops()
        return campaign if campaign.drops else None
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "Unable to recover inventory Drop campaign %s: %s",
            progress.get("id", "unknown"),
            exc,
        )
        return None


def apply_patch() -> None:
    """Add inventory-only campaigns before Drop state reconciliation runs."""
    inventory_name = "_Twitch__get_inventory"
    original_inventory = getattr(Twitch, inventory_name, None)
    if original_inventory is not None and not getattr(
        original_inventory,
        _PATCH_MARKER,
        False,
    ):

        def inventory_with_cache(self: Twitch) -> Any:
            inventory = original_inventory(self)
            _LAST_INVENTORY[id(self)] = (
                inventory if isinstance(inventory, dict) else {}
            )
            return inventory

        setattr(inventory_with_cache, _PATCH_MARKER, True)
        setattr(Twitch, inventory_name, inventory_with_cache)

    sync_name = "_Twitch__sync_campaigns"
    original_sync = getattr(Twitch, sync_name, None)
    if original_sync is None or getattr(original_sync, _PATCH_MARKER, False):
        return

    def sync_with_inventory_campaigns(
        self: Twitch,
        campaigns: list[Campaign],
    ) -> list[Campaign]:
        synced = list(original_sync(self, campaigns))
        inventory_campaigns = _inventory_campaigns(
            _LAST_INVENTORY.get(id(self), {})
        )
        inventory_ids = {
            progress.get("id")
            for progress in inventory_campaigns
            if progress.get("id") is not None
        }
        for key in list(_LOGGED_RECOVERIES):
            if key[0] == id(self) and key[1] not in inventory_ids:
                _LOGGED_RECOVERIES.discard(key)

        existing_ids = {getattr(campaign, "id", None) for campaign in synced}
        missing = [
            progress
            for progress in inventory_campaigns
            if progress.get("id") is not None
            and progress.get("id") not in existing_ids
        ]
        if not missing:
            return synced

        details = _details_by_id(self, missing)
        for progress in missing:
            campaign_id = progress.get("id")
            campaign_details = details.get(campaign_id)
            if campaign_details is None:
                continue
            campaign = _recover_campaign(self, campaign_details, progress)
            if campaign is None:
                continue
            synced.append(campaign)
            key = (id(self), campaign_id)
            if key not in _LOGGED_RECOVERIES:
                game = campaign.game or {}
                game_name = (
                    game.get("displayName")
                    or game.get("name")
                    or "unknown game"
                    if isinstance(game, dict)
                    else str(game)
                )
                logger.info(
                    "Recovered started Drop campaign %s (%s) directly from inventory",
                    campaign.name,
                    game_name,
                )
                _LOGGED_RECOVERIES.add(key)
        return synced

    setattr(sync_with_inventory_campaigns, _PATCH_MARKER, True)
    setattr(Twitch, sync_name, sync_with_inventory_campaigns)
