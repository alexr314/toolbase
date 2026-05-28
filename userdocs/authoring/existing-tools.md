# From existing tools

Already have a codebase of Orchestral `@define_tool` functions (or `BaseTool`
subclasses)? `tb ingest` turns it into a toolkit without moving your code.

## Ingest

From your project root:

```bash
tb ingest
```

It walks the directory, discovers tools by **static analysis** (it never
imports your modules), and writes a `toolkit.yaml` that lists each tool by its
import path. Your code stays where it is.

```bash
tb ingest path/to/code     # scan a specific directory (default: cwd)
tb ingest -o toolkit.yaml  # choose where to write
```

## Fill in the metadata

`ingest` writes the `tools:` list; add the toolkit's identity at the top of
`toolkit.yaml` — `name`, `version`, `author`, `description` (see
[Schemas](../reference/schemas.md)).

## Re-syncing

Run `tb ingest` again any time. With an existing `toolkit.yaml` it **merges**:
new tools are appended, and your edits (descriptions, `bundle:`, ordering,
comments) are preserved.

```bash
tb ingest            # merge newly-added tools
tb ingest --prune    # also remove entries whose source is gone
tb ingest --force    # rebuild the file from scratch
```

The dev loop:

```bash
tb install -e .      # editable install of this toolkit
# ... add a @define_tool ...
tb ingest            # merge it into toolkit.yaml
```

## Next

- [Config & setup](config-and-setup.md) — group tools into bundles, add config
- [Validate & publish](publish.md)
