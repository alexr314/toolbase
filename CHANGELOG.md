# Changelog

All notable changes to `toolbase` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

Serve/curation revamp. **Breaking** (v0, clean cutover — no compatibility aliases).

### Added

- `toolbase connect <client>` / `disconnect <client>` — write (or remove) the toolbase MCP entry in an agent client's config, replacing the manual JSON copy-paste. Claude Code in v1 (`~/.claude.json` for user scope, `.mcp.json` for `-l` project scope), via a pluggable adapter so Codex / Orchestral can follow. `--list` shows where toolbase is wired; `--clients` lists targets; `--profile` also sets the active profile; `--abspath` writes an absolute binary path. Non-destructive merge, atomic write.
- `toolbase activate` / `deactivate <toolkit | toolkit/bundle | toolkit__tool>` — expose or hide tools in the active profile. The casual-tier surface; users never need to learn "profiles" to curate.
- **Profiles** — named curated tool sets, one file per profile under `<scope>/.toolbase/profiles/<name>.yaml`. `toolbase profile <list|show|create|edit|delete|set-default|path|tools>` manages them (replaces `toolbase groups`).
- `toolbase install -a/--activate` — install and activate in one step.
- `toolbase list -v` — per-tool served/hidden view with bundle + config-gating annotations; `tb list` now marks each toolkit active/inactive, and `--json` gains an `active` field.
- `toolbase config init <toolkit> [--user | --project] [--force]` — scaffold a commented YAML config file from a toolkit's `config:` schema. Defaults to the project layer (matches `config set` / `unset`); pass `--user` for the user layer. Required fields land as `<NEEDS VALUE>`; optional fields with defaults get their default; optional fields without defaults are commented out so the full schema is visible.
- **Workspace-aware schema defaults.** `path` and `string` fields in a toolkit's `config:` block may use `${CWD}` (the orchestrator's `os.getcwd()` at serve time — i.e. the harness's launch directory, where the agent is working) or `${PROJECT_ROOT}` (the discovered `.toolbase/` parent, or `${CWD}` if there is none). Composition works (`${CWD}/scratch`). Unknown templates are rejected at schema parse time. `tb config show` renders templates alongside their current expansion.
- **Multi-bundle tool membership.** A tool's `bundle:` field now accepts either a single name or a list (`bundle: [a, b]`); a multi-bundle tool is served if **any** of its bundles is available and counts as in-profile if any of its bundles is in the profile's allowlist.
- **Per-bundle dependencies.** A toolkit author can declare `deps: [pip-spec]` on each bundle alongside the existing `requires:` (config-key gate). The toolkit's `requirements.txt` stays the always-installed base; bundle `deps:` add on top when the user installs that bundle.
- **Install-time bundle selection: `tb install <toolkit>[a,b]` (pip-extras style) and `--bundle a` (flag form).** Pip-installs only the selected bundles' `deps:` on top of `requirements.txt` rather than every bundle's deps. Re-installing with new bundles is **additive** (pip-like): pip-installs the new bundles' deps into the existing venv without rebuilding. `--rebuild` forces destructive reinstall. Cache metadata (`.install_meta.yaml`) and project manifest entries record the installed bundle set; serve filters tools whose bundles are entirely outside the installed set, with a one-line summary at startup per toolkit.
- **Subset-install visibility in `tb list`.** Version rows now end with `[subset: a, b]` when only some bundles' deps are installed (`[subset: (base only)]` for an explicit empty subset). `--json` gains an `installed_bundles` field (`null` for a full install, list for a subset). `tb list -v` annotates per-tool why a tool is hidden when its bundle isn't in the install set: `(skipped: bundle X not installed)` — multi-bundle plural `(skipped: bundles a, b not installed)`. Install-scope wins over the existing config-gating annotation since install-scope strips the deps that config-gating would later check. When 6+ tools would be install-gated in a single toolkit (large toolkits with bundle subsets — heptapod's 50-tool/8-bundle case prompted this), they collapse into a single dim summary line `(+N tools in uninstalled bundles: a, b, … — add with tb install <name>[<bundle>])` to keep the verbose output scannable. Config-gated tools stay inline since they're one `tb config set` away rather than a reinstall.

### Changed

- **Nothing-active by default.** Installing a toolkit places it in the cache but serves nothing until you `activate` it (conda-style: install ≠ activate). `tb serve` resolves an active profile — there is no "serve everything" fallback.
- `serve.yaml` is now defaults-only: `default.profile` (the active profile) and `default.disabled` (absolute blocklists), with a two-layer user→project merge.
- **Vocabulary:** the author-side intra-toolkit grouping is now a **bundle** (was `tool_groups:` / per-tool `group:`); the user-side curated subset is now a **profile** (was the `groups:` block in `serve.yaml`). The developer unit stays a **toolkit**. `tb serve --enable-bundle` replaces `--enable-group`.
- **Resolved state-config is injected at tool construction time.** Tools declared with required `StateField`s (e.g. a `base_directory` the toolkit author marks `required: true`) no longer fail with a pydantic `ValidationError` on serve startup; values flow from `~/.toolbase/config/<toolkit>.yaml` (and project-layer overrides) into the tool constructor via `_import_explicit_tools`. Required fields with a schema default — literal or template — are now satisfied by the default; previously the default was ignored and the field was flagged missing.
- **Per-tool failures during import / construction now skip just that tool**, emitting a structured `tool_import_skipped` log line to the per-toolkit log. A single misconfigured tool no longer takes down its sibling tools or the whole toolkit host.
- **CLI startup is faster.** `tb --help` / `tb list` and similar no-network commands dropped from ~290 ms to ~50 ms warm by lazy-importing `requests`, `rich.syntax`/`pygments`, `rich.panel` / `table` / `progress`, and dropping a dead `Syntax` import. Heavy modules load only when commands that need them run.
- `config_dir()` and `project_config_dir()` are pure path resolvers; they no longer `mkdir(parents=True, exist_ok=True)` as a side effect. Writers (`save_config` etc.) create parents lazily at write time, so a layered path lookup no longer leaves an empty `<project>/.toolbase/config/` dir behind that looks like a half-done install.

### Fixed

- **Partial-install cache slots no longer wedge subsequent `tb install` invocations.** A Ctrl-C during a long pip install (heavy bundle deps can take minutes) used to leave the cache slot with source files but no `.install_meta.yaml`. The next install with a bundle subset (`tb install foo[a,b]`) matched the "already installed with all bundles" branch, printed a misleading message about needing `--rebuild`, and exited 0 without doing anything. Two-part fix: (1) the fresh-install pipeline (source → env setup → meta write) is now wrapped in `try/finally` keyed on a success flag, so any interrupt or exception before meta-write removes the slot; (2) the collision check explicitly detects a missing `.install_meta.yaml` and treats it as a corrupted slot — auto-clean and proceed as fresh install rather than no-op.

### Removed

- `toolbase groups` and the `groups:` block in `serve.yaml` (replaced by profiles).
- `tb serve` positional toolkit names and the `--group` / `--enable-tool` / `--disable-tool` one-shot flags (curation now lives in profiles; `--profile` selects one).

---

## [0.1.0] — 2026-05-22

Initial Toolbase release. Toolbase is the community registry and CLI for AI agent toolkits — a **toolkit** is the publishable unit, and each toolkit bundles one or more **tools** that agents call over the [Model Context Protocol](https://modelcontextprotocol.io). You author and ship toolkits, install them into isolated environments, and serve them to coding agents (Claude Code, Codex) or any MCP client.

> Toolbase began as `scitoolkit`. The code is mature — it shipped across nine `scitoolkit` releases (0.1.0–0.6.1) over two weeks — but `toolbase` is a new, general-purpose package on PyPI, not a rename of the published `scitoolkit`. This entry is the cumulative feature set as of the first Toolbase release; the granular pre-rebrand release notes live with the `scitoolkit` project.

### Authoring and publishing

- `toolbase init` — scaffold a toolkit from template (`--with-setup` for toolkits that need a `setup.py`).
- `toolbase ingest` — register tools from existing source. Re-running over a directory that already has a `toolkit.yaml` merges (new tools appended, hand-edited entries preserved byte-for-byte) rather than overwriting; `--prune` removes stale entries, `--force` rebuilds from scratch.
- `toolbase create` — reserve a toolkit name on the registry without uploading code (optional; `publish` auto-registers on first run).
- `toolbase validate` — Pydantic-based pre-publish structural checks.
- `toolbase login` — browser-flow auth that stores a per-user token good for any toolkit you own or collaborate on. Legacy per-toolkit tokens (`toolbase login <toolkit>`) are still accepted but deprecated. `whoami` / `logout` round out auth.
- `toolbase publish [--dry-run]` — package and upload to the registry; auto-registers the name on first run, and blocks "version already exists" / "version decrease" before upload.

### Installing and managing

- `toolbase search` — find toolkits on the registry.
- `toolbase install <name|path>` — download (or build from a local path), extract, and set up an isolated environment (venv or conda, auto-detected). Scope flags: `-g` (global, the default), `-l` (pin into the current project's `.toolbase/manifest.yaml`), `-e <path>` (editable — symlink a local source into the cache so `serve` loads tools live, the `pip install -e .` parallel). Multiple versions of a toolkit coexist in the global cache; the binary lives once in the shared cache and the manifest scope is independent of file location.
- `toolbase list` / `toolbase uninstall <name>` — manage installed toolkits.

### Serving

- `toolbase serve` — multi-toolkit MCP aggregator (stdio). Each installed toolkit runs in its own subprocess in its own Python environment; the orchestrator aggregates them and exposes the union as a single MCP server. A crashed toolkit auto-restarts with exponential backoff and doesn't take the orchestrator down. Supports positional toolkit names, `--group`, `--enable-tool`, `--disable-tool`, `--dry-run`, `--call-timeout`.
- `toolbase groups` — manage named tool subsets that span toolkits.
- `toolbase logs` — tail the serve log with Rich coloring.

### Configuration and setup

- `toolbase config <show|edit|path|set|unset|validate>` — manage per-toolkit config at `~/.toolbase/config/<toolkit>.yaml`. Toolkits declare a `config:` block in `toolkit.yaml` (seven types: `string`, `secret`, `path`, `integer`, `float`, `boolean`, `choice`); the human-editable file is the canonical source, prompts are scaffolding.
- `toolbase setup <toolkit>` (`--reset`, `--check`) — run a toolkit's `setup.py` for involved setup: full prompts, resumable SHA256-verified downloads with auto-extraction (tar/zip, zip-slip defended), and derived-state writes via `ctx.set_config(...)`.

### Platform

- **Multi-tier execution:** same-Python toolkits run in venv, different-Python toolkits run under conda (auto-detected). Docker mode is detected and refused with a clear "coming in Phase 3B" message.
- **HTTP-loopback architecture** between the orchestrator and per-toolkit subprocesses.
- **Per-tool selection** per serve session or persistently in `~/.toolbase/serve.yaml`.
- **Skills surfacing:** a toolkit's `skills/*.md` files are auto-mirrored to `~/.claude/skills/` (symlinked on POSIX for live edits, copied on Windows) so Claude Code discovers them.
- **Agent-friendly:** every state-modifying command supports `--yes`, `--no`, `--no-input`; non-TTY stdin auto-applies non-interactive behavior.
- **`tb` alias:** every command is available as `tb` as well as `toolbase`.
- Python 3.12+ required.
