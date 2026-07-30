"""Compatibility guard for Twitch GraphQL channel-points responses."""

import copy

from TwitchChannelPointsMiner.classes.Exceptions import StreamerDoesNotExistException
from TwitchChannelPointsMiner.classes.Twitch import Twitch, logger
from TwitchChannelPointsMiner.classes.entities.CommunityGoal import CommunityGoal
from TwitchChannelPointsMiner.constants import GQLOperations

_PATCH_MARKER = "_missing_channel_points_data_guard"


def _load_channel_points_context(self, streamer):
    json_data = copy.deepcopy(GQLOperations.ChannelPointsContext)
    json_data["variables"] = {"channelLogin": streamer.username}

    response = self.post_gql_request(json_data)
    if response == {}:
        return

    if (
        not isinstance(response, dict)
        or "data" not in response
        or response["data"] is None
    ):
        logger.warning(
            "Invalid response from load_channel_points_context for %s: %s",
            streamer.username,
            response,
        )
        return

    if response["data"]["community"] is None:
        raise StreamerDoesNotExistException

    channel = response["data"]["community"]["channel"]
    community_points = channel["self"]["communityPoints"]
    streamer.channel_points = community_points["balance"]
    streamer.activeMultipliers = community_points["activeMultipliers"]

    if streamer.settings.community_goals is True:
        streamer.community_goals = {
            goal["id"]: CommunityGoal.from_gql(goal)
            for goal in channel["communityPointsSettings"]["goals"]
        }

    if community_points["availableClaim"] is not None:
        self.claim_bonus(streamer, community_points["availableClaim"]["id"])

    if streamer.settings.community_goals is True:
        self.contribute_to_community_goals(streamer)


def apply_patch():
    """Install the response guard once for all Twitch instances."""
    if getattr(Twitch.load_channel_points_context, _PATCH_MARKER, False):
        return

    setattr(_load_channel_points_context, _PATCH_MARKER, True)
    Twitch.load_channel_points_context = _load_channel_points_context
