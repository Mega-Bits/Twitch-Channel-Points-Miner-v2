# Twitch Channel Points Miner v2 — Mega-Bits fork

[![Current release](https://img.shields.io/github/v/release/Mega-Bits/Twitch-Channel-Points-Miner-v2?logo=github)](https://github.com/Mega-Bits/Twitch-Channel-Points-Miner-v2/releases)
[![Container](https://img.shields.io/badge/GHCR-twitch--channel--points--miner--v2-blue?logo=docker)](https://github.com/Mega-Bits/Twitch-Channel-Points-Miner-v2/pkgs/container/twitch-channel-points-miner-v2)
[![Last commit](https://img.shields.io/github/last-commit/Mega-Bits/Twitch-Channel-Points-Miner-v2?logo=github)](https://github.com/Mega-Bits/Twitch-Channel-Points-Miner-v2/commits/master)

This fork watches configured Twitch channels, collects Channel Points, claims eligible Drops and Moments, follows raids, and can participate in predictions. It also adds a deterministic two-slot watch selector, game-based Drop discovery, persistent Watch Streak state, and a persistent Discord status dashboard.

## Fork features

- Game-based Drop farming through `drop_games`.
- Optional completion of already-started, unexpired inventory Drops outside `drop_games`.
- Main-list streamers are preferred over directory fallbacks and remain selected while eligible.
- One dedicated Drop slot; the second slot follows the configured priority.
- Persistent local Watch Streak state across container restarts during the same broadcast.
- A single editable Discord dashboard message using its own optional webhook.
- Discord-native timestamps for local time-zone rendering.
- Separate `START_WATCHING` and `STOP_WATCHING` notification events.
- Multi-architecture GHCR images for `linux/amd64`, `linux/arm64`, and `linux/arm/v7`.
- Independent Semantic Versioning sourced from `TwitchChannelPointsMiner/VERSION`.

Detailed documentation:

- [Events and fork features](docs/events-and-features.md)
- [Discord status dashboard](docs/status-dashboard.md)
- [Game Drops and persistent Watch Streak state](docs/drop-games-watch-streak.md)
- [Versioning and container releases](docs/versioning.md)
- [Complete configuration example](example.py)

## Quick start

Create a runner based on `example.py`, then install and run:

```bash
python -m pip install -r requirements.txt
python run.py
```

The project stores cookies and local state under `cookies/`. Keep that directory persistent when running in a container.

## Docker / GHCR

Development images from `master`:

```bash
docker pull ghcr.io/mega-bits/twitch-channel-points-miner-v2:edge
```

Stable releases:

```bash
docker pull ghcr.io/mega-bits/twitch-channel-points-miner-v2:latest
docker pull ghcr.io/mega-bits/twitch-channel-points-miner-v2:2.1.0
```

`latest` and Semantic Version tags are published only from matching Git tags such as `v2.1.0`. Every master build also receives `master`, `edge`, and `sha-<commit>` tags.

A minimal Compose service can use:

```yaml
services:
  miner:
    image: ghcr.io/mega-bits/twitch-channel-points-miner-v2:latest
    restart: unless-stopped
    volumes:
      - ./run.py:/usr/src/app/run.py:ro
      - ./cookies:/usr/src/app/cookies
      - ./logs:/usr/src/app/logs
```

## New configuration options

### Dedicated Discord dashboard webhook

```python
Discord(
    webhook_api="https://discord.com/api/webhooks/EVENTS/WEBHOOK",
    dashboard_webhook_api="https://discord.com/api/webhooks/DASHBOARD/WEBHOOK",
    events=[
        Events.START_WATCHING,
        Events.STOP_WATCHING,
        Events.DROP_CLAIM,
    ],
)
```

`webhook_api` receives normal event messages. `dashboard_webhook_api` receives only the persistent dashboard. An empty dashboard webhook disables dashboard publishing.

### Farm Drops by game

```python
twitch_miner.mine(
    streamers=["otzdarva", "deadbydaylight"],
    drop_games=["Dead by Daylight", "Overwatch 2"],
    drop_game_limit=10,
    finish_started_drops=True,
)
```

At every inventory sync, configured streamers are refreshed and checked first. The game directory is queried only when no configured streamer is online, playing the correct game, and eligible for the campaign.

`finish_started_drops=True` additionally completes active campaigns Twitch already lists as in progress, even when their game is not present in `drop_games`. It ignores untouched and expired campaigns, uses the same dedicated Drop slot and campaign lock, and claims completed rewards through the normal inventory path. The option defaults to `False`.

## Notification events

Common events include:

```python
Events.START_WATCHING
Events.STOP_WATCHING
Events.STREAMER_ONLINE
Events.STREAMER_OFFLINE
Events.GAIN_FOR_WATCH
Events.GAIN_FOR_WATCH_STREAK
Events.DROP_CLAIM
Events.DROP_STATUS
Events.BET_WIN
Events.BET_LOSE
Events.CHAT_MENTION
```

The old separate `Miner started` Discord notification is no longer generated. Startup state is shown in the persistent dashboard and normal logs.

## Versioning

The single version source is:

```text
TwitchChannelPointsMiner/VERSION
```

Use Semantic Versioning in the form `MAJOR.MINOR.PATCH` or an optional prerelease such as `2.2.0-rc.1`. A stable container release requires a matching Git tag:

```bash
printf '2.1.0\n' > TwitchChannelPointsMiner/VERSION
git commit -am "Release 2.1.0"
git tag v2.1.0
git push origin master v2.1.0
```

The workflow rejects a tag whose version does not match the version file.

## Credits

This project is based on the work of the original Twitch Channel Points Miner contributors, including Tkd-Alex and rdavydov. Historical attribution is retained while active runtime links, releases, update checks, and container images point to the Mega-Bits fork.

## Disclaimer

Use the project at your own risk. Twitch can change APIs, eligibility rules, and platform behavior at any time. Respect Twitch's Terms of Service and applicable laws.
