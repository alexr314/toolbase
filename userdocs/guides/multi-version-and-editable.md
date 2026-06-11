# Multi-version & editable installs

## Multiple versions

Versions coexist in the cache:

```bash
tb install calculator@1.4.0
tb install calculator@1.5.0
tb list
```

```console
✓ calculator   (active)
  - 1.5.0 *  (used 1 hour ago, 40 MB)
  - 1.4.0    (used yesterday, 39 MB)
```

Which version serves: the pin in the active project's manifest if there
is one — with the machine-local layer (`manifest.local.yaml`, gitignored)
overriding the committed `manifest.yaml` name-by-name — otherwise the
highest installed. `*` marks the pinned version.

## Pin a version to a project

```bash
tb install -l calculator@1.4.0   # pin 1.4.0 in <repo>/.toolbase/manifest.yaml
```

The project now serves 1.4.0 even if a newer version is installed globally.
See [Projects & teams](projects-and-teams.md).

## Editable installs (developing a toolkit)

Symlink a local source dir into the cache so edits are live, the
`pip install -e .` of toolbase:

```bash
cd my-calculator
tb install -e . -a            # symlink + activate
# edit tools/, restart the agent session — changes are live
```

```bash
tb install -e .               # rebuild the env after changing dependencies
```

Editable installs pin `editable` into `manifest.local.yaml` — the
gitignored machine-local layer, never the committed manifest (the slot
points at *your* checkout; no other machine has it). Without that pin an
editable slot would lose version resolution to any numbered slot; if
that ever happens (e.g. after deleting the local layer), `tb list` and
serve startup warn that the editable slot is shadowed and show the
one-line fix. For the authoring loop, see
[Authoring → Validate & publish](../authoring/publish.md).

## Next

- [Projects & teams](projects-and-teams.md): version pinning across a team
- [Install & activate](install-and-activate.md): the basic loop
