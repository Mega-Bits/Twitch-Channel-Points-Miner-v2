# Status dashboard

The persistent Discord dashboard uses its own optional webhook and is completely separate from the normal Discord event feed.

Configure both targets in `LoggerSettings`:

```python
logger_settings=LoggerSettings(
    discord=Discord(
        webhook_api="https://discord.com/api/webhooks/EVENTS/WEBHOOK",
        dashboard_webhook_api="https://discord.com/api/webhooks/DASHBOARD/WEBHOOK",
        events=[
            Events.STREAMER_ONLINE,
            Events.STREAMER_OFFLINE,
            Events.START_WATCHING,
            Events.STOP_WATCHING,
            Events.DROP_CLAIM,
            Events.DROP_STATUS,
        ],
    ),
)
```

`webhook_api` receives normal event messages selected by `events`. `dashboard_webhook_api` receives only the single persistent status message. The dashboard does not use a custom event and does not need to be listed in `events`.

The default value of `dashboard_webhook_api` is an empty string. When it is empty, the miner does not create or update a Discord dashboard message.

## Dashboard contents

The dashboard contains:

- current miner state and priority order;
- the two active watch slots, their selection reasons, source, and current Channel Points balance;
- the Drop campaign and Drop currently being farmed;
- the game belonging to every Drop;
- whether that game is explicitly configured in `drop_games`;
- queued campaigns, remaining Drops, and eligible streamers;
- the latest Channel Points event;
- the latest non-points event;
- the five most recently retained Drop claims.

`Last points event` is updated only by Channel Points gains such as watch, claim, raid, and Watch Streak rewards. `Last non-points event` is updated by the remaining event types, including watch-target changes, online/offline changes, Drop status, claims, prediction events, and chat mentions.

A Drop is marked with `explicit game farming: yes` when its campaign game matches one of the names passed through `drop_games`. Campaigns discovered through another eligible source remain visible but are marked `no`.

All displayed times use Discord timestamps. Before the dashboard starts, the miner measures the dashboard webhook's Discord HTTP server clock and applies that offset to locally-created timestamps such as startup, inventory sync, points events, and claims. This prevents a skewed container clock from rendering those events in the future. Twitch-provided campaign end times remain unchanged.

The former separate `Miner started` notification has been removed. The miner no longer creates that startup notification or Discord message; startup state is represented only by the persistent dashboard and normal application logs.

## Request throttling

Dashboard updates are coalesced before being sent to Discord:

- requests are separated by at least 15 seconds;
- multiple state changes inside that interval result in one update containing the newest state;
- a payload identical to the last successful update is not sent again;
- Discord HTTP `429` responses are retried only after the supplied `Retry-After` delay;
- network and Discord server failures use exponential backoff from 15 seconds up to five minutes;
- the worker does not send periodic requests when nothing changed.

Inventory sync normally changes the dashboard roughly once per minute, while faster event bursts are combined into the next safe update.

Temporary Discord failures such as HTTP `503` are logged without a traceback. Dashboard logs never include the webhook ID, token, message ID, or query string. A warning contains only a sanitized reason such as `HTTP 503`, `request timed out`, or `connection error`, together with the next retry delay.

Treat every Discord webhook URL as a secret. If a complete URL appears in a terminal, log file, issue, or chat message, delete or rotate that webhook in Discord before using it again.

## Persistence and recovery

The message ID and compact recent activity state are stored next to the account cookie:

```text
cookies/<account>.discord-dashboard.json
```

Only a SHA-256 fingerprint of the dashboard webhook URL is persisted. The webhook secret itself is not written to the dashboard state file.

The retained state now stores `last_points_event` and `last_non_points_event` separately. Existing `last_event` values are migrated when they contain a non-points event.

If the stored Discord message was deleted or belongs to a different dashboard webhook, the miner creates a replacement and stores its new message ID. The next changed dashboard state detects a deleted stored message and recreates it.

When migrating from the previous shared-webhook behavior, the old dashboard message in the event channel is no longer updated. It can be deleted manually after the new dashboard message appears in the dedicated channel.

Existing Drop claim, points, online/offline, and watch-target notifications continue to be sent to the normal event webhook according to the configured Discord event list. See [events-and-features.md](events-and-features.md) for the complete event reference.
