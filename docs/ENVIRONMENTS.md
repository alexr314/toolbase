# Environments and Scoping in Toolbase

**Status:** Shipped in 0.5.0.
**Audience:** Toolkit authors and toolkit users coming from 0.4.x, plus anyone using Toolbase across more than one project.
**Last revised:** 2026-05-13.

---

## TL;DR

0.5.0 splits "where toolkits live on disk" from "what this project uses":

- A **cache** at `~/.toolbase/cache/<name>/<version>/` holds installed toolkit binaries. Multi-version side-by-side. Regenerable, like `~/.cache/pip/`.
- A **project manifest** at `<project>/.toolbase/manifest.yaml` pins which version of each toolkit *this* project uses. Small, checked into git.
- **Per-toolkit config** lives in two layers: user-level at `~/.toolbase/config/<toolkit>.yaml` (defaults for all your work) and project-level at `<project>/.toolbase/config/<toolkit>.yaml` (overrides for one project). Project wins key-by-key, like `.env`.

The same toolkit can be installed at multiple versions for different projects without conflict. Sharing a project just means committing `.toolbase/`; collaborators run `tb install` and the cache rebuilds.

If you're upgrading from 0.4.x: see **[Migrating from 0.4.x](#migrating-from-04x)** at the bottom. You'll run `tb reset` once, then `tb install` for the toolkits you actually need.

---

## The mental model

Three pieces, three responsibilities:

| Piece | Path | Owner | Source of truth for |
|---|---|---|---|
| **Cache** | `~/.toolbase/cache/<name>/<version>/` | Toolbase | The bytes that get executed. Regenerable. |
| **Project manifest** | `<project>/.toolbase/manifest.yaml` | You (checked into git) | Which version of each toolkit *this project* uses. |
| **Config (two layers)** | `~/.toolbase/config/<toolkit>.yaml` + `<project>/.toolbase/config/<toolkit>.yaml` | You | Per-toolkit settings: secrets, paths, options. |

The mental shortcut:

- **The cache is like `~/.cache/pip/`.** It's a download cache. If you delete it, things still work — they just have to be rebuilt.
- **The manifest is like `requirements.txt`.** It's small, checked into git, describes what this project needs. If you delete it, the project loses its identity.
- **The two config layers are like `.env`.** User-level is your global defaults; project-level overrides on a per-key basis when you're in that project.

---

## File layout

```
~/.toolbase/                       # user-scope root
├── cache/                           # installed toolkit binaries (regenerable)
│   └── <name>/
│       └── <version>/               # multi-version slots side-by-side
│           ├── venv/                # or conda env, or docker ref
│           ├── tools/               # toolkit content
│           ├── toolkit.yaml
│           ├── requirements.txt
│           ├── .install_meta.yaml   # schema_version, install_method, installed_at
│           ├── .last_used           # ISO-8601 timestamp; touched by serve
│           └── .disk_size           # cached byte count (for tb list)
├── config/                          # user-level toolkit config (defaults)
│   └── <toolkit>.yaml               # schema_version: 1
├── default-project/                 # implicit project when cwd has no .toolbase/
│   ├── manifest.yaml
│   └── config/
│       └── <toolkit>.yaml
├── logs/
│   └── serve.log
├── serve.yaml                       # user-level serve config (groups, etc.)
└── config.json                      # login state (never touched by reset)

<project>/.toolbase/               # project-scope root (commit to git)
├── manifest.yaml                    # pinned toolkit list
└── config/                          # project-level config overrides
    └── <toolkit>.yaml
```

You'll rarely need to look inside `cache/`. The two paths you'll edit by hand are the user-level `config/<toolkit>.yaml` and your project's `manifest.yaml` / `config/<toolkit>.yaml`.

### Project discovery

When you run a command from inside a project (or any subdir of one), Toolbase walks upward looking for `.toolbase/manifest.yaml` and uses the first hit as the project root. If no manifest is found, it falls back to `~/.toolbase/default-project/` — that way `tb install` outside a project still works (it pins to the implicit global project).

Override with `--project-dir <path>` on any command if you need to (CI, debugging, scripting).

---

## The five common workflows

### 1. Install in a new project

```
$ cd ~/research/exoplanet-paper
$ tb project init
✓ Initialized toolbase project at /home/alex/research/exoplanet-paper
  Manifest: /home/alex/research/exoplanet-paper/.toolbase/manifest.yaml

$ tb install arxiv-search
[fetches metadata, downloads, builds venv, writes cache slot, pins manifest]

$ cat .toolbase/manifest.yaml
schema_version: 1
toolkits:
  - name: arxiv-search
    version: 0.2.0
    pinned_at: '2026-05-13T10:24:31'
```

`tb install` (no version) pins the latest. Commit `.toolbase/` to git so your collaborators get the same versions.

