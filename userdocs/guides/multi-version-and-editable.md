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
  - 1.5.0 * <-  (used 1 hour ago, 40 MB)
  - 1.4.0       (used yesterday, 39 MB)
  serving 1.5.0 (pinned to 1.5.0)
```

`<-` marks the version that actually serves and the line below says why;
`*` marks the pinned one. They differ when nothing is pinned — then the
highest installed wins by default, and `tb list` says so rather than
leaving you to infer it.

Which version serves: the pin in the active project's manifest if there
is one — with the machine-local layer (`manifest.local.yaml`, gitignored)
overriding the committed `manifest.yaml` name-by-name — otherwise the
highest installed.

## Switch versions

```bash
tb use calculator@1.4.0   # serve 1.4.0 instead
tb use calculator         # clear the pin; highest installed wins again
```

`tb use` only writes the pin — no download, no environment rebuild. Both
slots stay in the cache, so switching back is another one-liner. It takes
effect the next time `tb serve` starts, so restart your agent session.

Scope mirrors `tb install`: `-u` (the default) chooses for the global
default-project, `-p` chooses for this project only.

```bash
tb use -p calculator@1.4.0   # pin 1.4.0 in <repo>/.toolbase/manifest.yaml
```

The project now serves 1.4.0 even if a newer version is installed globally.
See [Projects & teams](projects-and-teams.md).

If a pin names a version you've since removed, serve skips the toolkit
entirely rather than quietly running a different one. `tb list` reports
that as `not served`, with the versions you do have.

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
one-line fix. `tb use calculator@editable` restores it (that choice also
goes to the local layer, for the same reason). For the authoring loop, see
[Authoring → Validate & publish](../authoring/publish.md).

## Next

- [Projects & teams](projects-and-teams.md): version pinning across a team
- [Install & activate](install-and-activate.md): the basic loop
