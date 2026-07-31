"""Discover Drops-enabled live channels from configured Twitch game directories."""

import copy
import logging
import re
import time
import unicodedata

from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Settings import FollowersOrder
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings
from TwitchChannelPointsMiner.constants import GQLOperations

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_drop_games_patch"
_CONFIG = {}
_REFRESH_SECONDS = 120

_DIRECTORY_GAME_REDIRECT = {
    "operationName": "DirectoryGameRedirect",
    "extensions": {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": "1f0300090caceec51f33c5e20647aceff9017f740f223c3c532ba6fa59f6b6cc",
        }
    },
}
_DIRECTORY_PAGE_GAME = {
    "operationName": "DirectoryPage_Game",
    "extensions": {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": "86bcceb4e8b1a51256ff8eed8bd8aae4acacf80d737efe904f84f3aeadf8cafd",
        }
    },
}


def _normalize(value):
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def _slugify(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _settings():
    return StreamerSettings(
        make_predictions=False,
        follow_raid=False,
        claim_drops=True,
        claim_moments=False,
        watch_streak=False,
        community_goals=False,
        bet=None,
        chat=ChatPresence.NEVER,
    )


def _resolve_slug(twitch, game_name, config):
    cache = config.setdefault("slugs", {})
    normalized = _normalize(game_name)
    if normalized in cache:
        return cache[normalized]
    payload = copy.deepcopy(_DIRECTORY_GAME_REDIRECT)
    payload["variables"] = {"name": game_name}
    response = twitch.post_gql_request(payload)
    game = response.get("data", {}).get("game") if isinstance(response, dict) else None
    slug = game.get("slug") if isinstance(game, dict) else None
    slug = slug or _slugify(game_name)
    cache[normalized] = slug
    return slug


def _directory_nodes(twitch, game_name, config):
    slug = _resolve_slug(twitch, game_name, config)
    if not slug:
        return []
    payload = copy.deepcopy(_DIRECTORY_PAGE_GAME)
    payload["variables"] = {
        "limit": config["limit"],
        "slug": slug,
        "imageWidth": 50,
        "includeCostreaming": False,
        "options": {
            "broadcasterLanguages": [],
            "freeformTags": None,
            "includeRestricted": ["SUB_ONLY_LIVE"],
            "recommendationsContext": {"platform": "web"},
            "sort": "VIEWER_COUNT",
            "systemFilters": ["DROPS_ENABLED"],
            "tags": [],
            "requestID": "JIRA-VXP-2397",
        },
        "sortTypeIsRecency": False,
    }
    response = twitch.post_gql_request(payload)
    try:
        edges = response["data"]["game"]["streams"]["edges"]
    except (KeyError, TypeError):
        logger.warning("Unable to discover Drops channels for game %s: %s", game_name, response)
        return []
    return [
        edge.get("node", {})
        for edge in edges
        if isinstance(edge, dict)
        and isinstance(edge.get("node"), dict)
        and isinstance(edge["node"].get("broadcaster"), dict)
    ]


def _matching_campaigns(campaigns, game_name):
    target = _normalize(game_name)
    matches = []
    for campaign in campaigns:
        if not campaign.drops:
            continue
        game = campaign.game or {}
        names = {_normalize(game.get("name", "")), _normalize(game.get("displayName", ""))}
        if target in names:
            matches.append(campaign)
    return matches


def _refresh_game_streamers(twitch, streamers, campaigns):
    config = _CONFIG.get(id(twitch))
    if not config or time.time() - config.get("last_refresh", 0) < _REFRESH_SECONDS:
        return
    config["last_refresh"] = time.time()
    known = {streamer.username: streamer for streamer in streamers}
    seen = set()

    for game_name in config["games"]:
        matching = _matching_campaigns(campaigns, game_name)
        if not matching:
            continue
        campaign_ids = frozenset(campaign.id for campaign in matching)
        for node in _directory_nodes(twitch, game_name, config):
            broadcaster = node["broadcaster"]
            login = (broadcaster.get("login") or "").lower().strip()
            channel_id = broadcaster.get("id") or ""
            if not login:
                continue
            seen.add(login)
            streamer = known.get(login)
            if streamer is None:
                streamer = Streamer(login, settings=_settings(), source="game_drop")
                streamer.channel_id = str(channel_id)
                streamer.fallback_campaign_ids = campaign_ids
                streamers.append(streamer)
                known[login] = streamer
                logger.info("Added Drops-enabled game channel %s for %s", login, game_name)
            elif getattr(streamer, "source", "list") == "game_drop":
                streamer.fallback_campaign_ids = campaign_ids
            else:
                continue
            try:
                twitch.check_streamer_online(streamer)
            except Exception as exc:
                logger.warning("Unable to refresh game Drop channel %s: %s", login, exc)

    for streamer in streamers:
        if getattr(streamer, "source", "list") == "game_drop" and streamer.username not in seen:
            streamer.fallback_campaign_ids = frozenset()
            if streamer.is_online:
                streamer.set_offline()


def _has_drop(streamer):
    return (
        streamer.is_online is True
        and streamer.settings.claim_drops is True
        and bool(getattr(streamer.stream, "campaigns", []))
        and any(campaign.drops for campaign in streamer.stream.campaigns)
    )


def _pick_game_drop(twitch, streamers, indexes):
    locked_id = getattr(twitch, "locked_drop_campaign_id", None)
    candidates = []
    for index in indexes:
        streamer = streamers[index]
        if not _has_drop(streamer):
            continue
        if locked_id is not None and locked_id not in streamer.stream.campaigns_ids:
            continue
        progress = max(
            (
                getattr(drop, "percentage_progress", 0)
                for campaign in streamer.stream.campaigns
                for drop in campaign.drops
            ),
            default=0,
        )
        candidates.append((progress, index))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def get_drop_games(twitch):
    """Return the configured Drop games for a Twitch instance."""
    config = _CONFIG.get(id(twitch)) or {}
    return tuple(config.get("games", ()))


def apply_patch():
    """Install game-directory configuration and selection hooks once."""
    setattr(GQLOperations, "DirectoryGameRedirect", _DIRECTORY_GAME_REDIRECT)
    setattr(GQLOperations, "DirectoryPage_Game", _DIRECTORY_PAGE_GAME)

    original_run = TwitchChannelPointsMiner.run
    if not getattr(original_run, _PATCH_MARKER, False):
        def run_with_drop_games(
            self,
            streamers=[],
            blacklist=[],
            followers=False,
            followers_order=FollowersOrder.ASC,
            drop_games=None,
            drop_game_limit=10,
        ):
            games = tuple(dict.fromkeys(
                str(game).strip() for game in (drop_games or []) if str(game).strip()
            ))
            _CONFIG.pop(id(self.twitch), None)
            if games:
                _CONFIG[id(self.twitch)] = {
                    "games": games,
                    "limit": max(1, min(int(drop_game_limit), 30)),
                    "last_refresh": 0,
                    "slugs": {},
                }
            try:
                return original_run(
                    self,
                    streamers=streamers,
                    blacklist=blacklist,
                    followers=followers,
                    followers_order=followers_order,
                )
            finally:
                _CONFIG.pop(id(self.twitch), None)

        setattr(run_with_drop_games, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.run = run_with_drop_games

    original_mine = TwitchChannelPointsMiner.mine
    if not getattr(original_mine, _PATCH_MARKER, False):
        def mine_with_drop_games(
            self,
            streamers=[],
            blacklist=[],
            followers=False,
            followers_order=FollowersOrder.ASC,
            drop_games=None,
            drop_game_limit=10,
        ):
            return self.run(
                streamers=streamers,
                blacklist=blacklist,
                followers=followers,
                followers_order=followers_order,
                drop_games=drop_games,
                drop_game_limit=drop_game_limit,
            )

        setattr(mine_with_drop_games, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.mine = mine_with_drop_games

    sync_state_name = "_Twitch__sync_drop_campaign_state"
    original_sync_state = getattr(Twitch, sync_state_name, None)
    if original_sync_state is not None and not getattr(original_sync_state, _PATCH_MARKER, False):
        def sync_state_with_games(self, streamers, campaigns):
            result = original_sync_state(self, streamers, campaigns)
            _refresh_game_streamers(self, streamers, campaigns)
            return result

        setattr(sync_state_with_games, _PATCH_MARKER, True)
        setattr(Twitch, sync_state_name, sync_state_with_games)

    original_select = getattr(Twitch, "_select_streamers_to_watch", None)
    if original_select is not None and not getattr(original_select, _PATCH_MARKER, False):
        def select_with_game_drops(self, streamers, priority, max_watch_amount=2):
            game_indexes = [
                index
                for index, streamer in enumerate(streamers)
                if getattr(streamer, "source", "list") == "game_drop"
                and streamer.is_online is True
            ]
            previous_online = {index: streamers[index].is_online for index in game_indexes}
            for index in game_indexes:
                streamers[index].is_online = False
            try:
                selected = list(original_select(self, streamers, priority, max_watch_amount))
            finally:
                for index, online in previous_online.items():
                    streamers[index].is_online = online

            regular_drop_selected = any(_has_drop(streamers[index]) for index in selected)
            game_index = None if regular_drop_selected else _pick_game_drop(self, streamers, game_indexes)
            if game_index is not None:
                safe_priority = [
                    index
                    for index in selected
                    if index != game_index and not _has_drop(streamers[index])
                ]
                selected = [game_index] + safe_priority
            return selected[:max_watch_amount]

        setattr(select_with_game_drops, _PATCH_MARKER, True)
        Twitch._select_streamers_to_watch = select_with_game_drops

    refresh_name = "_Twitch__refresh_fallback_streamers"
    original_refresh = getattr(Twitch, refresh_name, None)
    if original_refresh is not None and not getattr(original_refresh, _PATCH_MARKER, False):
        def refresh_with_game_streamers(self, streamers):
            result = original_refresh(self, streamers)
            for streamer in streamers:
                if getattr(streamer, "source", "list") == "game_drop" and streamer.fallback_campaign_ids:
                    self.check_streamer_online(streamer)
            return result

        setattr(refresh_with_game_streamers, _PATCH_MARKER, True)
        setattr(Twitch, refresh_name, refresh_with_game_streamers)


def apply_startup_patch():
    """Add configured game directories to the existing startup notification."""
    from TwitchChannelPointsMiner import watch_notifications_patch as notifications

    emit = notifications._emit_startup_once
    startup_marker = f"{_PATCH_MARKER}_startup"
    if getattr(emit, startup_marker, False):
        return

    def emit_with_games(twitch, streamers, priority):
        key = id(twitch)
        with notifications._STATE_LOCK:
            if key in notifications._STARTUP_SENT:
                return
            notifications._STARTUP_SENT.add(key)
        notifications._enable_discord_events()
        messages = notifications._startup_messages(streamers, priority)
        games = get_drop_games(twitch)
        if games and messages:
            game_text = ", ".join(f"`{game}`" for game in games)
            messages[0] = f"**Drop games:** {game_text}\n{messages[0]}"
        for message in messages:
            notifications.logger.info(
                message,
                extra={"event": notifications.Events.STARTUP_STATUS},
            )

    setattr(emit_with_games, startup_marker, True)
    notifications._emit_startup_once = emit_with_games
