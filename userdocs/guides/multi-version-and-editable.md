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

**`tb install` never writes a pin.** It puts a version in the cache and
stops; `tb use` is the only command that chooses. So installing an older
version does not switch to it — install says so when that happens:

```console
$ tb install calculator@1.2.0
✓ Successfully installed calculator v1.2.0
Note: 1.4.0 is what serves here (highest installed, no pin), not the
1.2.0 you just installed.
  To use it: tb use calculator@1.2.0
```

`tb use` takes the scope keys: `-u` (the default) chooses for the
user-level default-project, `-p` for this project, `--private` for this
project's gitignored layer.

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

**An editable slot outranks every numbered version**, so your checkout
serves as soon as you link it — no pin needed. It writes no manifest
either.

Two things follow. First, the cache is user-level, so an editable slot
serves in *every* directory, not only the repo you're developing in;
`tb list` and serve startup say so, because a checkout linked months ago
would otherwise keep serving with nothing to indicate it. Second, an
explicit pin still wins:

```bash
tb use calculator@1.4.0   # a pinned loadout is safe from your checkout
tb use calculator         # clear it; the checkout serves again
```

That's what keeps a pinned profile reproducible while someone has that
toolkit checked out. For the authoring loop, see
[Authoring → Validate & publish](../authoring/publish.md).

## Next

- [Projects & teams](projects-and-teams.md): version pinning across a team
- [Install & activate](install-and-activate.md): the basic loop
