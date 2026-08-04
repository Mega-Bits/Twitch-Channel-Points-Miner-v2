# Persistent Watch Streak state and game-based Drops

## Persistent Watch Streak state

The miner stores the current broadcast ID, locally watched minutes, and whether the Watch Streak reward was already received in:

```text
cookies/watch_streak_state.json
```

Restarting the container during the same broadcast no longer resets the miner's local Watch Streak state. Entries older than 45 days are removed automatically.

The miner no longer relies only on Twitch PubSub to discover Watch Streak candidates. Configured channels with `watch_streak=True` that are currently marked offline are checked through a lightweight live-status request every two minutes. Checks are staggered across the configured list to avoid a burst of Twitch requests. When a live channel is found, its full stream state is refreshed and it becomes eligible for the next `Priority.STREAK` selection cycle without restarting the miner.

A Twitch `stream-up` notification remains the faster path and schedules fresh channel checks after 30 and 75 seconds. This gives Twitch time to expose the new broadcast through its API. When the broadcast ID changes, the local per-broadcast minutes and completion flag are reset before the matching persisted state is restored.

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

For matching active Drop campaigns, the miner checks the configured streamer list first. Only when no configured streamer is currently online, streaming the matching game, and eligible for the campaign does it query Twitch's game directory with the `DROPS_ENABLED` filter.

An explicitly configured game reserves the dedicated Drop slot. A normal priority streamer that happens to expose a different Drop campaign does not count as satisfying that configured game and cannot suppress its directory fallback. The selector accepts Twitch's channel campaign IDs immediately, so a valid game-directory channel no longer has to wait for a later full campaign-object assignment before it can enter the Drop slot.

The second slot normally excludes unrelated Drop-eligible streams because Twitch can advance only one time-based Drop campaign at a time. A pending Watch Streak remains an exception: it may occupy the normal slot until its local streak requirement is complete, even when that channel also advertises another Drop.

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

For example, with `drop_games=["Rust"]`, a Dead by Daylight or Overwatch Drop found on a normal list streamer no longer blocks a qualifying Rust streamer. The miner chooses a main-list Rust streamer when available and otherwise uses a Rust directory candidate.

## Finish already-started unmonitored Drops

Set `finish_started_drops=True` to finish campaigns that Twitch already lists in `dropCampaignsInProgress`, even when their game is not present in `drop_games`:

```python
twitch_miner.mine(
    streamers=["otzdarva", "deadbydaylight"],
    drop_games=["Dead by Daylight"],
    drop_game_limit=10,
    finish_started_drops=True,
)
```

The option defaults to `False`. When enabled, the miner temporarily adds only qualifying inventory campaigns to the Drop selection pool. A campaign qualifies when:

- Twitch reports it as already in progress;
- it still contains an unclaimed Drop;
- its start time has passed;
- its campaign end time has not passed;
- its game is not already explicitly covered by `drop_games`.

Started inventory campaigns are recovered even when Twitch omits them from the separate `ViewerDropsDashboard` campaign catalog. The miner loads their campaign details by ID, synchronizes their inventory progress, and then feeds them through the same campaign lock, streamer preference, game-directory fallback, dashboard, and claim paths. Active campaign and Drop windows are evaluated against UTC rather than the container's local time zone.

Started unmonitored campaigns are ordered by their current Drop progress and use the existing single-campaign lock, so one campaign is completed before the miner moves to the next. The miner first checks eligible configured streamers, then campaign-specific fallback channels, and finally the Drops-enabled game directory. Completed Drops are claimed through the normal inventory claim path.

The temporary game is not added permanently to `drop_games`. In the Discord dashboard it remains marked as `explicit game farming: no`, which distinguishes an inventory-resume campaign from a game explicitly configured by the user.

Untouched campaigns outside `drop_games` are not started by this option, and expired campaigns are ignored. Once the inventory campaign is complete or disappears from Twitch's active inventory, its temporary game-directory candidates and campaign lock are removed.

Useful Drop events:

```python
Events.DROP_STATUS
Events.DROP_CLAIM
Events.START_WATCHING
Events.STOP_WATCHING
```

The persistent Discord dashboard shows the active campaign, current Drop progress, selected streamer, queued campaigns, and recent claims without requiring those events to be added to the dashboard webhook.
