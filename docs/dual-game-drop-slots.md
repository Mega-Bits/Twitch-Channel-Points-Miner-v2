# Using the idle second watch slot for another game Drop

Version 2.4.4 can use the second watch slot for a second Drop game when that slot would otherwise be unused.

## Conditions

The second Drop slot is enabled only when all of the following are true:

1. the normal first Drop slot is already farming a `game_drop`;
2. `max_watch_amount` allows at least two watch slots;
3. the normal selector returned no second streamer;
4. no configured-list streamer is currently online;
5. another farmable Drop candidate exists for a different normalized game.

The second Drop never uses another campaign for the same game as slot 1. For example, Rust in slot 1 can be paired with Marvel Rivals in slot 2, but two Rust campaigns are not used concurrently.

As soon as any configured-list streamer becomes online, the temporary second Drop is released so the normal list priority can own that slot again.

## Candidate order

The idle slot prefers candidates in this order:

1. another explicit `drop_games` campaign with known campaign data;
2. another explicit `drop_games` game discovered through Twitch's `DROPS_ENABLED` game directory while the campaign catalog is unavailable;
3. a started unfinished `finish_started_drops` campaign from another game.

A candidate must still be online, eligible for Drops, and streaming the matching game. Existing per-channel rejection and catalogless-game circuit-breaker state is respected.

## Independent progress verification

Generated `game_drop` and `drop_fallback` channels in the second slot receive their own progress-verification state. They use the same `drop_progress_timeout`, `drop_candidate_cooldown`, `drop_game_failure_limit`, and 24-hour `drop_game_retry_cooldown` behavior as the primary game-Drop slot.

A second-slot failure cannot clear or restart the primary slot's Drop verification state. Catalogless failures for the second game still contribute to that game's in-memory circuit breaker.

## Stable directory selection

The active second catalogless channel is kept across Twitch directory pagination and viewer-order changes. If the latest directory page omits it, the miner directly rechecks the channel and keeps it only while it is still live and streaming the same game.

## Dashboard and notifications

The second slot is rendered with its real reason (`Game drop` or `Drop completion`), game/campaign details, and progress-verification countdown instead of appearing as a normal `Priority` streamer.

No Drop progress, completion state, or second-slot state is persisted to disk.
