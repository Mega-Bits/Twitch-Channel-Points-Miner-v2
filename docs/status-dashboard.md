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

## Read-only web dashboard

The web dashboard is disabled by default. Enable it through `mine()` or `run()`:

```python
twitch_miner.mine(
    streamers=["otzdarva"],
    drop_games=["Dead by Daylight"],
    status_web=True,
    status_web_host="127.0.0.1",
    status_web_port=8080,
)
```

It provides:

- a responsive live overview refreshed every ten seconds;
- current watch slots and Drop progress;
- queued campaigns and eligible streamers;
- recent events from SQLite;
- recent watch sessions;
- JSON endpoints for status, events, claims, snapshots, and watch sessions;
- an unauthenticated `/healthz` endpoint for container health checks.

The server binds to `127.0.0.1` by default. Binding to another interface requires an authentication token:

```python
status_web=True,
status_web_host="0.0.0.0",
status_web_port=8080,
status_web_token="replace-with-a-long-random-token",
```

When a token is configured, the browser uses HTTP Basic authentication. The username is ignored and the token is used as the password. The application sends no-store, frame-denial, content-type, referrer, and content-security headers.

For Docker access, map the configured port explicitly, for example `8080:8080`. Do not expose the service publicly without TLS and the required token.
