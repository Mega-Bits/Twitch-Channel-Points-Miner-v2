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
- the remote update check against the Mega-Bits `master` branch;
- Python package metadata in `setup.py`;
- OCI image labels and release tags in the GHCR workflow.

## Container tags

A push to `master` publishes moving development tags:

```text
edge
master
sha-<short commit>
```

A matching stable Git tag such as `v2.1.0` additionally publishes:

```text
2.1.0
v2.1.0
2.1
2
latest
sha-<short commit>
```

A prerelease such as `v2.2.0-rc.1` publishes its exact version tags but does not update `latest`, `2.2`, or `2`.

## Release procedure

1. Update `TwitchChannelPointsMiner/VERSION`.
2. Update the changelog and relevant documentation.
3. Merge the release commit into `master`.
4. Create and push a matching tag.

```bash
printf '2.1.0\n' > TwitchChannelPointsMiner/VERSION
git add TwitchChannelPointsMiner/VERSION CHANGELOG.md
git commit -m "Release 2.1.0"
git push origin master
git tag v2.1.0
git push origin v2.1.0
```

The workflow fails before building when the Git tag and version file differ.
