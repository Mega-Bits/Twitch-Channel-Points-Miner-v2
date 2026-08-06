# Completed explicit game Drops

The catalogless `drop_games` fallback searches Twitch's game directory with the `DROPS_ENABLED` filter when the personal campaign catalog is unavailable. Twitch can continue returning channels after the current account has already completed every reward for the active campaign.

Version 2.4.2 combines the live inventory check with restart-safe completion evidence and stable provisional channel selection.

## Terminal inventory states

The game-directory fallback is skipped when the matching inventory campaign has no reward that still needs watching. This includes:

- campaign status `COMPLETED`, `CLAIMED`, or `EXPIRED`;
- every reward already claimed;
- a completed reward that only needs claiming and therefore does not require additional watching;
- no remaining reward that can finish before the campaign or Drop deadline.

The normal inventory claim path remains responsible for claiming completed rewards.

## Restart-safe terminal ledger

Twitch removes fully completed campaigns from `dropCampaignsInProgress`. Version 2.4.1 therefore lost the only terminal evidence after a restart and could start the game-directory fallback again.

Version 2.4.2 stores terminal campaign records beside the account cookie file in:

```text
<account>.drop-terminal-state.json
```

The record retains the campaign ID, game, terminal reason, and expiry deadline. It survives container restarts but automatically expires after the campaign end time plus the existing grace period.

For accounts upgrading after a campaign was already removed from inventory, the miner also reads the persisted Discord dashboard claim history. Fully completed `100%` claims for the game create a bounded migration tombstone only when:

- the latest inventory sync contains no campaign for the game;
- no open tracked campaign exists for the game;
- the retained claim explicitly contains the game and completed progress.

One or two retained completed claims suppress the fallback for six hours after the latest claim. Three or more distinct completed claims suppress it for 24 hours. The deadline is based on the original claim timestamp, so restarting the miner does not extend it indefinitely.

A fully identified real campaign from Twitch always takes precedence. Exact terminal records are also bypassed immediately when Twitch advertises a different campaign ID for the same game.

## Stable provisional selection

A provisional game-directory channel stays selected while progress verification is pending, even when Twitch changes pagination or viewer ordering and temporarily omits that channel from the latest `DROPS_ENABLED` result page.

The miner restores the active candidate's directory mapping instead of marking it offline merely because it disappeared from that page. The channel changes only when:

- Twitch reports no Drop progress before `drop_progress_timeout`;
- the progress verifier explicitly rejects the channel;
- the channel genuinely goes offline;
- the channel changes game;
- a real campaign handoff selects a campaign-specific channel;
- persisted or current inventory evidence proves the game is complete or no longer finishable.

This prevents the first slot from alternating between `Game drop` and `Priority` during ordinary directory refreshes.

## Example logs

After migrating existing dashboard claims:

```text
Skipping game-directory Drop fallback for Rust because persisted completion
evidence is terminal: 5 fully completed Drop claim(s) remain in the persisted
dashboard while Twitch no longer lists an open inventory campaign. Rechecking
automatically after 2026-08-07T13:10:00+00:00 or immediately when a real new
campaign is discovered
```

For an exact campaign retained from inventory:

```text
Skipping game-directory Drop fallback for Rust because inventory is terminal:
Global Warfare 4: completed. Rechecking automatically; this guard expires at
2026-08-06T16:10:00+00:00 and is bypassed immediately when Twitch advertises a
different campaign ID
```

No additional configuration is required.
