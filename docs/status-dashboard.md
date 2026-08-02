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
- the two active watch slots and their reasons;
- the Drop campaign and Drop currently being farmed;
- queued campaigns, remaining Drops, and eligible streamers;
- the latest points event and latest significant event;
- the five most recently retained Drop claims.

All displayed times use Discord timestamps. Before the dashboard starts, the miner measures the dashboard webhook's Discord HTTP server clock and applies that offset to locally-created timestamps such as startup, inventory sync, points events, and claims. This prevents a skewed container clock from rendering those events in the future. Twitch-provided campaign end times remain unchanged.

The former separate `Miner started` notification has been removed. The miner no longer creates that startup notification or Discord message; startup state is represented only by the persistent dashboard and normal application logs.

## Persistence and recovery

The message ID and compact recent activity state are stored next to the account cookie:

```text
cookies/<account>.discord-dashboard.json
```

Only a SHA-256 fingerprint of the dashboard webhook URL is persisted. The webhook secret itself is not written to the dashboard state file.

If the stored Discord message was deleted or belongs to a different dashboard webhook, the miner creates a replacement and stores its new message ID. Dashboard updates are debounced to avoid editing the message more than once every five seconds.

When migrating from the previous shared-webhook behavior, the old dashboard message in the event channel is no longer updated. It can be deleted manually after the new dashboard message appears in the dedicated channel.

Existing Drop claim, points, online/offline, and watch-target notifications continue to be sent to the normal event webhook according to the configured Discord event list. See [events-and-features.md](events-and-features.md) for the complete event reference.
