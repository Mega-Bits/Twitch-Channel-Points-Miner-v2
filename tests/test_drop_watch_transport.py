"""Transport regression tests: Drop HLS session and Spade outcome visibility."""

import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import watch_transport_health as health
from TwitchChannelPointsMiner.classes.Twitch import Twitch


def _streamer(username):
    stream = SimpleNamespace(
        spade_url="https://spade.twitch.tv/track",
        update_elapsed=Mock(return_value=0),
        encode_payload=Mock(return_value={"data": "test-data"}),
        update_minute_watched=Mock(),
        campaigns=[],
    )
    return SimpleNamespace(
        username=username,
        is_online=True,
        online_at=0,
        settings=SimpleNamespace(claim_drops=True),
        stream=stream,
    )


class HeadPolicyTests(TestCase):
    def setUp(self):
        self.streamer = _streamer("drop-channel")
        self.config = {
            "active_selection_kind": "game_drop",
            "active_selection_streamer": "drop-channel",
        }

    def test_auto_probes_full_playlist_only_for_selected_drop_channel(self):
        self.assertTrue(health.use_full_playlist(self.config, self.streamer, "auto"))
        self.streamer.username = "points-channel"
        self.assertFalse(health.use_full_playlist(self.config, self.streamer, "auto"))

    def test_started_drop_completion_is_an_active_drop_slot(self):
        self.config["active_selection_kind"] = "drop_completion"
        self.assertTrue(health.use_full_playlist(self.config, self.streamer, "auto"))

    def test_head_modes_override_auto(self):
        self.assertFalse(health.use_full_playlist(self.config, self.streamer, "single"))
        self.assertTrue(health.use_full_playlist(None, self.streamer, "all"))
        self.assertTrue(health.use_full_playlist(self.config, self.streamer, "INVALID"))

    def test_default_auto_and_head_segment_limit(self):
        with patch.dict(os.environ, {"TWITCH_HLS_HEAD_MODE": "auto"}):
            self.assertTrue(health.use_full_playlist(self.config, self.streamer))
        seq = [f"https://example.com/{i}.ts" for i in range(25)]
        self.assertEqual(health.head_segments(seq, full=True), seq[-15:])
        self.assertEqual(health.head_segments(seq, full=False), seq[-1:])
        self.assertEqual(health.head_segments([], full=True), [])


class WatchTransportTests(TestCase):
    def setUp(self):
        self.twitch = object.__new__(Twitch)
        self.twitch.running = True
        self.twitch.user_agent = "test-agent"
        self.streamers = [_streamer("drop-channel"), _streamer("points-channel")]
        drop_games_patch._CONFIG[id(self.twitch)] = {
            "active_selection_kind": "game_drop",
            "active_selection_streamer": "drop-channel",
        }
        self.addCleanup(drop_games_patch._CONFIG.pop, id(self.twitch), None)
        health._WARNED.clear()
        health._HEALTHY.clear()

    def _perform_one_cycle(self, status_codes=(204, 204)):
        post_calls = []
        segment_url = "https://video-cdn.twitchcdn.net/"

        def mock_get(url, **kwargs):
            if "usher.ttvnw.net" in url:
                return SimpleNamespace(
                    status_code=200,
                    text="\n".join([
                        "#EXTM3U",
                        "https://video-cdn.twitchcdn.net/low.m3u8",
                    ]),
                )
            return SimpleNamespace(
                status_code=200,
                text="\n".join([
                    "#EXTM3U",
                    *(segment_url + f"chunk-{n}.ts" for n in range(3)),
                ]),
            )

        def mock_post(url, **kwargs):
            post_calls.append(url)
            if len(post_calls) == 2:
                self.twitch.running = False
            return SimpleNamespace(status_code=status_codes[len(post_calls) - 1])

        with (
            patch.object(
                Twitch,
                "_select_streamers_to_watch",
                return_value=[0, 1],
            ),
            patch.object(
                Twitch,
                "post_gql_request",
                return_value={
                    "data": {
                        "streamPlaybackAccessToken": {
                            "signature": "sig",
                            "value": "token",
                        }
                    }
                },
            ),
            patch.object(Twitch, "_Twitch__chuncked_sleep"),
            patch("TwitchChannelPointsMiner.classes.Twitch.requests.get", side_effect=mock_get),
            patch("TwitchChannelPointsMiner.classes.Twitch.requests.head") as head,
            patch("TwitchChannelPointsMiner.classes.Twitch.requests.post", side_effect=mock_post),
            patch.object(health.logger, "warning") as warning,
            patch.object(health.logger, "info") as info,
            patch.dict(os.environ, {"TWITCH_HLS_HEAD_MODE": "auto"}),
        ):
            head.return_value.status_code = 200
            self.twitch.send_minute_watched_events(self.streamers, [], chunk_size=3)
        return head.call_count, post_calls, warning, info

    def test_drop_slot_sends_all_hls_heads_and_secondary_one(self):
        count, posts, warning, info = self._perform_one_cycle()
        self.assertEqual(count, 4)  # 3 Drop HLS chunks plus 1 points-only chunk
        self.assertEqual(len(posts), 2)
        warning.assert_not_called()
        self.assertEqual(info.call_count, 1)
        self.assertIn("3 HLS HEAD(s)", str(info.call_args))
        self.streamers[0].stream.update_minute_watched.assert_called_once()
        self.streamers[1].stream.update_minute_watched.assert_called_once()

    def test_non_204_spade_status_produces_visible_warning(self):
        count, posts, warning, info = self._perform_one_cycle((403, 204))
        self.assertEqual(count, 4)
        self.assertEqual(len(posts), 2)
        self.assertEqual(warning.call_count, 1)
        self.assertIn("HTTP 403", str(warning.call_args))
        info.assert_not_called()
        self.streamers[0].stream.update_minute_watched.assert_not_called()
        self.streamers[1].stream.update_minute_watched.assert_called_once()

    def test_status_warnings_are_rate_limited_and_do_not_include_urls(self):
        health._WARNED.clear()
        s = self.streamers[0]
        with (
            patch.object(health.time, "monotonic", side_effect=[0, 10, 301]),
            patch.object(health.logger, "warning") as logger,
        ):
            health.transport_failed(self.twitch, s, "Spade", http_status=403)
            health.transport_failed(self.twitch, s, "Spade", http_status=403)
            health.transport_failed(self.twitch, s, "Spade", http_status=403)
        self.assertEqual(logger.call_count, 2)
        self.assertNotIn("https://", str(logger.call_args_list))


if __name__ == "__main__":
    import unittest
    unittest.main()
