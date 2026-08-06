# Completed and unavailable explicit game Drops

The catalogless `drop_games` fallback searches Twitch's game directory with the `DROPS_ENABLED` filter when the personal campaign catalog is unavailable. Twitch can continue returning channels after the current account has already completed the active campaign, and it can temporarily omit the currently watched channel from a paginated directory response.

Version 2.4.2 handles both cases without persisting Drop progress or completion state.

## No local progress or completion database

The miner does not write Drop minutes, percentages, claimed rewards, completed campaigns, or inferred campaign state to disk.

The current Twitch inventory and campaign data remain authoritative. Completing or claiming a Drop outside the miner cannot leave behind a local completion record that overrides Twitch later.

The runtime pause described below exists only in memory. Restarting the miner clears it.

## Live inventory terminal states

When Twitch still returns a matching inventory campaign, the game-directory fallback is skipped if that campaign has no reward that still needs watching. This includes:

- campaign status `COMPLETED`, `CLAIMED`, or `EXPIRED`;
- every reward already claimed;
- a completed reward that only needs claiming and therefore does not require additional watching;
- no remaining reward that can finish before the campaign or Drop deadline.

The normal inventory claim path remains responsible for claiming completed rewards.

## Runtime-only circuit breaker

Twitch can remove a completed campaign from `dropCampaignsInProgress`. In that state, absence from inventory is not treated as proof of either completion or availability.

The miner provisionally tests channels from the live `DROPS_ENABLED` game directory and requires real Twitch-reported Drop progress. By default:

1. each candidate receives the configured `drop_progress_timeout`;
2. a candidate without progress is rejected by the existing progress verifier;
3. after three different candidates fail for the same game, catalogless discovery for that game is paused for 30 minutes;
4. normal priority or started-Drop completion can use the slot during the pause;
5. after the pause, the miner performs a fresh live Twitch check.

The pause and failure history are memory-only and are never written to disk.

A fully identified real campaign from Twitch immediately bypasses and clears the runtime pause.

## Configuration

```python
twitch_miner.mine(
    streamers=[...],
    drop_games=["Rust", "Marvel Rivals"],
    drop_progress_timeout=240,
    drop_candidate_cooldown=900,
    drop_game_failure_limit=3,
    drop_game_retry_cooldown=1800,
)
```

- `drop_game_failure_limit` controls how many different catalogless candidates may fail before the game is paused. Set it to `0` to disable the circuit breaker.
- `drop_game_retry_cooldown` controls the in-memory pause in seconds and is clamped between 60 seconds and six hours.
- Neither setting persists across restarts.

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

After three different Rust candidates produce no Drop progress:

```text
Pausing catalogless Drop discovery for Rust for 1800 seconds after 3 different
DROPS_ENABLED channels produced no Twitch-reported progress; no progress or
completion state is written to disk
```

When the in-memory pause expires:

```text
Retrying catalogless Drop discovery for rust after the in-memory pause expired
```

No local Drop-state file is created.
