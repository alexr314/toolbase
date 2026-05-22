# Portable group manifests

**Status:** proposal (pre-implementation)
**Authors:** Tony Menzo; drafted with Claude (Opus 4.7)
**Date:** 2026-05-08
**Suggested labels:** `enhancement`, `serve`, `groups`

## Summary

Today, named groups live exclusively in `~/.toolbase/serve.yaml` and are referenced by name (`tb serve --group physics`). This proposal extends groups so they can also be **loaded from arbitrary paths**, **exported and imported as standalone files**, and **carry toolkit version pins**. The result is a `requirements.txt`-style portable manifest for tool sets — version-controllable per-project, shareable between users, and reproducible across machines.

The data model and resolver already support cross-toolkit composition. The work here is almost entirely at the CLI surface plus a small schema extension for version pinning.

---

## Motivation

Two real workflows are blocked by today's name-only, global-only group system:

1. **Sharing a curated tool set between users.** A scientist wants to hand a collaborator a single file (`physics.yaml`) capturing "the toolkits and tools I use for this project." Today the only path is "install toolbase, then manually replicate `groups create ...` commands."
2. **Project-scoped tool sets.** A researcher working on multiple projects wants different tool sets per project, version-controlled alongside the project's other config files (cf. `requirements.txt`, `pyproject.toml`, `.tool-versions`).

Both reduce to the same primitive: **load a group from an arbitrary path, with optional version pinning, instead of looking it up by name in the global file.**

---

## What's already in place

The data model and resolver already support cross-toolkit composition; the gap is purely at the CLI surface. Implementers should not need to touch the resolver.

