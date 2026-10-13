"""Regression coverage for Drop claim correctness, retries and notifications.

The runtime patches in patches/ are applied by the PR workflow first.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from TwitchChannelPointsMiner import drop_claim_game_patch as claims
from TwitchChannelPointsMiner.classes.Settings import Events
from TwitchChannelPointsMiner.classes.entities.Drop import Drop


def _ready_drop(instance="campaign#drop-1"):
    return SimpleNamespace(
        id="drop-1",
        name="Mailbox",
        benefit="Mailbox",
        drop_instance_id=instance,
        is_claimed=False,
        has_preconditions_met=True,
        current_minutes_watched=120,
        minutes_required=120,
    )


def _response(status):
    return {"data": {"claimDropRewards": {"status": status}}}


class DropEligibilityTests(TestCase):
    def _drop(self):
        start = (datetime.now() - timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        end = (datetime.now() + timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return Drop(
            {
                "id": "drop-1",
                "name": "Mailbox",
                "benefitEdges": [{"benefit": {"name": "Mailbox"}}],
                "requiredMinutesWatched": 120,
                "startAt": start,
                "endAt": end,
            }
        )

    def _progress(self, minutes, instance="campaign#drop-1", claimed=False, prerequisites=True):
        return {
            "hasPreconditionsMet": prerequisites,
            "currentMinutesWatched": minutes,
            "dropInstanceID": instance,
            "isClaimed": claimed,
        }

    def test_instance_id_alone_is_not_claimable(self):
        drop = self._drop()
        drop.update(self._progress(50))
        self.assertFalse(drop.is_claimable)

    def test_completed_drop_is_claimable(self):
        drop = self._drop()
        drop.update(self._progress(120))
        self.assertTrue(drop.is_claimable)

    def test_claim_requires_preconditions(self):
        drop = self._drop()
        drop.update(self._progress(120, prerequisites=False))
        self.assertFalse(drop.is_claimable)

    def test_claim_requires_unclaimed_instance(self):
        drop = self._drop()
        drop.update(self._progress(120, claimed=True))
        self.assertFalse(drop.is_claimable)
        drop.update(self._progress(120, instance=None))
        self.assertFalse(drop.is_claimable)


class ClaimRetryTests(TestCase):
    def setUp(self):
        claims._CLAIM_ATTEMPTS.clear()
        self.now = 100.0
        clock = patch.object(claims.time, "monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.addCleanup(claims._CLAIM_ATTEMPTS.clear)
        self.drop = _ready_drop()
        self.client = SimpleNamespace(
            post_gql_request=Mock(return_value=_response("ELIGIBLE_FOR_ALL"))
        )

    def _claim(self, client=None, drop=None):
        return claims._claim_drop_with_game(client or self.client, drop or self.drop)

    def test_accepted_claim_notifies_once_even_when_inventory_is_stale(self):
        with patch.object(claims.twitch_logger, "info") as info:
            self.assertTrue(self._claim())
            self.assertTrue(self._claim())  # stale isClaimed=False from Twitch
            self.client.post_gql_request.assert_called_once()
            emitted = [
                call
                for call in info.call_args_list
                if call.kwargs.get("extra", {}).get("event") == Events.DROP_CLAIM
            ]
            self.assertEqual(len(emitted), 1)
            self.assertIn("Claim", emitted[0].args[0])
            self.now += claims._CONFIRMED_HOLD_SECONDS + 1
            self.assertTrue(self._claim())
            self.assertEqual(self.client.post_gql_request.call_count, 2)

    def test_already_claimed_is_success_without_new_claim_notification(self):
        self.client.post_gql_request.return_value = _response(
            "DROP_INSTANCE_ALREADY_CLAIMED"
        )
        with patch.object(claims.twitch_logger, "info") as info:
            self.assertTrue(self._claim())
            self.assertTrue(self._claim())
            self.client.post_gql_request.assert_called_once()
            emitted = [
                call for call in info.call_args_list
                if call.kwargs.get("extra", {}).get("event") == Events.DROP_CLAIM
            ]
            self.assertEqual(emitted, [])

    def test_rejected_claim_backoff_and_sanitized_status(self):
        self.client.post_gql_request.return_value = _response(
            "DROP_INSTANCE_NOT_ELIGIBLE"
        )
        with (
            patch.object(claims.twitch_logger, "warning") as warning,
            patch.object(claims.twitch_logger, "info") as info,
        ):
            self.assertFalse(self._claim())
            self.assertFalse(self._claim())
            self.now += 299
            self.assertFalse(self._claim())
            self.client.post_gql_request.assert_called_once()
            self.assertEqual(warning.call_count, 1)
            self.assertEqual(info.call_count, 0)
            self.assertIn("DROP_INSTANCE_NOT_ELIGIBLE", str(warning.call_args))
            self.now += 2
            self.assertFalse(self._claim())
            self.assertEqual(self.client.post_gql_request.call_count, 2)
            self.assertEqual(warning.call_count, 2)
            self.now += 300  # Next cooldown increases to 15 minutes
            self.assertFalse(self._claim())
            self.assertEqual(self.client.post_gql_request.call_count, 2)
            self.now += 601
            self.assertFalse(self._claim())
            self.assertEqual(self.client.post_gql_request.call_count, 3)

    def test_graphql_errors_never_emit_claim_event(self):
        self.client.post_gql_request.return_value = {
            "errors": [{"extensions": {"code": "FORBIDDEN"}}],
            "data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}},
        }
        with (
            patch.object(claims.twitch_logger, "warning") as warning,
            patch.object(claims.twitch_logger, "info") as info,
        ):
            self.assertFalse(self._claim())
            self.assertIn("FORBIDDEN", str(warning.call_args))
            self.assertEqual(info.call_count, 0)

    def test_invalid_and_empty_responses_trigger_backoff(self):
        for response in (None, {}, {"data": {"claimDropRewards": None}}):
            claims._CLAIM_ATTEMPTS.clear()
            self.client.post_gql_request.reset_mock(return_value=True)
            self.client.post_gql_request.return_value = response
            with patch.object(claims.twitch_logger, "warning"):
                self.assertFalse(self._claim())
                self.assertFalse(self._claim())
            self.client.post_gql_request.assert_called_once()

    def test_network_exception_does_not_crash_or_expose_exception_text(self):
        self.client.post_gql_request.side_effect = RuntimeError("super-secret")
        with patch.object(claims.twitch_logger, "warning") as warning:
            self.assertFalse(self._claim())
            self.assertEqual(self.client.post_gql_request.call_count, 1)
            self.assertNotIn("super-secret", str(warning.call_args))
            self.assertIn("RuntimeError", str(warning.call_args))

    def test_does_not_claim_unfinished_or_claimed_drop(self):
        with patch.object(claims.twitch_logger, "info") as info:
            self.drop.current_minutes_watched = 30
            self.assertFalse(self._claim())
            self.drop.current_minutes_watched = 120
            self.drop.is_claimed = True
            self.assertFalse(self._claim())
            self.drop.is_claimed = False
            self.drop.has_preconditions_met = False
            self.assertFalse(self._claim())
            self.client.post_gql_request.assert_not_called()
            info.assert_not_called()

    def test_different_instance_or_client_is_not_blocked(self):
        with patch.object(claims.twitch_logger, "info"):
            self.assertTrue(self._claim())
            second = _ready_drop(instance="campaign#drop-2")
            self.assertTrue(self._claim(drop=second))
            other_client = SimpleNamespace(
                post_gql_request=Mock(return_value=_response("ELIGIBLE_FOR_ALL"))
            )
            self.assertTrue(self._claim(client=other_client))
        self.assertEqual(self.client.post_gql_request.call_count, 2)
        other_client.post_gql_request.assert_called_once()

    def test_concurrent_same_instance_is_reserved_before_network_request(self):
        def reentrant(_):
            self.assertFalse(self._claim())
            return _response("ELIGIBLE_FOR_ALL")

        self.client.post_gql_request.side_effect = reentrant
        with patch.object(claims.twitch_logger, "info"):
            self.assertTrue(self._claim())
        self.client.post_gql_request.assert_called_once()

    def test_confirmed_inventory_status_clears_only_that_instance(self):
        with patch.object(claims.twitch_logger, "info"):
            self.assertTrue(self._claim())
            self.assertTrue(self._claim(drop=_ready_drop("campaign#drop-2")))
        self.assertEqual(len(claims._CLAIM_ATTEMPTS), 2)
        claims._clear_confirmed_claims(
            self.client,
            {
                "dropCampaignsInProgress": [
                    {
                        "timeBasedDrops": [
                            {"self": {"dropInstanceID": self.drop.drop_instance_id, "isClaimed": True}},
                            {"self": {"dropInstanceID": "campaign#drop-2", "isClaimed": False}},
                        ]
                    }
                ]
            },
        )
        self.assertEqual(len(claims._CLAIM_ATTEMPTS), 1)
        self.assertIn((id(self.client), "campaign#drop-2"), claims._CLAIM_ATTEMPTS)
