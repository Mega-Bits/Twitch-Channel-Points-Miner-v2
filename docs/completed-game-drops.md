# Completed explicit game Drops

The catalogless `drop_games` fallback searches Twitch's game directory with the `DROPS_ENABLED` filter when the personal campaign catalog is unavailable. This can still return channels after the account has already completed the active campaign for that game.

Version 2.4.1 cross-checks this fallback against the latest Twitch inventory before assigning the Drop slot.

## Terminal inventory states

The game-directory fallback is skipped when the matching inventory campaign has no reward that still needs watching. This includes:

- campaign status `COMPLETED`, `CLAIMED`, or `EXPIRED`;
- every reward already claimed;
- a completed reward that only needs claiming and therefore does not require additional watching;
- no remaining reward that can finish before the campaign or Drop deadline.

The normal inventory claim path remains responsible for claiming completed rewards.

## Expiry handling

A completed campaign suppresses the game-only fallback until its Twitch end time plus a short grace period. The guard is not permanent. After the deadline it permits discovery again so a future campaign for the same game cannot remain blocked forever.

When Twitch exposes a different campaign ID on a Drops-enabled channel, that new ID bypasses the guard immediately. This supports overlapping old and new campaigns for the same game.

## Stable provisional selection

A provisional game-directory channel stays selected while progress verification is pending. A directory reorder alone no longer causes a stop/start cycle.

The channel changes only when:

- Twitch reports no Drop progress before `drop_progress_timeout`;
- the channel goes offline;
- the channel changes game;
- the channel becomes ineligible;
- the inventory proves that the campaign is complete or no longer finishable.

## Example log

```text
Skipping game-directory Drop fallback for Rust because inventory is terminal:
Global Warfare 4: completed. Rechecking automatically; this guard expires at
2026-08-06T16:10:00+00:00 and is bypassed immediately when Twitch advertises a
different campaign ID
```

No additional configuration is required.
