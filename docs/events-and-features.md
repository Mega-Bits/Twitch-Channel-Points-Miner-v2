# Events and Mega-Bits fork features

## Notification events

Events can be selected independently for Discord, Telegram, Matrix, Gotify, Pushover, or the generic webhook integration.

### Watch selection

| Event | Meaning |
|---|---|
| `Events.START_WATCHING` | A channel enters one of the active watch slots. The message includes the selection reason and Drop details when applicable. |
| `Events.STOP_WATCHING` | A channel leaves an active watch slot. |
| `Events.STREAMER_ONLINE` | A configured streamer is detected online. |
| `Events.STREAMER_OFFLINE` | A configured streamer is detected offline. |

Watch-selection events follow the same opt-in filtering as every other notification. They are emitted internally so the dashboard and other configured integrations can observe state changes, but the Discord event webhook receives them only when they are explicitly included in `Discord(events=[...])`.

For example, this configuration sends start notifications but not stop notifications:

```python
discord=Discord(
    webhook_api="https://discord.com/api/webhooks/EVENTS/WEBHOOK",
    dashboard_webhook_api="https://discord.com/api/webhooks/DASHBOARD/WEBHOOK",
    events=[Events.START_WATCHING],
)
```

An empty Discord `events` list disables all normal Discord event messages while leaving the separately configured persistent dashboard active.

### Channel Points

| Event | Meaning |
|---|---|
| `Events.GAIN_FOR_WATCH` | Periodic watch points were credited. |
| `Events.GAIN_FOR_WATCH_STREAK` | A Watch Streak reward was credited. |
| `Events.GAIN_FOR_CLAIM` | Points were credited from a claim. |
| `Events.GAIN_FOR_RAID` | Points were credited for participating in a raid. |
| `Events.BONUS_CLAIM` | The clickable Channel Points bonus was claimed. |
| `Events.JOIN_RAID` | The miner followed a raid. |

### Drops and Moments

| Event | Meaning |
|---|---|
| `Events.DROP_STATUS` | Drop campaign progress changed. |
| `Events.DROP_CLAIM` | A completed Drop was claimed. |
| `Events.MOMENT_CLAIM` | A Twitch Moment was claimed. |

### Predictions and chat

| Event | Meaning |
|---|---|
| `Events.BET_START` | Prediction handling started. |
| `Events.BET_WIN` | A prediction won. |
| `Events.BET_LOSE` | A prediction lost. |
| `Events.BET_REFUND` | Prediction points were refunded. |
| `Events.BET_FILTERS` | A configured prediction filter prevented a bet. |
| `Events.BET_GENERAL` | General prediction information. |
| `Events.BET_FAILED` | Prediction placement failed. |
| `Events.CHAT_MENTION` | The configured account was mentioned in chat. |

The old separate startup notification is not generated. The miner's startup state is represented by normal application logs and, when configured, the persistent Discord dashboard.

## Persistent Discord dashboard

Configure a separate webhook:

```python
discord=Discord(
    webhook_api="https://discord.com/api/webhooks/EVENTS/WEBHOOK",
    dashboard_webhook_api="https://discord.com/api/webhooks/DASHBOARD/WEBHOOK",
    events=[Events.START_WATCHING, Events.STOP_WATCHING, Events.DROP_CLAIM],
)
```

The event webhook and dashboard webhook are independent. The dashboard edits one persistent message, uses Discord-native timestamps, recreates a deleted message, and is disabled when `dashboard_webhook_api` is empty.

## Game-based Drop farming

```python
twitch_miner.mine(
    streamers=["otzdarva", "deadbydaylight"],
    drop_games=["Dead by Daylight", "Overwatch 2"],
    drop_game_limit=10,
)
```

For every configured game campaign, the miner:

1. refreshes all main-list streamers with `claim_drops=True` during inventory sync;
2. keeps the selected main-list streamer while that streamer remains eligible;
3. switches to another main-list streamer only after eligibility is lost;
4. queries the Twitch game directory only when no main-list streamer qualifies;
5. returns from a directory fallback to a qualifying main-list streamer on the next inventory sync;
6. clears campaign locks and fallback associations after completion.

Directory candidates are restricted to the dedicated Drop slot. They do not participate in normal order, Watch Streak, subscription, point-balance, or prediction priorities.

## Watch slots

The selector can watch up to two streams:

- one slot is reserved for the active Drop campaign when a valid Drop target exists;
- the other slot follows the configured priority, including Watch Streak and normal streamer order.

A global campaign lock prevents progress from being spread across multiple Drop campaigns at the same time.

## Persistent Watch Streak state

Local Watch Streak progress is stored in:

```text
cookies/watch_streak_state.json
```

The state is restored only for the same Twitch account, streamer, and broadcast ID. It does not modify Twitch's server-side rules and cannot recover a missed broadcast by itself.

## Version and Docker image identity

The fork version is read from `TwitchChannelPointsMiner/VERSION`. Runtime update checks, Python package metadata, and GHCR release tags use that same value. See [versioning.md](versioning.md).
