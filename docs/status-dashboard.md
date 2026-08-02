# Status dashboard

When Discord logging is configured, the miner maintains one persistent status message per Twitch account. The message is edited instead of sending a new status card on every inventory or watch-state change.

The dashboard contains:

- current miner state and priority order;
- the two active watch slots and their reasons;
- the Drop campaign and Drop currently being farmed;
- queued campaigns, remaining Drops, and eligible streamers;
- the latest points event and latest significant event;
- the five most recently retained Drop claims.

All displayed times use Discord timestamps. Before the dashboard starts, the miner measures the Discord HTTP server clock and applies that offset to locally-created timestamps such as startup, inventory sync, points events, and claims. This prevents a skewed container clock from rendering those events in the future. Twitch-provided campaign end times remain unchanged.

The separate `Miner started` startup card is suppressed in Discord because the persistent dashboard already contains the startup state. Startup details remain available in the normal application log.

The message ID and compact recent activity state are stored next to the account cookie:

```text
cookies/<account>.discord-dashboard.json
```

Only a SHA-256 fingerprint of the webhook URL is persisted. The webhook secret itself is not written to the dashboard state file.

If the stored Discord message was deleted or belongs to a different webhook, the miner creates a replacement and stores its new message ID. Dashboard updates are debounced to avoid editing the message more than once every five seconds.

The dashboard is independent from the normal event feed. Existing Drop claim, points, online/offline, and watch-target notifications continue to be sent according to the configured Discord event list.
