# Versioning and container releases

The Mega-Bits fork uses independent Semantic Versioning with one source of truth:

```text
TwitchChannelPointsMiner/VERSION
```

The initial fork release using this system is `2.1.0`.

## Version format

Accepted forms:

```text
2.1.0
2.2.0-rc.1
```

Build metadata with `+` is intentionally not accepted because it is not valid in Docker image tags without normalization.

Use the version components as follows:

- `PATCH`: fixes that preserve existing configuration and behavior;
- `MINOR`: backward-compatible features or new optional settings;
- `MAJOR`: incompatible configuration, storage, or behavior changes.

## What reads the version

The same file controls:

- `TwitchChannelPointsMiner.__version__`;
- the version printed at startup;
- the version displayed by the Discord dashboard;
- the remote update check against the Mega-Bits `master` branch;
- Python package metadata in `setup.py`;
- OCI image labels and release tags in the GHCR workflow.

## Container tags

A successful push to `master` publishes moving current-build tags:

```text
latest
edge
master
sha-<short commit>
```

`latest`, `edge`, and `master` therefore point to the newest successful build from the default branch. They may remain on the previous build while a multi-platform image is still being created.

A matching stable Git tag such as `v2.2.2` additionally publishes immutable and compatibility tags:

```text
2.2.2
v2.2.2
2.2
2
sha-<short commit>
```

The stable tag build also refreshes `latest`. A prerelease such as `v2.3.0-rc.1` publishes its exact version tags but does not update the stable major or minor tags.

## Updating a running container

A restart alone does not pull a changed image. Recreate the service after the GHCR workflow has finished:

```bash
docker compose pull
docker compose up -d --force-recreate
```

Or force a registry check in one command:

```bash
docker compose up -d --pull always --force-recreate
```

The Discord dashboard displays the active version and renderer identity, making stale images directly visible.

## Release procedure

1. Update `TwitchChannelPointsMiner/VERSION`.
2. Update the changelog and relevant documentation.
3. Merge the release commit into `master`.
4. Create and push a matching tag for immutable SemVer image tags.

```bash
printf '2.2.2\n' > TwitchChannelPointsMiner/VERSION
git add TwitchChannelPointsMiner/VERSION CHANGELOG.md
git commit -m "Release 2.2.2"
git push origin master
git tag v2.2.2
git push origin v2.2.2
```

The workflow fails before building when the Git tag and version file differ.
