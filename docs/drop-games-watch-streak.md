# Persistent Watch Streak state and game-based Drops

## Persistent Watch Streak state

The miner stores the current broadcast ID, locally watched minutes, and whether the Watch Streak reward was already received in:

```text
cookies/watch_streak_state.json
```

Restarting the container during the same broadcast no longer resets the miner's local Watch Streak state. Entries older than 45 days are removed automatically.

This persistence does not change Twitch's server-side Watch Streak rules. Twitch may allow a missed streak of 3 or more broadcasts to be recovered within 24 hours by watching eligible Clips, Stories, VODs, or a later live stream. Only content marked by Twitch as eligible counts; automatic Clip recovery is not implemented by this patch.

Useful notification events:

```python
Events.START_WATCHING
Events.STOP_WATCHING
Events.GAIN_FOR_WATCH_STREAK
```

## Farm Drops by game

Pass `drop_games` to `mine()` or `run()`:

```python
twitch_miner.mine(
    streamers=[
        "otzdarva",
        "deadbydaylight",
    ],
    drop_games=[
        "Dead by Daylight",
        "Overwatch 2",
    ],
    drop_game_limit=10,
)
```

For matching active Drop campaigns, the miner checks the configured streamer list first. Only when no configured streamer is currently online, streaming the matching game, and eligible for that campaign does it query Twitch's game directory with the `DROPS_ENABLED` filter.

At every inventory sync the miner:

1. refreshes game and Drop eligibility for configured streamers;
2. keeps the already selected configured streamer while it remains eligible;
3. switches to another configured streamer only when the current one goes offline, changes game, or loses campaign eligibility;
4. searches the game directory when no configured streamer qualifies;
5. replaces a directory fallback with a qualifying configured streamer as soon as the next inventory sync detects one;
6. releases the streamer and campaign lock when the Drop campaign is complete.

Game-directory channels:

- are used only for the dedicated Drop slot;
- never enter `ORDER`, `STREAK`, `SUBSCRIBED`, or point-balance priorities;
- remain available as warm fallbacks while their campaign is still active;
- never override an eligible configured-list streamer.

`drop_game_limit` accepts values from 1 through 30 per game and defaults to 10.

Useful Drop events:

```python
Events.DROP_STATUS
Events.DROP_CLAIM
Events.START_WATCHING
Events.STOP_WATCHING
```

The persistent Discord dashboard shows the active campaign, current Drop progress, selected streamer, queued campaigns, and recent claims without requiring those events to be added to the dashboard webhook.
