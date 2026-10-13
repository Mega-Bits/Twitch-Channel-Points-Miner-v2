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


class StartedCampaignEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.twitch = object()
        self.campaign = SimpleNamespace(
            id="started-campaign",
            game={"id": "123", "name": "Rust", "displayName": "Rust"},
        )
        self.config = {"campaigns_by_id": {"started-campaign": self.campaign}}

        def streamer(name, source):
            return SimpleNamespace(
                username=name,
                source=source,
                is_online=True,
                online_at=0,
                settings=SimpleNamespace(claim_drops=True),
                fallback_campaign_ids=frozenset({"started-campaign"}),
                stream=SimpleNamespace(
                    game={"id": "123", "name": "Rust"},
                    campaigns_ids=["started-campaign"],
                    campaigns=[],
                ),
            )

        self.directory = streamer("generic-directory", "game_drop")
        self.fallback = streamer("specific-fallback", "drop_fallback")
        self.configured = streamer("configured", "list")

    def _find_completion(self, streamers):
        with patch.object(
            controller.priority_order,
            "_explicit_first_campaigns",
            return_value=([], ["started-campaign"]),
        ):
            return controller._completion_candidate(
                self.twitch, streamers, self.config, set()
            )

    def test_generic_directory_is_not_proof_of_specific_campaign(self):
        self.assertIsNone(self._find_completion([self.directory]))

    def test_campaign_specific_fallback_is_still_supported(self):
        result = self._find_completion([self.directory, self.fallback])
        self.assertIsNotNone(result)
        self.assertEqual(result["username"], "specific-fallback")
        self.assertEqual(result["kind"], "drop_completion")

    def test_configured_streamer_remains_preferred(self):
        result = self._find_completion(
            [self.directory, self.fallback, self.configured]
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["username"], "configured")

    def test_existing_generic_completion_owner_is_released(self):
        controller._set_slot(
            self.config,
            controller._PRIMARY,
            {
                "campaign_id": "started-campaign",
                "kind": "drop_completion",
                "username": "generic-directory",
                "synthetic": False,
            },
        )
        state, index = controller._observe_slot(
            self.twitch,
            [self.directory, self.fallback],
            self.config,
            controller._PRIMARY,
        )
        self.assertIsNone(state)
        self.assertIsNone(index)
        self.assertIsNone(controller._slot(self.config, controller._PRIMARY))

    def test_explicit_game_campaign_logic_stays_unchanged(self):
        with patch.object(
            controller.priority_order,
            "_explicit_first_campaigns",
            return_value=(["started-campaign"], []),
        ):
            result = controller._real_game_candidate(
                self.twitch,
                [self.directory],
                self.config,
                "rust",
                "Rust",
                set(),
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["username"], "generic-directory")


class InventoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.twitch = object()
        self.now = 1_800_000_000
        self.campaign = {
            "id": "known-campaign",
            "name": "Known Rust campaign",
            "status": "ACTIVE",
            "game": {"id": "123", "name": "Rust", "displayName": "Rust"},
            "endAt": "2030-01-01T00:00:00Z",
            "timeBasedDrops": [
                {
                    "id": "known-drop",
                    "requiredMinutesWatched": 60,
                    "endAt": "2030-01-01T00:00:00Z",
                    "self": {
                        "isClaimed": True,
                        "currentMinutesWatched": 60,
                        "dropInstanceID": "claimed-instance",
                    },
                }
            ],
        }

    def _state(self, campaigns):
        from TwitchChannelPointsMiner import inventory_campaign_recovery_patch as recovery
        from TwitchChannelPointsMiner import completed_game_drop_guard_patch as guard

        with patch.dict(
            recovery._LAST_INVENTORY,
            {id(self.twitch): {"dropCampaignsInProgress": campaigns}},
        ):
            return guard._inventory_state_for_game(
                self.twitch, "Rust", self.now, {}
            )

    def test_claimed_reward_blocks_catalog_only_farming(self):
        state = self._state([self.campaign])
        self.assertTrue(state["blocked"])
        self.assertIn("known-campaign", state["terminal_ids"])

    def test_remaining_unclaimed_reward_reenables_farming(self):
        from copy import deepcopy

        campaign = deepcopy(self.campaign)
        campaign["timeBasedDrops"][0]["self"].update(
            isClaimed=False, currentMinutesWatched=5, dropInstanceID=None
        )
        state = self._state([campaign])
        self.assertFalse(state["blocked"])
        self.assertIn("known-campaign", state["open_ids"])

    def test_missing_active_drop_details_are_not_completion_evidence(self):
        from copy import deepcopy

        incomplete = deepcopy(self.campaign)
        incomplete["timeBasedDrops"] = []
        state = self._state([self.campaign, incomplete | {"id": "new-campaign"}])
        self.assertFalse(state["blocked"])
        self.assertIn("new-campaign", state["open_ids"])

    def test_missing_inventory_does_not_imply_claimed(self):
        self.assertIsNone(self._state([]))


if __name__ == "__main__":
    unittest.main()
