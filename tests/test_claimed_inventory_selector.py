"""Regression tests for inventory-aware catalogless Drop selection.

Run after installing project requirements:
    python -m unittest discover -s tests -v
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from TwitchChannelPointsMiner import (
    drop_completion_game_drop_arbitration_patch as controller,
)


class CompletedInventorySelectionTests(unittest.TestCase):
    def setUp(self):
        self.twitch = object()
        self.streamer = SimpleNamespace(
            username="example",
            source="game_drop",
            is_online=True,
            settings=SimpleNamespace(claim_drops=True),
            stream=SimpleNamespace(
                game={"name": "Rust", "displayName": "Rust"},
                campaigns_ids=["old-campaign"],
            ),
        )
        self.streamers = [self.streamer]
        self.config = {
            "catalogless_streamer_games": {"example": "Rust"},
        }
        self.completed = {
            "blocked": True,
            "terminal_ids": {"old-campaign"},
            "reason": "all rewards claimed",
            "blocked_until": 2_000_000_000,
            "game": "Rust",
            "game_key": "rust",
        }

    def _candidate(self):
        return controller._catalogless_candidate(
            self.twitch, self.streamers, self.config, "rust", "Rust", set(), 100.0
        )

    def test_claimed_campaign_blocks_new_and_existing_catalogless_slots(self):
        with (
            patch.object(
                controller.completed_guard,
                "_inventory_state_for_game",
                return_value=self.completed,
            ),
            patch.object(controller.completed_guard, "_log_guard"),
            patch.object(controller.configured, "_watchable", return_value=True),
        ):
            self.assertIsNone(self._candidate())
            existing = {
                "game_key": "rust",
                "username": "example",
                "synthetic": True,
                "campaign_id": "__game_only_drop__:rust",
            }
            valid, index = controller._validate_synthetic_owner(
                self.twitch, self.streamers, self.config, existing
            )
            self.assertFalse(valid)
            self.assertEqual(index, 0)

    def test_new_advertised_campaign_id_does_not_get_blocked(self):
        self.streamer.stream.campaigns_ids.append("new-campaign")
        with (
            patch.object(
                controller.completed_guard,
                "_inventory_state_for_game",
                return_value=self.completed,
            ),
            patch.object(controller.completed_guard, "_log_guard") as log_guard,
            patch.object(controller.configured, "_watchable", return_value=True),
        ):
            candidate = self._candidate()
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate["username"], "example")
            self.assertTrue(candidate["synthetic"])
            valid, index = controller._validate_synthetic_owner(
                self.twitch,
                self.streamers,
                self.config,
                {"game_key": "rust", "username": "example"},
            )
            self.assertTrue(valid)
            self.assertEqual(index, 0)
            log_guard.assert_not_called()

    def test_missing_inventory_does_not_count_as_claimed(self):
        with (
            patch.object(
                controller.completed_guard,
                "_inventory_state_for_game",
                return_value=None,
            ),
            patch.object(controller.configured, "_watchable", return_value=True),
        ):
            self.assertIsNotNone(self._candidate())

    def test_active_unclaimed_campaign_is_not_blocked(self):
        with (
            patch.object(
                controller.completed_guard,
                "_inventory_state_for_game",
                return_value={**self.completed, "blocked": False},
            ),
            patch.object(controller.configured, "_watchable", return_value=True),
        ):
            self.assertIsNotNone(self._candidate())


if __name__ == "__main__":
    unittest.main()
