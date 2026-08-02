# Changelog

All notable changes to the Mega-Bits fork are documented here.

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
