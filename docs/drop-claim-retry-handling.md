# Twitch Drop claims: accepted status, deduplication and retries

The miner watches eligible channels, updates Drop progress and attempts a reward
claim only when Twitch reports all required viewing minutes, all prerequisites,
a concrete Drop instance ID and `isClaimed=false`.

## Interpreting claim logs

`Claim Drop(...)` (the existing Discord `DROP_CLAIM` event) is now emitted
**after** Twitch's GraphQL claim mutation responds with the previously
recognized successful status `ELIGIBLE_FOR_ALL`. A
`DROP_INSTANCE_ALREADY_CLAIMED` result is treated as already claimed but
**does not trigger a new-claim notification**.

Responses with errors, missing fields, unknown statuses, or network exceptions
are not reported as successful. They instead emit a brief warning recording the
name/game, a sanitized Twitch status or error code (never the raw response),
and the retry interval. These warnings are rate-limited to one per actual
attempt.

A successful claim response confirms that Twitch accepted the claim request;
it is not a guarantee that the reward has already arrived inside the game.
Use Twitch Drops inventory and the game's account linking to verify delivery.

## Retry policy

Claim attempts are tracked **per Twitch account client and Drop instance ID**,
not per game. This avoids blocking a new instance/campaign for the same game.
The in-memory state is shared by the normal campaign-sync and inventory-sync
claim paths.

- One request for an instance can be in flight at a time (2-minute reservation).
- Failed attempts retry after **5, 15, 30, then 60 minutes**, staying at a
  60-minute maximum instead of retrying on every inventory update.
- A newly accepted (`ELIGIBLE_FOR_ALL`) response suppresses repeat claims
  for **30 minutes**. If Twitch's inventory still says `isClaimed=false`
  afterward, the miner can recheck instead of silently losing the claim for
  hours.
- A definitive `DROP_INSTANCE_ALREADY_CLAIMED` response suppresses requests
  for **6 hours**; new-claim notifications are not emitted for this status.
- When the Twitch inventory explicitly reports `isClaimed=true` for that
  instance, its retry state is cleared. Missing inventory is *not* evidence of
  claim confirmation, and no claim state is persisted to disk.

These timings are conservative retry protections, not assumptions about the
account's Drop eligibility. Restarting the miner resets in-memory throttle
state.

## Automated tests

```sh
python -m unittest discover -s tests -v
```

Tests exercise incomplete rewards, unmet prerequisites, accepted/rejected
GraphQL statuses, network errors, stale inventory, deduplicated parallel
requests, separate accounts/instances, and retry cooldowns.