### 2. Install in an existing project (cloned from a teammate)

```
$ git clone https://github.com/alex/exoplanet-paper.git
$ cd exoplanet-paper
$ cat .toolbase/manifest.yaml
schema_version: 1
toolkits:
  - name: arxiv-search
    version: 0.2.0

$ tb install arxiv-search
[reads the manifest pin, installs exactly 0.2.0 into the cache]
```

You can also install everything pinned in the manifest at once (planned in 0.5.x; for now install one at a time).

### 3. Switch a project from one version to another

```
$ tb install arxiv-search@0.3.0
[adds a second cache slot at ~/.toolbase/cache/arxiv-search/0.3.0/]
[updates manifest pin to 0.3.0]

$ tb list
arxiv-search
  - 0.3.0 *   (used 2 seconds ago, 182 MB)
  - 0.2.0     (used 3 days ago, 180 MB)

* = pinned in this project
```

Both versions stay in the cache. Switch back any time with `tb install arxiv-search@0.2.0` — no re-download, no re-build; the slot is already there. The pin in your manifest is the only thing that moves.

### 4. Share a project (manifest in git, cache rebuilds)

```
$ git add .toolbase/manifest.yaml
$ git commit -m "Pin arxiv-search 0.3.0"
$ git push

# On your collaborator's machine:
$ git pull
$ tb install arxiv-search   # picks up the manifest pin
[fresh cache build for arxiv-search@0.3.0]
```

What's in git: the manifest, and optionally `project/.toolbase/config/<toolkit>.yaml` if it has shareable settings.

What's NOT in git: the cache (it's a build artifact), `config/<toolkit>.yaml` files that contain secrets (gitignore them, or only store secrets at the user layer).

### 5. One machine, many projects

This is the killer feature. Each project pins what it needs; the cache holds every version anyone has installed.

```
~/research/paper-a/.toolbase/manifest.yaml   # pins arxiv-search 0.2.0
~/research/paper-b/.toolbase/manifest.yaml   # pins arxiv-search 0.3.0
~/.toolbase/cache/arxiv-search/0.2.0/        # one shared slot
~/.toolbase/cache/arxiv-search/0.3.0/        # one shared slot
```

`cd paper-a && tb serve` runs 0.2.0. `cd paper-b && tb serve` runs 0.3.0. No environment switching, no reinstalls.

---

## The two-layer config story

Per-toolkit configuration lives in two layers:

- **User layer:** `~/.toolbase/config/<toolkit>.yaml` — your global defaults.
- **Project layer:** `<project>/.toolbase/config/<toolkit>.yaml` — overrides for this project only.

Resolution: the project layer wins key-by-key. Keys absent from the project layer fall through to the user layer.

### Worked example: ASTER

ASTER (the exoplanet toolkit) has two config fields:

- `opacity_path` — where the ~2.3 GB opacity files live on this machine.
- `api_key` — for the NASA Exoplanet Archive.

`opacity_path` is a machine-wide thing — every project on this laptop uses the same opacity files. Set it once at the user layer:

```
$ tb config set aster opacity_path /scratch/alex/aster/opacities --user
```

`api_key` is *probably* shared too. But you might have a separate key for your high-volume scratch project (rate-limited differently, billed differently). Override it per-project:

```
$ cd ~/research/high-volume-paper
$ tb config set aster api_key sct_npe_HIGHVOLUME_KEY
   (no --user flag → writes to project layer in this dir)
```

Now `tb serve` from `high-volume-paper/` uses the high-volume key. From any other dir, it falls back to the user-layer key (if set).

### `tb config show` reads the merged view

```
$ tb config show aster
opacity_path: /scratch/alex/aster/opacities  # from user
api_key:      <set>                          # from project
```

`--layer user` or `--layer project` shows just that one layer. `--layer` is the way to see the actual stored file, not the merged view.

### Where the project layer file lives

```
<project>/.toolbase/config/<toolkit>.yaml
```

It's a regular YAML file. You can edit it by hand:

```yaml
# .toolbase/config/aster.yaml
schema_version: 1
api_key: sct_npe_HIGHVOLUME_KEY
```

Note the sparse shape — only the keys that override appear. Keys absent here fall through to the user layer.

### Don't commit secrets

If your project layer holds secrets (`api_key`, etc.), gitignore the file:

```
# .gitignore
.toolbase/config/aster.yaml
```

The manifest itself (`.toolbase/manifest.yaml`) is safe to commit — it only has names and versions.

---

## `tb list` reading guide

```
$ tb list
arxiv-search
  - 0.2.0 *   (used 2 hours ago, 180 MB)
heptapod
  - 0.3.0 *   (used yesterday, 8.4 GB)
  - 0.1.0     (used 3 days ago, 8.2 GB)

* = pinned in this project (./.toolbase/manifest.yaml)
```

Reading row by row:

