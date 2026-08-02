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

Which version serves: the `version:` on that toolkit's entry in the
active loadout if there is one — with the private layer
(`<name>.local.yaml`, gitignored) overriding the committed loadout
toolkit-by-toolkit — otherwise the highest installed.

Versions live in the loadout beside the tool selection, so one file says
both which tools an agent gets and which build of them:

```yaml
# .toolbase/loadouts/default.yaml
toolkits:
  calculator:
    version: 1.4.0        # omit to take the newest installed
    bundles: [basic]
```

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
tb use -p calculator@1.4.0   # pin 1.4.0 in this project's loadout
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

**Linking a checkout doesn't make it serve.** Like every other install,
`-e` writes no manifest: an `editable` slot loses to any numbered
version until you say otherwise.

```bash
tb use calculator@editable    # serve the checkout, here
tb use calculator             # stop; the newest numbered version serves
```

That's deliberate. The cache is user-level — one `editable` slot shared
by every directory on your machine — so if linking a checkout won by
default, one `tb install -e` would change what every agent session
everywhere runs, and confining it again would mean pinning numbered
versions in every *other* project. Opting in with `tb use` selects the
checkout exactly where you run it, and nowhere else. It also keeps a
pinned version safe while someone has that toolkit checked out.

If the toolkit has *only* an editable slot — the usual authoring case —
it serves with no pin, because there's nothing for it to lose to.

The cost is the "my edits do nothing" symptom, so all three places that
can see it say so. Install, the moment it links a losing checkout:

```console
$ tb install -e .
✓ Successfully installed calculator (editable)
Note: 1.4.0 is what serves here (highest installed, no pin), not the
editable checkout you just linked.
  To use it: tb use calculator@editable
```

`tb list` marks it (`⚠ your editable checkout is NOT what serves`), and
the serve startup banner repeats it. For the authoring loop, see
[Authoring → Validate & publish](../authoring/publish.md).

## Next

- [Projects & teams](projects-and-teams.md): version pinning across a team
- [Install & activate](install-and-activate.md): the basic loop
