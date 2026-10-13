# Raspberry Pi (32-bit ARMv7) container builds

The supported GHCR build matrix includes **linux/amd64**, **linux/arm64**,
and **linux/arm/v7**. The ARMv7 target remains supported for 32-bit Raspberry
Pi OS installations.

## Why older ARMv7 builds timed out

On GitHub's x86 runners, ARMv7 builds use QEMU. An unrestricted `pandas`
dependency forced a large native `pandas`/NumPy source build on ARMv7, spending
over 30 minutes in build-dependency preparation and metadata processing before
the 40-minute GitHub Actions timeout. The previous Dockerfile also compiled
legacy `cryptography==3.3.2` on ARMv7.

The miner's AnalyticsServer web routes only need basic filtering and sorting
of existing JSON records, so these operations now use the Python standard
library. The no-stream chart fallback also handles empty history without
crashing. The legacy standalone `aggregate()` helper still supports pandas
if that package has been explicitly installed.

`requirements.txt` and the default `setup.py` runtime dependencies no longer
install `pandas` or the development-only `pre-commit` tool. Optional package
extras in `setup.py` keep those available for users who explicitly need them.
The Dockerfile keeps the small compiler toolchain needed for Pillow on ARMv7,
then removes build-only packages.

## How to verify

Pull requests modifying the Dockerfile or runtime dependencies run the
`Raspberry Pi ARMv7 Docker smoke test` workflow. It builds
`linux/arm/v7`, starts an ARMv7 container under QEMU, checks that pandas is
not installed, and imports the AnalyticsServer routes.

The regular `Build and publish container` workflow continues to publish a
multi-architecture manifest **only after all three architecture builds pass**.
Do not consider an individual AMD64/ARM64 success proof that the Pi image was
released.

To verify the architecture of a published image:

```sh
docker buildx imagetools inspect ghcr.io/mega-bits/twitch-channel-points-miner-v2:latest
```

Look for `linux/arm/v7` in the manifest. On a 32-bit Pi, Docker pulls that
variant automatically from the multi-platform tag.
