# Changelog

All notable changes to the Mega-Bits fork are documented here.

## 2.3.0

### Added

- New opt-in `finish_started_drops` setting for `mine()` and `run()`.
- Active campaigns already present in Twitch's `dropCampaignsInProgress` can be completed even when their game is not listed in `drop_games`.
- Started unmonitored campaigns can temporarily use eligible configured streamers, campaign fallback channels, or the Drops-enabled game directory.

### Changed

- Started unmonitored campaigns are ordered by current progress and use the existing single-campaign Drop lock.
- Temporarily resumed games remain separate from the explicit `drop_games` configuration and continue to display `explicit game farming: no` in the Discord dashboard.

### Safety

- `finish_started_drops` defaults to `False`.
- Untouched campaigns outside `drop_games`, campaigns without remaining rewards, campaigns that have not started, and expired campaigns are ignored.

## 2.2.3

### Fixed

- `Events.START_WATCHING` and `Events.STOP_WATCHING` no longer add themselves to the configured Discord event list.
- Watch-selection events are sent to the Discord event webhook only when they are explicitly present in `Discord(events=[...])`.
- Omitting either watch event now reliably disables that notification without affecting the persistent dashboard or other integrations.

## 2.2.2

### Added

- The Discord dashboard now shows the active miner version and renderer identity.
- Dashboard startup logs report the active version and whether the enhanced renderer is loaded.
- New Drop claim events and retained dashboard claim entries include the campaign game when Twitch exposes a matching campaign or inventory association.

### Changed

- GHCR `latest` is updated on every successful `master` build together with `master` and `edge`.
- Stable SemVer, major, and minor image tags still require a matching Git release tag.

### Fixed

- Pulling `latest` after a merged dashboard change no longer leaves users on an older tagged image indefinitely.
- The game is no longer limited to active and queued Drop rows; it is also attached to newly captured Drop claims.

## 2.2.1

### Fixed

- Discord dashboard failures no longer print credential-bearing webhook URLs or tracebacks.
- Dashboard retry warnings now report only a sanitized failure reason such as `HTTP 503`, `request timed out`, or `connection error`.
- The existing exponential retry behavior remains active for temporary Discord and network failures.

## 2.2.0

### Added

- Current Channel Points for every streamer shown under `Currently watching`.
- The game name on each tracked Drop.
- An explicit indicator showing whether the Drop game is configured in `drop_games`.
- A dedicated `Last non-points event` dashboard field.

### Changed

- Discord dashboard writes are coalesced and limited to one request per 15 seconds.
- Identical dashboard payloads are no longer sent repeatedly.
- Discord `429` responses now honor `Retry-After` before another request is attempted.
- Network and Discord server failures use exponential retry backoff up to five minutes.
- The previous generic `Last event` field now tracks only non-points events, while points events remain in `Last points event`.

## 2.1.0

### Added

- Independent Semantic Versioning through `TwitchChannelPointsMiner/VERSION`.
- Version-derived GHCR release tags and OCI image labels.
- Dedicated optional Discord webhook for the persistent status dashboard.
- Discord-native timestamp correction using the dashboard webhook server clock.
- `START_WATCHING` and `STOP_WATCHING` notification events.
- Persistent Watch Streak state for the same broadcast.
- Game-based Drop discovery with sticky main-list streamer preference.
- Dedicated Drop slot and single-campaign progress locking.

### Changed

- Runtime identity and update checks now target the Mega-Bits repository.
- Master builds use `edge`, `master`, and immutable SHA tags.
- Stable Semantic Version tags and `latest` are published only by a matching `v<version>` Git tag.
- `example.py` documents the new Discord and Drop settings.

### Removed

- The separate `Miner started` Discord notification.
