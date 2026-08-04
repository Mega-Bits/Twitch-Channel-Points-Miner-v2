"""Show the selected Drop purpose instead of labeling every slot as Priority."""

from __future__ import annotations

from typing import Any

from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import finish_started_drops_patch
from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner import watch_notifications_patch

_PATCH_MARKER = "_status_dashboard_drop_reason_patch"


def _game_name(campaign: Any) -> str:
    game = getattr(campaign, "game", {}) or {}
    if isinstance(game, dict):
        return str(game.get("displayName") or game.get("name") or "Unknown game")
    return str(game or "Unknown game")


def _selection(twitch: Any, streamer: Any) -> tuple[dict[str, Any], Any, str] | None:
    config = drop_games_patch._CONFIG.get(id(twitch)) or {}
    if config.get("active_selection_streamer") != getattr(streamer, "username", None):
        return None
    campaign_id = config.get("active_selection_campaign_id")
    campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
    kind = str(config.get("active_selection_kind") or "")
    if campaign is None or kind not in {"game_drop", "drop_completion"}:
        return None
    return config, campaign, kind


def _campaign_details(campaign: Any, explicit: bool) -> dict[str, Any] | None:
    drops = [
        drop
        for drop in (getattr(campaign, "drops", ()) or ())
        if not getattr(drop, "is_claimed", False)
    ]
    if not drops:
        return None
    game = _game_name(campaign)
    drop = dashboard._drop_snapshot(drops[0])
    drop["game"] = game
    drop["explicit_drop_game"] = explicit
    return {
        "id": getattr(campaign, "id", None),
        "name": str(getattr(campaign, "name", "Unknown campaign")),
        "game": game,
        "drop": drop,
        "explicit_drop_game": explicit,
    }


def _reason_for_campaign(twitch: Any, campaign: Any) -> str:
    game = _game_name(campaign)
    explicit_games = {
        drop_games_patch._normalize(value)
        for value in finish_started_drops_patch.get_explicit_drop_games(twitch)
    }
    if drop_games_patch._normalize(game) in explicit_games:
        return "Game drop"
    if finish_started_drops_patch.is_started_drop_resume(
        twitch,
        getattr(campaign, "id", None),
    ):
        return "Drop completion"
    return "Drop"


def apply_patch() -> None:
    """Install dashboard and watch-notification reason corrections."""
    current_slot_snapshot = dashboard._slot_snapshot
    if not getattr(current_slot_snapshot, _PATCH_MARKER, False):

        def slot_snapshot_with_drop_reason(twitch: Any, streamer: Any) -> dict[str, Any]:
            slot = current_slot_snapshot(twitch, streamer)
            selected = _selection(twitch, streamer)
            if selected is not None:
                _, campaign, kind = selected
                explicit = kind == "game_drop"
                slot["reason"] = "Game drop" if explicit else "Drop completion"
                if not isinstance(slot.get("campaign"), dict):
                    details = _campaign_details(campaign, explicit)
                    if details is not None:
                        slot["campaign"] = details
                else:
                    slot["campaign"]["explicit_drop_game"] = explicit
                    drop = slot["campaign"].get("drop")
                    if isinstance(drop, dict):
                        drop["explicit_drop_game"] = explicit
                        drop["game"] = slot["campaign"].get("game") or _game_name(campaign)
                return slot

            campaign_data = slot.get("campaign")
            if isinstance(campaign_data, dict):
                campaign_id = campaign_data.get("id")
                config = drop_games_patch._CONFIG.get(id(twitch)) or {}
                campaign = (config.get("campaigns_by_id", {}) or {}).get(campaign_id)
                if campaign is not None:
                    slot["reason"] = _reason_for_campaign(twitch, campaign)
            return slot

        setattr(slot_snapshot_with_drop_reason, _PATCH_MARKER, True)
        dashboard._slot_snapshot = slot_snapshot_with_drop_reason

    current_watch_reason = watch_notifications_patch._watch_reason
    if not getattr(current_watch_reason, _PATCH_MARKER, False):

        def watch_reason_with_drop_reason(twitch: Any, streamer: Any):
            selected = _selection(twitch, streamer)
            if selected is None:
                return current_watch_reason(twitch, streamer)
            _, campaign, kind = selected
            reason = "Game drop" if kind == "game_drop" else "Drop completion"
            details = _campaign_details(campaign, kind == "game_drop")
            if details is None:
                return reason, None
            drop = details["drop"]
            return (
                reason,
                f"**Campaign:** {details['name']}\n"
                f"**Game:** {details['game']}\n"
                f"**Drop:** {drop['name']}\n"
                f"**Progress:** `{drop['current']}/{drop['required']}` · `{drop['percent']}%`",
            )

        setattr(watch_reason_with_drop_reason, _PATCH_MARKER, True)
        watch_notifications_patch._watch_reason = watch_reason_with_drop_reason
