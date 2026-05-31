# Install & activate

Get a toolkit onto your machine and expose it to the agent.

!!! abstract "Running example"
    The guides use a toy `calculator` toolkit (bundles `basic`, `scientific`,
    `symbolic`) and a companion `units` toolkit, stand-ins for whatever you
    actually install.

## Install

```bash
tb install calculator           # latest
tb install calculator@1.4.0     # a specific version
```

Installing builds the toolkit an isolated environment in the shared global
cache (`~/.toolbase/cache/`), available to every project on your machine. It
does **not** serve it. That's `activate`.

### Install only some bundles

For toolkits that declare bundles with their own dependencies, you can
install a subset to skip the heavy deps you don't need (pip-extras style):

```bash
tb install calculator[basic]                 # just the 'basic' bundle
tb install calculator[basic,symbolic]        # two bundles
tb install calculator --bundle basic         # same as above, flag form
```

Subsequent installs are **additive** (pip-like) — adding `[symbolic]` later
pip-installs the new bundle's deps on top of the existing venv without
rebuilding:

```bash
tb install calculator[basic]      # fresh install, just basic
tb install calculator[symbolic]   # adds symbolic; venv untouched
```

To scope back down or wipe the slot, use `--rebuild`:

```bash
tb install calculator[basic] --rebuild   # destructive: only 'basic' remains
```

Tools in non-installed bundles are silently skipped at serve time with one
log line per toolkit (`tb logs` to see them).

## Activate

```bash
tb activate calculator
```

```console
✓ Activated calculator (whole toolkit).
```

Activation is **project-local by default**: it writes a profile under the
current directory's `.toolbase/` (creating it if needed), so the toolkit is
exposed only when you work here. Add `-g` to activate it **user-wide** (every
session, any directory) instead:

```bash
tb activate calculator        # this project only (creates ./.toolbase/)
tb activate calculator -g     # user-wide
```

`tb install calculator -a` installs and activates in one step, following the
same rule: project-local by default, `-g` for user-wide. The binary still
lands in the shared global cache either way; only the activation is scoped.

## See what you have

```bash
tb list
```

```console
Active profile: default

✓ calculator   1.4.0   (active)
✗ units        0.9.0   (inactive)
```

`tb list -v` adds a per-tool view (see [Curating tools](curating-tools.md)).

## Update & uninstall

```bash
tb install calculator@1.5.0   # newer version, alongside 1.4.0
tb uninstall calculator       # remove it
```

## Next

- [Curating tools](curating-tools.md): serve only the bundles/tools you want
- [Configuring toolkits](configuring-toolkits.md): API keys, paths
- [Connecting harnesses](connecting-harnesses.md): wire toolbase into your agent
