# Changelog

All notable changes to the Mega-Bits fork are documented here.

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
