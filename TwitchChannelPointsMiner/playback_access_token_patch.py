"""Compatibility patch for Twitch PlaybackAccessToken variables."""

from TwitchChannelPointsMiner.classes.Twitch import Twitch

_PATCH_MARKER = "_playback_platform_variable_guard"


def apply_patch():
    """Ensure current PlaybackAccessToken requests include the required platform."""
    original_post_gql_request = Twitch.post_gql_request

    if getattr(original_post_gql_request, _PATCH_MARKER, False):
        return

    def _post_gql_request(self, json_data):
        if (
            isinstance(json_data, dict)
            and json_data.get("operationName") == "PlaybackAccessToken"
        ):
            variables = json_data.setdefault("variables", {})
            if isinstance(variables, dict):
                variables.setdefault("platform", "web")

        return original_post_gql_request(self, json_data)

    setattr(_post_gql_request, _PATCH_MARKER, True)
    Twitch.post_gql_request = _post_gql_request
