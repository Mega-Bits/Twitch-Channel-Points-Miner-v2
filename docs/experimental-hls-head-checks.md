# Optional HLS HEAD checks (experimental)

The channel watch loop already obtains Twitch's HLS playback token, requests the master and media playlists, sends an HLS segment HEAD request and then sends the Spade `minute-watched` event.

This change makes playlist parsing resilient to trailing blank lines and HLS tags. It also adds an **opt-in** comparison mode based on [DevilXD's TwitchDropsMiner change](https://github.com/DevilXD/TwitchDropsMiner/commit/f693b0ca41301ac2282f3978713479efebe0490e).

## Configuration

Use an environment variable (for Docker Compose, add it to the miner service's `environment` section):

- `TWITCH_HLS_HEAD_MODE=single` (default): request only the latest valid segment, then send the existing Spade event.
- `TWITCH_HLS_HEAD_MODE=all`: request up to 15 listed HLS segments in playlist order, then send the existing Spade event.

For example:

```yaml
services:
  miner:
    environment:
      TWITCH_HLS_HEAD_MODE: "all"
```

No setting changes are needed to retain the default behavior. Both modes keep the existing two-slot selector, 20-second iteration schedule, Spade telemetry, and watch-streak tracking. Requests use the existing 20-second per-request timeout, so slow HEAD responses can delay processing; the experimental mode may generate substantially more requests.

## Validation before enabling generally

1. Confirm default mode starts and sends successful HEAD and Spade requests.
2. Enable `all` in a controlled test; inspect status codes and request timeouts for both watch slots.
3. Verify **real** Channel Points, watch streaks and Drops inventory progress. HTTP 200 and Spade 204 alone do not prove credit.
4. Roll back instantly by removing the environment variable or setting it to `single`.

This experiment has not been live-tested against Twitch. Twitch platform behavior and terms may change.
