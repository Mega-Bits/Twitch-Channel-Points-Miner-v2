# Drop campaign build patches

The Docker image applies these patches during the build and runs `compileall` before publishing.

## Behavior

- Keep one in-progress Drop campaign locked until all remaining Drops in that campaign are claimed.
- Prefer a qualifying channel from the configured main list.
- When the main list has no qualifying channel, use `allow.channels` fallback entries.
- Poll active fallback channels once per campaign-sync cycle so channels that go live after startup are detected.
- After the locked campaign completes, deactivate its fallback entries and return to the main list.
- On the main list, select a Drop channel first and then fill remaining capacity using the configured `Priority` order.
- Prevent another Drop-enabled channel from taking the second slot while a campaign is locked; the second slot remains available for safe priority choices such as Watch Streaks.

Campaigns without `allow.channels` cannot create fallback streamers and are intentionally skipped when no configured channel qualifies.
