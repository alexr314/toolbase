# Validate & publish

## Validate

```bash
tb validate
```

Checks structure and `toolkit.yaml`: tool entries, bundles, config schema,
cross-references (a tool's `bundle` must be declared; a bundle's `requires`
keys must exist in `config`). Fix everything here before publishing.

## Authenticate

```bash
tb login         # browser flow; stores a per-user token
tb whoami        # check who you're signed in as
tb logout
```

## Publish

```bash
tb publish --dry-run    # validate + package, no upload — always run this first
tb publish              # package + upload to the registry
```

`publish` registers the name on first run (no separate step). It blocks
"version already exists" and "version decrease"; override the latter only
deliberately with `--allow-version-decrease`. Bump `version` in `toolkit.yaml`
for each release.

To reserve a name without uploading code yet:

```bash
tb create my-toolkit -c <category> -d "<description>"
```

## Iterate

Develop against a live install instead of publish→install round-trips:

```bash
tb install -e . -a      # symlink this source into the cache + activate
# edit tools/, restart the agent session — changes are live
```

See [Multi-version & editable](../guides/multi-version-and-editable.md).
