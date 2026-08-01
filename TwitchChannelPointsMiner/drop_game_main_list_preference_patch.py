"""Prefer configured streamers for game-based Drop farming and keep them sticky."""

import logging
import time

from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner import drop_games_patch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_drop_game_main_list_preference_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}


def _source(streamer):
    return str(getattr(streamer, "source", "list")).strip().lower()


def _is_main_streamer(streamer):
    return _source(streamer) not in _FALLBACK_SOURCES


def _game_values(game):
    game = game or {}
    values = set()
    for key in ("id", "name", "displayName"):
        value = game.get(key) if isinstance(game, dict) else None
        if value not in (None, ""):
            values.add(drop_games_patch._normalize(value))
    return values


def _same_game(streamer, campaign):
    stream_game = getattr(getattr(streamer, "stream", None), "game", {}) or {}
    campaign_game = getattr(campaign, "game", {}) or {}

    stream_id = stream_game.get("id") if isinstance(stream_game, dict) else None
    campaign_id = campaign_game.get("id") if isinstance(campaign_game, dict) else None
    if stream_id not in (None, "") and campaign_id not in (None, ""):
        return str(stream_id) == str(campaign_id)

    return bool(_game_values(stream_game).intersection(_game_values(campaign_game)))


def _watchable(streamer):
    return (
        streamer.is_online is True
        and (
            getattr(streamer, "online_at", 0) == 0
            or (time.time() - streamer.online_at) > 30
        )
    )


def _eligible_for_campaign(streamer, campaign, *, main_only=False):
    if main_only and not _is_main_streamer(streamer):
        return False
    if streamer.is_online is not True:
        return False
    if getattr(streamer.settings, "claim_drops", False) is not True:
        return False
    if campaign.id not in (getattr(streamer.stream, "campaigns_ids", []) or []):
        return False
    if not _same_game(streamer, campaign):
        return False

    if _source(streamer) in _FALLBACK_SOURCES:
        campaign_ids = getattr(streamer, "fallback_campaign_ids", frozenset())
        if campaign_ids and campaign.id not in campaign_ids:
            return False
    return True


def _configured_campaigns(config, campaigns):
    ordered = []
    seen = set()
    for game_name in config.get("games", ()):
        for campaign in drop_games_patch._matching_campaigns(campaigns, game_name):
            if campaign.id in seen:
                continue
            seen.add(campaign.id)
            ordered.append(campaign)
    return ordered


def _refresh_main_streamers(twitch, streamers):
    for streamer in streamers:
        if not _is_main_streamer(streamer):
            continue
        if getattr(streamer.settings, "claim_drops", False) is not True:
            continue
        try:
            # Force a fresh game and campaign eligibility check for online
            # configured streamers on every inventory pass.
            if streamer.is_online is True:
                setattr(streamer.stream, "_Stream__last_update", 0)
            twitch.check_streamer_online(streamer)
        except Exception as exc:
            logger.warning(
                "Unable to refresh configured Drop streamer %s: %s",
                streamer.username,
                exc,
            )


