# Authoring a toolkit

Two ways in, depending on where you're starting:

- **[Start from scratch](from-scratch.md)**: your first toolkit. `tb init`
  scaffolds a template; you write tools and declare them.
- **[From existing tools](existing-tools.md)**: you already have a codebase of
  Orchestral `@define_tool` functions. `tb ingest` discovers them and writes
  the `toolkit.yaml` for you, without moving your code.

Both paths then share the same steps:

1. [Config & setup](config-and-setup.md): values the user supplies, gated
   bundles, heavier setup.
2. [Validate & publish](publish.md): validate, authenticate, ship.

```
init  /  ingest  →  (config, bundles)  →  validate  →  login  →  publish
```

Develop against a live install instead of publish→install round-trips with
`tb install -e . -a` (see
[editable installs](../guides/multi-version-and-editable.md)).
