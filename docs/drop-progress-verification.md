# Drop channel progress verification

Twitch's game directory with the Drops filter (`DROPS_ENABLED`) proves that a live channel has Drops enabled for the selected game. It does not always prove that the channel participates in one particular channel-exclusive campaign.

Version 2.4.0 verifies automatically discovered `game_drop` and `drop_fallback` channels by watching for a real increase in Twitch's reported Drop progress.

## Configuration

```python
twitch_miner.mine(
    streamers=["otzdarva", "deadbydaylight"],
    drop_games=["Rust", "The Quinfall"],
    drop_game_limit=30,
    drop_progress_timeout=240,
    drop_candidate_cooldown=900,
)
```

- `drop_progress_timeout` is the maximum number of seconds an automatically discovered channel may remain selected without a progress increase. The default is `240`. Values from `1` through `119` are raised to `120` to avoid false failures between Twitch inventory updates. Set it to `0` to disable progress verification.
- `drop_candidate_cooldown` controls how long a failed channel is skipped for the affected game or campaign. The default is `900` seconds. Values are clamped between `60` and `7200` seconds.

## Selection and rotation

The selector keeps the existing priority order:

1. fully identified campaign from `drop_games`;
2. game-directory-only fallback from the configured game and Twitch's `DROPS_ENABLED` filter;
3. already-started unmonitored Drop completion;
4. normal streamer priority.

Progress verification applies only to automatically generated `game_drop` and `drop_fallback` channels. A configured-list streamer still uses the stricter campaign metadata and is not rotated merely because Twitch reports progress late.

When an automatically discovered channel is selected, the miner records the current campaign progress as its baseline. The channel is verified only after Twitch reports a later increase. Existing progress from a previous channel therefore cannot verify a new candidate accidentally.

If no increase is observed before the timeout:

1. the channel is temporarily excluded for that game or campaign;
2. the selector tests the next online channel returned by the Drops-enabled game directory;
3. only after all eligible explicit-game candidates are unavailable may started Drop completion become the fallback.

The temporary exclusion expires automatically. It is not persisted across miner restarts.

## Catalogless handoff

When Twitch initially returns no campaign catalog, verification begins against the game-directory-only selection. If Twitch later exposes the real campaign, the same timer and candidate are retained. The timer is not reset simply because campaign metadata appeared.

If campaign progress becomes visible for the first time after selection, that value becomes the baseline. A subsequent increase is still required before the channel is considered verified.

## Logs

A new candidate starts with a single message:

```text
Verifying Drop progress for example_channel on Rust / Global Warfare 4; rotating after 240 seconds without progress
```

Successful verification logs:

```text
Verified Drops-enabled channel example_channel for Rust / Global Warfare 4 after Twitch reported progress
```

A failed candidate logs:

```text
No Drop progress detected for example_channel on Rust / Global Warfare 4; excluding this channel for 900 seconds and trying the next Drops-enabled channel
```

Internal online checks for every directory candidate remain silent. Normal watch-target changes can still use `Events.START_WATCHING` and `Events.STOP_WATCHING` when those events are explicitly enabled.

## Discord dashboard

While a generated Drop channel is being tested, the selected streamer receives an additional detail line:

```text
Drop verification: waiting for progress · rotates in 4 minutes
```

After Twitch reports an increase, the line changes to:

```text
Drop verification: progress confirmed
```

The rotation deadline uses a Discord timestamp and is rendered in each viewer's local time zone.