- **Toolkit name** at the top of each group.
- **Versions** indented underneath, one per line.
- **`*`** after a version means it's the pinned version in *this project*. The legend at the bottom tells you which manifest the `*` refers to.
- **`(used <delta>, <size>)`** — last time `tb serve` activated this version, and the cached disk size. `used never` means it's installed but hasn't been served.

`tb list --json` is the structured form:

```json
[
  {"name": "arxiv-search", "version": "0.2.0",
   "last_used_iso": "2026-05-13T08:41:23", "size_bytes": 188743680,
   "pinned_in_project": true},
  {"name": "heptapod", "version": "0.3.0", ...},
  {"name": "heptapod", "version": "0.1.0", ...}
]
```

Use the JSON form when scripting (`tb list --json | jq '.[] | select(.pinned_in_project)'`).

If you have nothing installed:

```
$ tb list
No toolkits installed. Try tb install arxiv-search
```

---

## Cache GC

Not in 0.5.0. The cache grows monotonically (each `tb install` adds; `tb uninstall` is the only thing that prunes). The thinking: visibility first via `tb list` + `.disk_size`, eviction policies later when bloat actually bites.

If your cache is too big right now, find the offenders with `tb list`, then `tb uninstall <name>` or `tb uninstall <name>@<version>` to prune. `tb reset --all` is the scorched-earth option (see below).

---

## Migrating from 0.4.x

0.4.x installed toolkits under `~/.toolbase/toolkits/<name>/` — flat, one slot per toolkit, no multi-version. 0.5.0 moved to `~/.toolbase/cache/<name>/<version>/`. **There is no auto-migration.** Existing 0.4.x installs surface a one-line heads-up and `tb` keeps working (the cache just looks empty until you reinstall).

### The cutover, in three commands

```
# 1. Detect that you have legacy installs.
$ tb list
[heads-up to stderr:]
Heads up: 0.5.0 changed the install layout. Toolkits installed under
~/.toolbase/toolkits/ are no longer used. Run `tb reset` to remove
them and reinstall the ones you need.

# 2. Clear the legacy directory (default mode — preserves cache/, config/,
#    default-project/, serve.yaml, logs/, config.json).
$ tb reset --dry-run
Dry-run: the following would be removed
  toolkits/ (legacy 0.4.x layout)
    /home/alex/.toolbase/toolkits

$ tb reset
This will remove the legacy 0.4.x layout:
  toolkits/ (legacy 0.4.x layout)
    /home/alex/.toolbase/toolkits
Proceed? [y/N] y
✓ Removed /home/alex/.toolbase/toolkits
✓ Legacy layout removed.
Reinstall toolkits with tb install <name> to populate the new cache layout.

# 3. Reinstall the toolkits you actually need.
$ tb install arxiv-search
$ tb install aster
```

That's it. Your `~/.toolbase/config/<toolkit>.yaml` files survive (the format is forward-compatible — they just get a `schema_version: 1` line added on the next write). Your login state at `~/.toolbase/config.json` survives. Your `serve.yaml` (groups, selective serve) survives.

### `tb reset` has three modes

| Mode | Removes | Preserves |
|---|---|---|
| `tb reset` (default) | `toolkits/` (legacy 0.4.x) | Everything else |
| `tb reset --all` | `cache/`, `toolkits/`, `downloads/`, `default-project/` | `config.json`, `logs/`, `config/` |
| `tb reset --all --include-config` | All of the above + `config/` | `config.json`, `logs/` |

`--dry-run` works with every mode. `--yes` / `-y` skips confirmations (for CI).

`config.json` (your login state) and `logs/` are **always** preserved. There's no flag to delete them; that's deliberate.

### What about per-project state from 0.4.x?

There was none. 0.4.x had no project concept — everything was user-global. After the cutover, set up your projects fresh:

```
$ cd ~/research/my-paper
$ tb project init
$ tb install <name>          # this writes a pin into the new manifest
```

If you have a `~/.toolbase/config/<toolkit>.yaml` file with values you want, those carry over untouched — they're still your user-layer defaults. Override per-project only if you actually need to.

---

## Pointers

- **Frontend documentation site:** [tool-base.org/docs/environments](https://tool-base.org/docs/environments) (will mirror this file with extra walkthroughs).
- **Design doc with rationale:** `docs/ENVIRONMENTS_DESIGN.md` (in the main toolbase project repo).
- **Setup system (Tier-1 declarative + Tier-2 `setup.py`):** `docs/SETUP_SYSTEM_SPEC.md`.
- **Serve architecture (per-toolkit subprocess + MCP):** `docs/SERVE_ARCHITECTURE.md`.
- **CLI reference:** `tb --help` and `tb <command> --help` for everything.

If something here is unclear or wrong, open an issue at [github.com/toolbase/toolbase](https://github.com/toolbase/toolbase).
