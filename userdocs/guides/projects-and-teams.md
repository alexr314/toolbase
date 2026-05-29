# Projects & teams

Pin toolkits, curation, config, and harness wiring into a repo so the setup
travels with it. `-g` (the default) is user scope: you, everywhere. `-l` is
project scope: this repository, committed.

## Pin toolkits to the project

```bash
tb install -l calculator      # install + pin into <repo>/.toolbase/manifest.yaml
tb install -l units           # one toolkit per command
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

The manifest records which version the project uses; serve respects that pin.
Binaries live once in the shared cache. Only the pin is project-scoped.

## Curate for the project

`-l` targets the project's profile instead of your user one:

```bash
tb activate -l calculator/basic
tb activate -l calculator/scientific
tb activate -l units/convert
```

## Configure for the project

Config uses `--project` / `--user` (project overrides user):

```bash
tb config set calculator precision 10 --project   # committed, shared
tb config set calculator cas_path /opt/sympy --user  # private, your machine
```

## Wire the harness for the team

```bash
tb connect claude-code -l     # writes <repo>/.mcp.json (committed)
```

## Commit

```
<repo>/.toolbase/
  manifest.yaml            # pinned toolkits + versions
  serve.yaml               # default.profile + blocklists
  config/<toolkit>.yaml    # shared, non-secret config
  profiles/default.yaml    # the project's curated tool set
<repo>/.mcp.json           # harness wiring (Claude Code)
```

Commit all of `.toolbase/` and `.mcp.json`. Keep per-user secrets in your user
layer (`~/.toolbase/config/<toolkit>.yaml`), not in the repo.

## Reproduce on a clone

```bash
git clone <repo> && cd <repo>
pip install toolbase
tb install calculator@1.4.0   # install each toolkit the manifest pins
tb install units@0.9.0
# supply any private secrets (e.g. cas_path) in the user layer, then open the agent
```

!!! note
    There's no one-command "install everything in the manifest" yet. Install
    the pinned toolkits explicitly (the versions are listed in
    `.toolbase/manifest.yaml`). The committed profile and project config mean
    the agent then sees exactly what the project intends.

## Next

- [Profiles](profiles-power-user.md): multiple named profiles per project
- [Multi-version & editable](multi-version-and-editable.md): version pinning in depth
