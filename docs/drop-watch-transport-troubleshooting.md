# Twitch Drop watchtime: HLS / Spade diagnostics

**Channel Points are not proof of Twitch Drop progress.** Channel Points
events arrive over Twitch PubSub; Drop minutes depend on a valid watch
session and the account/Drop campaign being eligible.

## What changed

With `TWITCH_HLS_HEAD_MODE=auto` (now the default), the authoritative
primary `game_drop` or `drop_completion` watch slot checks up to 15 recent
HLS segments per 20-second cycle. The regular Channel Points slot keeps its
lightweight single-segment HEAD. This is closer to the HLS method used by the
upstream TwitchDropsMiner than the earlier one-segment default.

`TWITCH_HLS_HEAD_MODE=single` restores the previous minimal transport.
`TWITCH_HLS_HEAD_MODE=all` probes multiple segments for *both* watch slots,
which increases HTTP traffic and is not recommended by default on a Pi.

Both HLS and the legacy Spade `minute-watched` request remain in use.
By default both watch slots and Channel Points behavior are preserved.

If progress is still stuck while HLS+Spade transport succeeds, an **opt-in**
isolation test is available:

```yaml
environment:
  TWITCH_DROP_EXCLUSIVE_WATCH: "true"
```

With this enabled, **only one Twitch watch session is active when a Drop
campaign is selected**; the second watch slot is unused rather than alternating
Spade/playlist requests for two channels. This can test whether a second
concurrent watch session is displacing Twitch Drop credit. Note that Channel
Points or Watch Streaks on the second channel may be reduced while enabled.
Disable the option to restore normal two-slot operation. The option has no
effect when no Drop is selected. Twitch determines credited watchtime; this
option is diagnostic, not a guaranteed remedy.

The miner now logs throttled warnings (no more than once every five minutes
per channel/stage) if a playback token, master/variant HLS playlist, segment
HEAD request or Spade POST fails or returns the wrong status. It never logs
signed HLS URLs, auth tokens or raw responses in these diagnostics.

A healthy Drop watch channel reports a status line every 15 minutes:

```
Twitch watch transport accepted for Drop channel CHANNEL: 12 HLS HEAD(s) and Spade HTTP 204; verify credited Drop minutes in Twitch inventory
```

This means the **HTTP transport succeeded**, *not* that Twitch credited the
Drop. If this line appears repeatedly while Drop progress remains stalled,
inspect the campaign's Twitch Drops Inventory for account linking, eligible
channels and whether another device/browser is watching streams on the same
account.

## Raspberry Pi troubleshooting

If the log says `Twitch watch transport failed ... Spade` or HLS, check the
Pi's DNS resolver, firewall and optional network filters such as Pi-hole,
NextDNS or AdGuard. In a confirmed upstream case, DNS blocking
`spade.twitch.tv` prevented mining while the application otherwise appeared
functional.

A simple DNS check on the Pi:

```sh
getent ahosts spade.twitch.tv
```

A DNS lookup alone does not prove the actual playback/claim transaction
works. If using a DNS blocklist, temporarily allow Twitch's Spade service
and the stream's CDN to test. Do not share cookie files, OAuth tokens or
signed HLS URLs in issue reports.

## What to verify

1. Start with only one active Twitch account watching via the miner; stop
   competing Twitch playback in other browsers/devices temporarily.
2. Watch a campaign that is visible in Twitch Drops Inventory, on a channel
   eligible for that **specific** campaign (a general `DROPS_ENABLED` tag
   alone is insufficient).
3. Compare that campaign's `currentMinutesWatched` 15–20 minutes apart.
   If it stays unchanged despite healthy transport, Twitch has not credited
   watchtime: this cannot be deduced from Channel Points or HTTP 204.
4. Recheck the log for repeated `HLS segment HEAD`, `Spade`, or
   `PlaybackAccessToken` warnings; they identify which request is blocked.

Rust's catalog-only discovery is a best-effort fallback: if Twitch doesn't
provide an eligible campaign or progress after trying three channels, its
existing 24-hour circuit breaker still applies. This avoids endlessly
watching an ineligible game.

## Verification

Automated tests cover the auto/single/all modes, a real simulated two-slot
minute-watcher iteration, Spade non-204 logging and log rate-limiting. The
GitHub Actions PR test workflow applies the same runtime Twitch patch files
as the Docker image before executing the tests.