- `Group` dataclass — [toolbase/serve/config.py:63-75](../../toolbase/serve/config.py#L63-L75) — already a list of toolkits + per-tool disable list. The schema is intentionally cross-toolkit.
- `load_serve_config(path)` — [toolbase/serve/config.py:95](../../toolbase/serve/config.py#L95) — already accepts an arbitrary path; defaults to `SERVE_CONFIG_PATH`.
- `Group.to_yaml_dict()` — [toolbase/serve/config.py:71](../../toolbase/serve/config.py#L71) — round-trips for export.
- `resolve_serve_set(...)` — [toolbase/serve/config.py:195](../../toolbase/serve/config.py#L195) — pure resolver, source-agnostic.

`--group` resolves *by name only* against the global file ([config.py:247](../../toolbase/serve/config.py#L247)). Wiring an alternative source feeds the same resolver path.

---

## Feature 1 — Load a group from a path

**Why:** unblocks both motivating workflows; smallest possible CLI change.

**Proposal:**

```bash
tb serve --group-file ./physics.yaml
```

**Behavior:** loads the YAML, instantiates a `Group`, and feeds it to `resolve_serve_set` exactly the way `--group <name>` does today. `--group` and `--group-file` are mutually exclusive.

**Acceptance:**

- [ ] `--group-file PATH` flag wired through `cli.py` to the existing resolver.
- [ ] Clear error if the file is missing, malformed, or references uninstalled toolkits (with actionable pointer: `tb install <name>`).
- [ ] Unit tests: happy path, missing file, malformed YAML, missing toolkit warning.

---

## Feature 2 — File format for portable manifests

**Why:** the current `Group` schema is multi-group-per-file (intended for `serve.yaml`). Portable files should be single-group-per-file so they're easy to share, name, and version-control.

**Proposal — single-group standalone format:**

```yaml
name: physics-pipeline
toolkits:
  - heptapod
  - arxiv-search
tools:
  disabled:
    - heptapod__expensive_op
```

The existing multi-group `serve.yaml` schema is preserved unchanged. The loader auto-detects which form a file is in (presence of top-level `name:` ⇒ single-group; presence of top-level `groups:` or `default:` ⇒ multi-group / serve config).

**Acceptance:**

- [ ] Loader handles both shapes; round-trips cleanly.
- [ ] Documented in a follow-on `docs/PORTABLE_GROUPS_SPEC.md` once the issue is accepted.

**Open question:** TOML support? The schema fits trivially. Reasonable to defer to a follow-up unless there's demand.

---

## Feature 3 — Export and import groups

**Why:** completes the share/adopt loop. Direct `requirements.txt` analog. Export and import are inverses: export emits a standalone manifest from one of your local groups; import takes such a manifest and registers it in your home-dir config.

### Export

`tb groups export NAME` reads one of the groups defined in `~/.toolbase/serve.yaml` and writes it as a standalone single-group YAML file (the Feature 2 format). The output is self-contained — any other user can consume it via `--group-file` (Feature 1) or `groups import` (below).

```bash
tb groups export physics > physics.yaml      # to stdout (pipeable)
tb groups export physics --out physics.yaml  # to a path
```

### Import

`tb groups import PATH` does **not** install tools or modify any toolkit storage. It is purely a config-file edit: it reads the standalone single-group file at `PATH` and **inserts it as a new entry under the `groups:` block of `~/.toolbase/serve.yaml`**, leaving everything else in that file untouched. The result is that the imported group becomes globally available as `--group <name>` from any directory.

```bash
tb groups import ./physics.yaml              # register the file's group in ~/.toolbase/serve.yaml
tb groups import ./physics.yaml --name p2    # register under a different local name
tb groups import ./physics.yaml --force      # overwrite if a group of that name already exists
```

**Concrete before/after.** Suppose `~/.toolbase/serve.yaml` currently looks like:

```yaml
default:
  toolkits:
    disabled: [slow-toolkit]
groups:
  exoplanet:
    toolkits: [aster, arxiv-search]
```

And `physics.yaml` (the standalone single-group manifest) looks like:

```yaml
name: physics-pipeline
toolkits: [heptapod, arxiv-search]
tools:
  disabled: [heptapod__expensive_op]
```

After running `tb groups import ./physics.yaml`, the home-dir file becomes:

```yaml
default:
  toolkits:
    disabled: [slow-toolkit]
groups:
  exoplanet:
    toolkits: [aster, arxiv-search]
  physics-pipeline:                          # ← newly added; no other lines changed
    toolkits: [heptapod, arxiv-search]
    tools:
      disabled: [heptapod__expensive_op]
```

The user can now run `tb serve --group physics-pipeline` from anywhere.

**Flag semantics:**

- `--name NAME` overrides the manifest's internal `name:` field. Useful for shorthand (`--name p2`) or when the original name collides with an existing group.
- `--force` permits overwriting an existing group of the same name. Without it, a collision is an error with an actionable message ("group 'physics-pipeline' already exists in `~/.toolbase/serve.yaml`; pass `--force` to overwrite or `--name` to register under a different local name").

**Note on project files.** This MVP targets `~/.toolbase/serve.yaml` only. A future iteration paired with Feature 5 could add `--to <path>` or `--project` to import into a project-local `.toolbase/serve.yaml` instead; out of scope here.

**Acceptance:**

- [ ] `groups export NAME [--out PATH]` — writes a single-group YAML to stdout or path; preserves the group's `name:` field.
- [ ] `groups import PATH [--name NAME] [--force]` — merges into the `groups:` block of `~/.toolbase/serve.yaml`; the rest of the file is untouched.
- [ ] Errors on name collision unless `--force` is passed; the error message names the conflicting group and the file path.
- [ ] `--name` value, when provided, overrides the manifest's internal `name:` field.
- [ ] Tests: round-trip export → import, name collision (error + `--force` overwrite), malformed input, custom `--name`, preservation of unrelated entries in the existing `serve.yaml`.

---

## Feature 4 — Toolkit version pinning in manifests

**Why:** without version pins, a manifest is brittle. `arxiv-search 2.0` could rename or remove a tool and silently break a shared workflow. Pinning makes the manifest reproducible.

**Proposal — extend the manifest schema:**

```yaml
name: physics-pipeline
toolkits:
  - name: heptapod
    version: ">=1.2,<2.0"     # PEP 440 spec; optional
  - name: arxiv-search
    version: "==0.4.2"
  - name: misc-utils           # bare string still accepted = "any installed"
```

**Behavior:** at serve startup, after toolkit discovery, validate each pin against the installed `.tb_meta.json` version. Mismatch ⇒ skip that toolkit with a clear pointer:

```
heptapod is installed at 1.0.3, but this manifest requires >=1.2,<2.0.
Run: tb install heptapod --version 1.2.0
```

**Acceptance:**

- [ ] PEP 440 spec parsing (use `packaging.specifiers.SpecifierSet`).
- [ ] Bare string accepted for back-compat ("any installed").
- [ ] Mismatch produces actionable error message naming the version found and the version required.
- [ ] Tests: exact pin, range pin, bare string, mismatch path, malformed spec.

---

## Feature 5 — Project-local auto-discovery

**Why:** matches `pyproject.toml` / `.tool-versions` / `direnv` ergonomics. The directory-based shape (`.toolbase/serve.yaml`) mirrors the home-dir layout and leaves room for additional project-scoped files later (e.g. per-project `.toolbase/config/<toolkit>.yaml` if that ever becomes useful).

**Proposal:**

```bash
tb serve   # walks up from cwd looking for ./.toolbase/serve.yaml.
            # If found, uses it. Otherwise falls back to ~/.toolbase/serve.yaml.
```

**Scoping behavior:** when a project file is present, it **replaces** (not merges with) the home-dir `serve.yaml`. The toolkit set, group definitions, and disabled lists are all read from the project file. This is intentional — project-scoped configs are meant to be self-contained and reproducible, not affected by whatever happens to be in the user's home dir. Users who want home-dir defaults to apply can either copy the relevant entries into the project file or skip discovery with `--no-project`.

**Acceptance:**

- [ ] Discovery walks up from cwd to first `.toolbase/serve.yaml` hit (or stops at home dir).
- [ ] `--no-project` escape hatch to force home-dir config even if a project file exists.
- [ ] At serve startup, log the resolved config source: `loaded project config from ./.toolbase/serve.yaml`.
- [ ] Tests: cwd hit, parent hit, no hit (fallback), `--no-project` override.

---

## Feature 6 — Companion install from manifest (v2 / out of scope for minimum-viable-product)

**Why:** completes the `pip install -r requirements.txt` analog.

**Proposal:**

```bash
tb install --from physics.yaml    # installs missing toolkits at the pinned versions
```

**Out of scope for the MVP.** Listed here so the design above (Feature 4's version pins) lands in a way that makes this build-on-able without rework.

---

## Feature 7 — Registry-enforced tool name stability (backend, separate work)

**Why:** version pins (Feature 4) only carry meaning if "no breaking change inside this version range" is a real guarantee. Today, nothing prevents a toolkit author from renaming or removing a tool within a minor or patch bump.

**Proposal:** `tb publish` (or the registry on upload) diffs the new tool list against the previously-published version. **Removing or renaming a tool requires a major version bump.** Adding tools is fine in any bump.

**Out of scope for this issue** — backend work, separate from CLI changes. Flagging here so it's tracked alongside, since pinning without enforcement is hopeful at best.

---

## Resolution order: how the features compose

Features 1 (`--group-file`) and 5 (project `.toolbase/serve.yaml`) both load configuration from a file, but they serve **orthogonal purposes** and compose cleanly rather than competing.

| | **Feature 1: `--group-file PATH`** | **Feature 5: `.toolbase/serve.yaml`** |
|---|---|---|
| Trigger | Explicit flag | Auto-discovered from cwd |
| File shape | Single-group standalone (Feature 2) | Multi-group `serve.yaml` schema |
| Lifetime | Ad-hoc / one-shot | Persistent / contextual |
| Lives | Anywhere on disk | Fixed: `./.toolbase/serve.yaml` |
| Use case | "Run this exact manifest a collaborator gave me" | "In this project, my default serve set is X" |
| Mental model | `pip install -r requirements.txt` | `pyproject.toml` |

### Source resolution (highest priority first)

When `tb serve` runs, the resolved configuration source is determined by this strict priority:

1. **`--group-file PATH`** — load the standalone single-group file. **Project discovery is skipped entirely.** The home-dir `serve.yaml` is also not consulted. This makes `--group-file` invocations deterministic regardless of cwd.
2. **`--group <name>`** — look up the named group:
   - In the project `.toolbase/serve.yaml` if discovered (and `--no-project` was not passed), or
   - In `~/.toolbase/serve.yaml` otherwise.
3. **No source flag** — apply the `default:` block from:
   - The project `.toolbase/serve.yaml` if discovered (and `--no-project` was not passed), or
   - `~/.toolbase/serve.yaml` otherwise.

`--enable-tool` and `--disable-tool` apply on top of whichever source was selected (their semantics are unchanged from today).

### Concrete examples

```bash
# Inside a project with ./.toolbase/serve.yaml that defines groups "quick-look" and "final":

tb serve                              # → project default block
tb serve --group quick-look           # → project's "quick-look" group
tb serve --group-file ../shared/physics.yaml
                                       # → external standalone file; project file ignored
tb serve --no-project                 # → home-dir ~/.toolbase/serve.yaml default
tb serve --no-project --group physics # → home-dir's "physics" group

# Outside any project (no .toolbase/serve.yaml in cwd or ancestors):

tb serve                              # → home-dir default block
tb serve --group physics              # → home-dir's "physics" group
tb serve --group-file ./physics.yaml  # → standalone file (cwd-relative path)
```

### Mutual exclusivity

`--group` and `--group-file` are mutually exclusive — passing both is an error with a clear message ("`--group` reads a named group from `serve.yaml`; `--group-file` reads a standalone manifest from a path. Pick one.").

---

## Open questions to resolve before implementation

- **Bare-path serve syntax** — should `tb serve ./physics.yaml` (no flag) work? Convenient, but ambiguous if a toolkit is ever named `physics.yaml`. Workable rule: treat the arg as a file iff it ends in `.yaml`/`.yml`/`.toml` and exists on disk. Not blocking; could ship `--group-file` first and add bare-path later.
- **TOML support** — defer to follow-up unless there's demand.
- **Per-tool allowlist syntax in manifests** — today's `Group` only has `disabled_tools`. Should portable manifests support `enabled:` too, mirroring the `--enable-tool` flag? Probably yes for symmetry, but adds resolver wiring. Not blocking for MVP.

---

## Suggested split

If the maintainer prefers separate issues:

- **Issue A — MVP:** Features 1, 2, 3, 4 (the core file-portable manifest workflow with version pinning).
- **Issue B — Polish:** Feature 5 (project-local discovery).
- **Issue C — Companion:** Feature 6 (`install --from`).
- **Issue D — Registry:** Feature 7 (tool-name stability enforcement, backend work).

Or one tracking issue with all features as checkboxes.

---

## Out of scope

- **Per-tool versioning.** Discussed and rejected as the wrong abstraction (see "Design rationale" appendix below).
- **Per-tool dependency resolution independent of toolkit deps.** Same.
- **Cross-toolkit interface contracts** beyond what the LLM already bridges. The agent is the integration layer for tool-to-tool composition; this proposal does not change that.
- **A new state-file format for `~/.toolbase/serve.yaml`.** The existing multi-group schema stays; the new single-group format is purely for portable files.

---

## Acceptance summary (MVP — Features 1-4)

- [ ] `tb serve --group-file PATH` works against the proposed single-group YAML schema.
- [ ] `tb groups export NAME` writes a standalone-format YAML.
- [ ] `tb groups import PATH` adopts a standalone group into `~/.toolbase/serve.yaml`.
- [ ] Toolkit `version:` field parses PEP 440 specs and rejects with a clear error if the installed version doesn't satisfy.
- [ ] Missing toolkits in a manifest produce a clear "install with `tb install ...`" pointer.
- [ ] Unit tests cover: load from path, version mismatch, missing toolkit, round-trip export/import, format auto-detection.
- [ ] Spec doc at `docs/PORTABLE_GROUPS_SPEC.md` matching the precedent of `SERVE_ARCHITECTURE.md` / `SETUP_SYSTEM_SPEC.md`.