def _reconcile_main_locks(twitch, streamers, campaigns, config):
    ordered_campaigns = _configured_campaigns(config, campaigns)
    campaign_by_id = {campaign.id: campaign for campaign in ordered_campaigns}
    valid_ids = set(campaign_by_id)
    locks = config.setdefault("main_streamer_locks", {})

    for campaign_id in list(locks):
        if campaign_id not in valid_ids:
            locks.pop(campaign_id, None)

    main_streamers = [streamer for streamer in streamers if _is_main_streamer(streamer)]
    for campaign in ordered_campaigns:
        previous_username = locks.get(campaign.id)
        previous = next(
            (
                streamer
                for streamer in main_streamers
                if streamer.username == previous_username
            ),
            None,
        )
        if previous is not None and _eligible_for_campaign(
            previous, campaign, main_only=True
        ):
            continue

        replacement = next(
            (
                streamer
                for streamer in main_streamers
                if _eligible_for_campaign(streamer, campaign, main_only=True)
            ),
            None,
        )
        if replacement is None:
            if previous_username is not None:
                logger.info(
                    "Configured streamer %s is no longer eligible for Drop campaign %s",
                    previous_username,
                    campaign.name,
                )
            locks.pop(campaign.id, None)
            continue

        locks[campaign.id] = replacement.username
        if previous_username != replacement.username:
            logger.info(
                "Using configured streamer %s for Drop campaign %s",
                replacement.username,
                campaign.name,
            )

    config["campaigns_by_id"] = campaign_by_id
    config["campaign_order"] = tuple(campaign.id for campaign in ordered_campaigns)

    locked_campaign_id = getattr(twitch, "locked_drop_campaign_id", None)
    if locked_campaign_id in valid_ids:
        config["target_campaign_id"] = locked_campaign_id
    elif config.get("target_campaign_id") not in valid_ids:
        config.pop("target_campaign_id", None)

    return ordered_campaigns


def _refresh_game_streamers(twitch, streamers, campaigns):
    """Refresh the main list first and query directories only when needed."""
    config = drop_games_patch._CONFIG.get(id(twitch))
    if not config:
        return

    _refresh_main_streamers(twitch, streamers)
    ordered_campaigns = _reconcile_main_locks(
        twitch, streamers, campaigns, config
    )
    locks = config.setdefault("main_streamer_locks", {})
    campaign_by_id = {campaign.id: campaign for campaign in ordered_campaigns}
    valid_ids = set(campaign_by_id)

    known = {streamer.username: streamer for streamer in streamers}
    discovered = {}

    # Keep already discovered fallback channels warm while their campaign is active.
    for streamer in streamers:
        if _source(streamer) != "game_drop":
            continue
        streamer.fallback_campaign_ids = frozenset(
            campaign_id
            for campaign_id in getattr(
                streamer, "fallback_campaign_ids", frozenset()
            )
            if campaign_id in valid_ids
        )

    # On every inventory pass, search only games whose campaign has no eligible
    # configured-list streamer. This lets a newly eligible main-list streamer
    # immediately replace a directory fallback at the next selection pass.
    for game_name in config.get("games", ()):
        matching = [
            campaign
            for campaign in drop_games_patch._matching_campaigns(
                ordered_campaigns, game_name
            )
            if campaign.id not in locks
        ]
        if not matching:
            continue

        campaign_ids = {campaign.id for campaign in matching}
        for node in drop_games_patch._directory_nodes(twitch, game_name, config):
            broadcaster = node["broadcaster"]
            login = (broadcaster.get("login") or "").lower().strip()
            if not login:
                continue
            entry = discovered.setdefault(
                login,
                {
                    "channel_id": str(broadcaster.get("id") or ""),
                    "campaign_ids": set(),
                },
            )
            entry["campaign_ids"].update(campaign_ids)

    for login, entry in discovered.items():
        streamer = known.get(login)
        if streamer is None:
            streamer = drop_games_patch.Streamer(
                login,
                settings=drop_games_patch._settings(),
                source="game_drop",
            )
            streamer.channel_id = entry["channel_id"]
            streamer.fallback_campaign_ids = frozenset(entry["campaign_ids"])
            streamers.append(streamer)
            known[login] = streamer
            logger.info("Added Drops-enabled game channel %s", login)
        elif _source(streamer) == "game_drop":
            streamer.fallback_campaign_ids = frozenset(
                set(streamer.fallback_campaign_ids) | entry["campaign_ids"]
            )
        else:
            # Never create a duplicate for a configured-list or allow.channels entry.
            continue

        try:
            twitch.check_streamer_online(streamer)
        except Exception as exc:
            logger.warning(
                "Unable to refresh game Drop channel %s: %s", login, exc
            )

    for streamer in streamers:
        if _source(streamer) != "game_drop":
            continue
        if not streamer.fallback_campaign_ids and streamer.is_online:
            streamer.set_offline()


