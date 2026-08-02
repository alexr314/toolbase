# Projects & teams

Pin toolkits, curation, config, and harness wiring into a repo so the setup
travels with it. Inside a project (any directory with a `.toolbase/`),
toolbase writes there by default. Use `-u` (config: `--user`) to target your
user-wide layer instead.

## Pin toolkits to the project

```bash
tb install calculator         # into the shared cache; writes no manifest
tb install units
tb use -p calculator@1.4.0    # pin 1.4.0 for this repo
tb use -p units@0.9.0
```

```yaml
# <repo>/.toolbase/manifest.yaml
schema_version: 1
toolkits:
  - name: calculator
    version: 1.4.0
  - name: units
    version: 0.9.0
```

`install` puts binaries in the user-level cache, shared across projects, and
takes no scope — it never writes a manifest. `tb use` is the only command
that pins, so every entry in the file above is one somebody chose. Without a
pin, the newest installed version serves (an editable checkout ahead of all
of them), which is usually what you want.

## Curate, configure, and wire

Inside the repo these default to the project, so no flags are needed:

```bash
tb activate calculator/basic           # project profile
tb config set calculator precision 10  # project config (committed, shared)
tb connect claude-code                 # writes <repo>/.mcp.json (committed)
```

Reach for the user-wide layer with `-u` (config: `--user`) when something
shouldn't be committed, like a private secret:

```bash
tb config set calculator cas_path /opt/sympy --user   # private, your machine
```

## Commit

```
<repo>/.toolbase/
  manifest.yaml            # pinned toolkits + versions
  serve.yaml               # default.profile + blocklists
  config/<toolkit>.yaml    # shared, non-secret config
  profiles/default.yaml    # the project's curated tool set
<repo>/.mcp.json           # harness wiring (Claude Code)
<repo>/toolkits.yaml       # optional import file (see below)
```

Commit all of `.toolbase/` and `.mcp.json`. Keep per-user secrets in your user
layer (`~/.toolbase/config/<toolkit>.yaml`), not in the repo. Pins that are
only true on this machine go in `.toolbase/manifest.local.yaml` via
`tb use --private`, which auto-gitignores itself — commit the dependency, not
your local resolution. An editable checkout needs no pin at all: it outranks
numbered versions on its own.

## Reproduce on a clone

Commit an **import file** listing the project's toolkits and a fresh
machine provisions with one command:

```yaml
# <repo>/toolkits.yaml
toolkits:
  - name: calculator          # registry install
    version: 1.4.0
    bundles: [basic]          # optional subset
  - name: units
    version: 0.9.0
  - source: ../my-private-kit # path install (relative to this file)
    editable: true            # live symlink for dev machines
```

```bash
git clone <repo> && cd <repo>
pip install toolbase
tb install toolkits.yaml
# supply any private secrets (e.g. cas_path) in the user layer, then open the agent
```

Entries use `name:` (registry) or `source:` (a path — including an
exported tarball from `tb export`, the registry-free way to move private
toolkits between machines). The committed profile and project config
mean the agent then sees exactly what the project intends.

## Next

- [Profiles](profiles-power-user.md): multiple named profiles per project
- [Multi-version & editable](multi-version-and-editable.md): version pinning in depth
