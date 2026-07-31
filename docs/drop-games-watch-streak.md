# Persistent Watch Streak state and game-based Drops

## Persistent Watch Streak state

The miner stores the current broadcast ID, locally watched minutes, and whether the Watch Streak reward was already received in:

```text
cookies/watch_streak_state.json
```

Restarting the container during the same broadcast no longer resets the miner's local Watch Streak state. Entries older than 45 days are removed automatically.

This persistence does not change Twitch's server-side Watch Streak rules. Twitch may allow a missed streak of 3 or more broadcasts to be recovered within 24 hours by watching eligible Clips, Stories, VODs, or a later live stream. Only content marked by Twitch as eligible counts; automatic Clip recovery is not implemented by this patch.

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

For matching active Drop campaigns, the miner queries Twitch's game directory with the `DROPS_ENABLED` filter and dynamically adds live candidates.

Game-directory channels:

- are used only for the dedicated Drop slot;
- never enter `ORDER`, `STREAK`, `SUBSCRIBED`, or point-balance priorities;
- are refreshed every two minutes and checked for online state during campaign sync;
- prefer configured streamer-list channels when one already qualifies;
- follow the existing campaign lock until the Drop campaign is complete.

`drop_game_limit` accepts values from 1 through 30 per game and defaults to 10.
