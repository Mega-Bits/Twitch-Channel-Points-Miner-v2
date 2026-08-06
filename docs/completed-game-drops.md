# Completed and unavailable explicit game Drops

The catalogless `drop_games` fallback searches Twitch's game directory with the `DROPS_ENABLED` filter when the personal campaign catalog is unavailable. Twitch can continue returning channels after the current account has already completed the active campaign, and it can temporarily omit the currently watched channel from a paginated directory response.

Version 2.4.3 handles this without persisting Drop progress or completion state and without rotating continuously through an unverifiable game.

## No local progress or completion database

The miner does not write Drop minutes, percentages, claimed rewards, completed campaigns, inferred campaign state, failed candidates, or retry pauses to disk.

The current Twitch inventory and campaign data remain authoritative. Completing or claiming a Drop outside the miner cannot leave behind a local completion record that overrides Twitch later.

All fallback verification and failure state exists only in memory. Restarting the miner clears it, including a running 24-hour pause.

## Live inventory terminal states

When Twitch still returns a matching inventory campaign, the game-directory fallback is skipped if that campaign has no reward that still needs watching. This includes:

- campaign status `COMPLETED`, `CLAIMED`, or `EXPIRED`;
- every reward already claimed;
- a completed reward that only needs claiming and therefore does not require additional watching;
- no remaining reward that can finish before the campaign or Drop deadline.

The normal inventory claim path remains responsible for claiming completed rewards.

## Runtime-only 24-hour circuit breaker

Twitch can remove a completed campaign from `dropCampaignsInProgress`. When the inventory and campaign catalog are unavailable, absence from those sources is not proof of either completion or availability.

The miner therefore provisionally tests channels from the live `DROPS_ENABLED` game directory and requires real Twitch-reported Drop progress. By default:

1. each candidate receives the configured `drop_progress_timeout`;
2. a candidate without progress is rejected by the existing progress verifier;
3. after three different candidates fail for the same game, catalogless discovery for that game is paused for 24 hours;
4. normal priority or started-Drop completion can use the slot during the pause;
5. after 24 hours, the miner performs a fresh live Twitch check;
6. a fully identified real Twitch campaign clears the pause immediately.

This avoids short retry loops when the account already completed the Drop but Twitch still lists globally Drops-enabled channels for the game.

## Configuration

```python
twitch_miner.mine(
    streamers=[...],
    drop_games=["Rust", "Marvel Rivals"],
    drop_progress_timeout=240,
    drop_candidate_cooldown=900,
    drop_game_failure_limit=3,
    drop_game_retry_cooldown=86400,
)
```

- `drop_game_failure_limit` controls how many different catalogless candidates may fail before the game is paused. Set it to `0` to disable the circuit breaker.
- `drop_game_retry_cooldown=86400` is the default and retries after 24 hours.
- Positive values are accepted up to seven days.
- Setting `drop_game_retry_cooldown=0` keeps the game disabled for the rest of the current miner process instead.
- No setting or pause is persisted across restarts.

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
Pausing catalogless Drop discovery for Rust for 86400 seconds after 3 different
DROPS_ENABLED channels produced no Twitch-reported progress; no progress or
completion state is written to disk
```

After the pause expires:

```text
Retrying catalogless Drop discovery for rust after the in-memory pause expired
```

No local Drop-state file is created.