def _campaigns_for_streamer(config, streamer):
    campaign_by_id = config.get("campaigns_by_id", {})
    return [
        campaign_by_id[campaign_id]
        for campaign_id in config.get("campaign_order", ())
        if campaign_id in campaign_by_id
        and _eligible_for_campaign(streamer, campaign_by_id[campaign_id])
    ]


def _choose_campaign(twitch, config, streamer):
    campaigns = _campaigns_for_streamer(config, streamer)
    if not campaigns:
        return None

    locked_campaign_id = getattr(twitch, "locked_drop_campaign_id", None)
    for preferred_id in (
        locked_campaign_id,
        config.get("target_campaign_id"),
    ):
        if preferred_id is None:
            continue
        for campaign in campaigns:
            if campaign.id == preferred_id:
                return campaign

    progress = getattr(twitch, "drop_campaign_progress", {}) or {}
    position = {
        campaign_id: index
        for index, campaign_id in enumerate(config.get("campaign_order", ()))
    }
    return max(
        campaigns,
        key=lambda campaign: (
            progress.get(campaign.id, 0),
            -position.get(campaign.id, 0),
        ),
    )


def _preferred_main_index(twitch, streamers, config, campaign_id):
    campaign = config.get("campaigns_by_id", {}).get(campaign_id)
    username = config.get("main_streamer_locks", {}).get(campaign_id)
    if campaign is None or username is None:
        return None

    for index, streamer in enumerate(streamers):
        if streamer.username != username:
            continue
        if not _watchable(streamer):
            return None
        if _eligible_for_campaign(streamer, campaign, main_only=True):
            return index
        return None
    return None


def _force_drop_slot(streamers, selected, preferred_index, max_watch_amount):
    safe_priority = [
        index
        for index in selected
        if index != preferred_index
        and not drop_games_patch._has_drop(streamers[index])
    ]
    return ([preferred_index] + safe_priority)[:max_watch_amount]


def apply_patch():
    """Install inventory-time main-list preference and sticky selection."""
    # The existing drop-games sync wrapper resolves this global at call time.
    drop_games_patch._refresh_game_streamers = _refresh_game_streamers

    original_select = getattr(Twitch, "_select_streamers_to_watch", None)
    if original_select is None or getattr(original_select, _PATCH_MARKER, False):
        return

    def select_with_sticky_main(
        self, streamers, priority, max_watch_amount=2
    ):
        selected = list(
            original_select(self, streamers, priority, max_watch_amount)
        )
        config = drop_games_patch._CONFIG.get(id(self))
        if not config:
            return selected

        locked_campaign_id = getattr(self, "locked_drop_campaign_id", None)
        target_campaign_id = (
            locked_campaign_id
            if locked_campaign_id in config.get("campaigns_by_id", {})
            else config.get("target_campaign_id")
        )

        if target_campaign_id is not None:
            preferred_index = _preferred_main_index(
                self, streamers, config, target_campaign_id
            )
            if preferred_index is not None:
                config["target_campaign_id"] = target_campaign_id
                return _force_drop_slot(
                    streamers, selected, preferred_index, max_watch_amount
                )

        # Establish a soft campaign target as soon as a Drop channel is selected.
        # The next inventory sync turns it into the normal campaign lock once
        # Twitch reports progress in the inventory.
        for index in selected:
            streamer = streamers[index]
            if not drop_games_patch._has_drop(streamer):
                continue
            campaign = _choose_campaign(self, config, streamer)
            if campaign is None:
                continue
            config["target_campaign_id"] = campaign.id
            preferred_index = _preferred_main_index(
                self, streamers, config, campaign.id
            )
            if preferred_index is not None:
                return _force_drop_slot(
                    streamers, selected, preferred_index, max_watch_amount
                )
            break

        return selected[:max_watch_amount]

    setattr(select_with_sticky_main, _PATCH_MARKER, True)
    Twitch._select_streamers_to_watch = select_with_sticky_main
