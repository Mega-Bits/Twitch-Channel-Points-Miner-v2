# Status dashboard

When Discord logging is configured, the miner maintains one persistent status message per Twitch account. The message is edited instead of sending a new status card on every inventory or watch-state change.

The dashboard contains:

- current miner state and priority order;
- the two active watch slots and their reasons;
- the Drop campaign and Drop currently being farmed;
- queued campaigns, remaining Drops, and eligible streamers;
- the latest points event and latest significant event;
- the five most recently retained Drop claims.

All displayed times use Discord timestamps. Discord therefore renders absolute and relative times in each viewer's local time zone.

The message ID and compact recent activity state are stored next to the account cookie:

```text
cookies/<account>.discord-dashboard.json
```

Only a SHA-256 fingerprint of the webhook URL is persisted. The webhook secret itself is not written to the dashboard state file.

If the stored Discord message was deleted or belongs to a different webhook, the miner creates a replacement and stores its new message ID. Dashboard updates are debounced to avoid editing the message more than once every five seconds.

The dashboard is independent from the normal event feed. Existing Drop claim, points, online/offline, and watch-target notifications continue to be sent according to the configured Discord event list.

## SQLite history

The miner also records dashboard history in a per-account SQLite database:

```text
cookies/<account>.miner-status.sqlite3
```

The database uses WAL mode and contains:

- every structured miner event received by the dashboard handler;
- deduplicated inventory and watch-slot snapshots;
- Drop claims as filterable events;
- watch sessions with channel, reason, source, campaign, start time, and end time.

Unchanged snapshots are not inserted repeatedly. Entries older than 90 days are removed by a daily cleanup pass. Watch sessions left open by an unclean shutdown are closed automatically when the database is opened again.

The history layer exposes bounded queries for recent events, claims, snapshots, and watch sessions. These APIs are used by the read-only web dashboard in the next stacked change.
