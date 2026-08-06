# Completed and unavailable explicit game Drops

The catalogless `drop_games` fallback searches Twitch's game directory with the `DROPS_ENABLED` filter when the personal campaign catalog is unavailable. Twitch can continue returning channels after the current account has already completed the active campaign, and it can temporarily omit the currently watched channel from a paginated directory response.

Version 2.4.3 handles this without persisting Drop progress or completion state and without retrying an unverifiable game forever by default.

## No local progress or completion database

The miner does not write Drop minutes, percentages, claimed rewards, completed campaigns, inferred campaign state, failed candidates, or game latches to disk.

The current Twitch inventory and campaign data remain authoritative. Completing or claiming a Drop outside the miner cannot leave behind a local completion record that overrides Twitch later.

All fallback verification and failure state exists only in memory. Restarting the miner clears it.

## Live inventory terminal states

When Twitch still returns a matching inventory campaign, the game-directory fallback is skipped if that campaign has no reward that still needs watching. This includes:

- campaign status `COMPLETED`, `CLAIMED`, or `EXPIRED`;
- every reward already claimed;
- a completed reward that only needs claiming and therefore does not require additional watching;
- no remaining reward that can finish before the campaign or Drop deadline.

The normal inventory claim path remains responsible for claiming completed rewards.

## Runtime-only session latch

Twitch can remove a completed campaign from `dropCampaignsInProgress`. When the inventory and campaign catalog are unavailable, absence from those sources is not proof of either completion or availability.

The miner therefore provisionally tests channels from the live `DROPS_ENABLED` game directory and requires real Twitch-reported Drop progress. By default:

1. each candidate receives the configured `drop_progress_timeout`;
2. a candidate without progress is rejected by the existing progress verifier;
3. after three different candidates fail for the same game, catalogless discovery for that game is disabled for the rest of the current miner session;
4. normal priority or started-Drop completion can use the slot;
5. a fully identified real Twitch campaign clears the latch immediately;
6. restarting the miner clears the latch because nothing is persisted.

This avoids endless cycles when the account already completed the Drop but Twitch still lists globally Drops-enabled channels for the game.

## Configuration

```python
twitch_miner.mine(
    streamers=[...],
    drop_games=["Rust", "Marvel Rivals"],
    drop_progress_timeout=240,
    drop_candidate_cooldown=900,
    drop_game_failure_limit=3,
    drop_game_retry_cooldown=0,
)
```

- `drop_game_failure_limit` controls how many different catalogless candidates may fail before the game is latched. Set it to `0` to disable the circuit breaker.
- `drop_game_retry_cooldown=0` is the default and keeps the game disabled until a real campaign appears or the miner restarts.
- Set `drop_game_retry_cooldown` to a positive number of seconds to restore periodic in-memory retries. Positive values are capped at six hours.
- Neither setting nor the latch persists across restarts.

## Stable provisional selection

A provisional game-directory channel stays selected while progress verification is pending, even when Twitch changes pagination or viewer ordering and temporarily omits that channel from the latest `DROPS_ENABLED` result page.

When this happens, the miner directly confirms that the active channel is still live and still streaming the configured game. It restores the provisional assignment only after that fresh check.

The channel changes only when:

- Twitch reports no Drop progress before `drop_progress_timeout`;
- the progress verifier explicitly rejects the channel;
- the channel genuinely goes offline;
- the channel changes game;
- a real campaign handoff selects a campaign-specific channel;
- current live inventory data proves the game is complete or no longer finishable.

This prevents the first slot from alternating between `Game drop` and `Priority` during ordinary directory refreshes.

## Example logs

After three different Rust candidates produce no Drop progress with the default configuration:

```text
Disabling catalogless Drop discovery for Rust for the rest of this miner session
after 3 different DROPS_ENABLED channels produced no Twitch-reported progress;
a real Twitch campaign or a miner restart enables the game again, and no
progress or completion state is written to disk
```

With an explicitly configured positive retry cooldown:

```text
Pausing catalogless Drop discovery for Rust for 1800 seconds after 3 different
DROPS_ENABLED channels produced no Twitch-reported progress; no progress or
completion state is written to disk
```

No local Drop-state file is created.
