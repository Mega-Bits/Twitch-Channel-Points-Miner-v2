"""Suppress duplicate streamer online/offline logs and notifications."""

from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer

_PATCH_MARKER = "_stream_state_transition_patch"


def apply_patch() -> None:
    """Emit online/offline events only when the state actually changes."""
    set_online = Streamer.set_online
    if not getattr(set_online, _PATCH_MARKER, False):

        def set_online_on_transition(self):
            if self.is_online is True:
                self.toggle_chat()
                return None
            return set_online(self)

        setattr(set_online_on_transition, _PATCH_MARKER, True)
        Streamer.set_online = set_online_on_transition

    set_offline = Streamer.set_offline
    if not getattr(set_offline, _PATCH_MARKER, False):

        def set_offline_on_transition(self):
            if self.is_online is not True:
                self.toggle_chat()
                return None
            return set_offline(self)

        setattr(set_offline_on_transition, _PATCH_MARKER, True)
        Streamer.set_offline = set_offline_on_transition
