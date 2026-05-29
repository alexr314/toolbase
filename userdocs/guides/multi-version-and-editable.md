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

Which version serves: the one pinned in the active project's manifest if
there is one, otherwise the highest installed. `*` marks the project-pinned
version.

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

Editable installs aren't pinned into the committed manifest (the path is
machine-specific). For the authoring loop, see
[Authoring → Validate & publish](../authoring/publish.md).

## Next

- [Projects & teams](projects-and-teams.md): version pinning across a team
- [Install & activate](install-and-activate.md): the basic loop
