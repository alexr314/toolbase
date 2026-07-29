# Changelog

All notable changes to `toolbase` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [0.9.0] — 2026-07-29

### Changed

- **Toolkit hosts now run on MCP SDK 2.x.** The `mcp` dependency moved from `>=1.0,<2` to `>=2` and `orchestral-ai` from `>=1.4` to `>=1.10`, in both toolbase's own dependencies and the `HOST_RUNTIME_REQUIREMENTS` installed into every toolkit venv. toolbase never imports the SDK itself — it reaches it only through `orchestral.mcp` — so the major orchestral targets is the major toolbase needs, and the two bounds only make sense as a pair. orchestral 1.10 is a clean port to the 2.x API with no 1.x compatibility path and refuses to start against an older SDK; conversely orchestral <1.10 is built on the decorator API (`Server.list_tools` / `Server.call_tool`) that 2.0 removed. Since orchestral-ai declares no `mcp` constraint of its own, pinning both ends is what keeps pip off a mismatched pair.

  **Existing toolkit venvs were built against mcp 1.x and need `tb install <toolkit> --rebuild`** to pick up the new pair; until then their hosts fail at startup with `'Server' object has no attribute 'list_tools'`, surfacing as `mcp connect failed: unhandled errors in a TaskGroup`.

- Toolkit validation now names `orchestral-ai>=1.10.0` as the floor a toolkit's `requirements.txt` should declare. The check itself is unchanged (still presence-only, not a version comparison).

## [0.8.1] — 2026-07-27

### Fixed

- **`tb connect --abspath` no longer silently falls back to a bare command.** It resolved the binary with `shutil.which("toolbase")`, which returns `None` exactly when toolbase isn't on PATH at connect time (a venv/conda install, or toolbase invoked by absolute path) — the very case `--abspath` exists for — so it quietly wrote a bare `toolbase` that the harness then couldn't launch. It now resolves the toolbase beside the running interpreter first (`sys.executable`'s dir), falling back to `which` then bare.

### Changed

- **`tb connect` command default is now scope-aware.** User-scope connects (`-g`, a machine-local config that is never committed) default to the **absolute** toolbase path so the harness always finds it — important because a harness process (e.g. a GUI app, or a shell without your env activated) often doesn't have a venv/conda `bin` on PATH. Project-scope connects (git-committed, shared) stay **bare** `toolbase` for portability, and now print a note when toolbase is env-installed that a bare command may not resolve for the harness (suggesting `--abspath`, `-g`, or a login-PATH install). New `--portable` flag forces the bare command in any scope; `--abspath` still forces the absolute path.

## [0.8.0] — 2026-07-27

### Added

- **`tb connect opencode` — OpenCode is now a supported harness.** A new `OpenCodeAdapter` wires toolbase into OpenCode's `mcp` block (`opencode.json` / `opencode.jsonc`, user `~/.config/opencode/` honoring `$XDG_CONFIG_HOME`, or project root), writing a local stdio server in OpenCode's single-`command`-array form (`{"type":"local","command":["toolbase","serve"],"enabled":true}`) with a non-destructive, atomic merge that preserves `$schema` and other servers. An existing comment-free `.jsonc` is edited in place; a `.jsonc` that actually carries comments is refused with guidance rather than clobbered. Registering the adapter also lights up `tb disconnect opencode`, `--harnesses`, and `--list`. OpenCode skills surface as `~/.config/opencode/command/<toolkit>__<skill>.md` slash-command prompts (via the existing `SkillTarget` flat layout), keeping only the `description` frontmatter OpenCode shows in its TUI — enabled by a new `SkillTarget.frontmatter_keys` that narrows the emitted block to named keys.

## [0.7.0] — 2026-07-26

### Added

- **`tb connect` now surfaces skills per-harness, and Codex is supported natively.** A toolkit's `skills/*.md` guides are surfaced into the harness you connect, not at install time against a hardcoded path. `tb connect <harness>` surfaces the *activated* toolkits' skills (the same set whose tools are served) into that harness's location; `tb disconnect` / `tb connect --remove` clears them; `--no-skills` wires the MCP server only. Two layouts, one per adapter: **Claude Code** → `~/.claude/skills/<toolkit>__<skill>/SKILL.md` (frontmatter preserved; auto-surfaced to the model and exposed as a `/<name>` slash command), **Codex** → `~/.codex/prompts/<toolkit>__<skill>.md` (frontmatter stripped to the body; a user-invoked `/<name>` slash-command prompt). Adapters declare their surface via a new `HarnessAdapter.skill_target()` returning a `SkillTarget`; `skills.surface_skills` / `unsurface_skills` / `unsurface_all` generalize the copy/gate/ownership logic across layouts. Flat-layout ownership is tracked by a `.toolbase-managed.json` manifest (a flat file has no dir for the `OWNED_MARKER`), so user-authored prompts with the same prefix are never removed.
- **Skill packs (skills-only toolkits) are now first-class.** A toolkit may ship only `skills/*.md` guides and declare no tools. `tools:` in `toolkit.yaml` is now optional (defaults to empty), and the `orchestral-ai` requirement is waived when a toolkit has no tools (a skill pack runs no Orchestral tool framework). Such a toolkit validates, installs, activates, and surfaces its skills through `tb connect` like any other; the serve orchestrator recognizes it (`_toolkit_is_skills_only`: no declared tools *and* no implicit `tools/__init__.py`) and skips launching a host for it — while keeping it fully discoverable so `tb activate` / `tb connect` still surface its guides. When the *only* active toolkits are skill packs, `tb serve` explains there is nothing to serve over MCP and points at `tb connect`, rather than failing with an opaque error. This pairs with per-skill toggling to make a curated pack of standalone skills a supported unit.
- **Per-skill enable/disable, via the existing activation grammar.** `tb deactivate <toolkit>__<skill>` blocklists a single guide; `tb activate <toolkit>__<skill>` restores it. A `<toolkit>__<name>` item is resolved to a **skill** when it matches a surfaced skill slug and *not* a tool (on a genuine name collision the tool wins and a note is printed, preserving existing tool references). Skills are on by default when the toolkit is active, so a profile records only a per-toolkit blocklist — a new `skills: { disabled: [...] }` block under each toolkit entry (`ToolkitSelection.disabled_skills`). Skill surfacing (`surface_skills`, and hence `tb connect`) subtracts these slugs, the per-skill analog of bundle gating. Install/connect now print each guide by its `<toolkit>__<slug>` toggle name so the slug is discoverable. This makes it practical to ship a toolkit as a bundle of individually toggleable skills.

### Changed

- **Skill surfacing moved off `tb install`.** Installing a toolkit now only *reports* that it ships skills and points at `tb connect`; it no longer writes into `~/.claude/skills/`. Skills follow the harness you connect (and the toolkits you've activated), symmetric with how MCP tools are wired. Uninstalling a toolkit reaps its surfaced skills from every harness target (Claude Code and Codex), not just Claude. Back-compat: `install_skills_for_toolkit` / `uninstall_skills_for_toolkit` remain as Claude-dir wrappers over the new `SkillTarget` API.

## [0.6.1] — 2026-07-24

### Fixed

- `read_versioned_yaml` / `write_versioned_yaml` (`envs/schema.py`) and the setup-storage helpers (`setup/storage.py`) no longer share a single module-level `ruamel.yaml.YAML()` instance across calls. A `YAML()` object holds mutable parse state and is not thread-safe, so concurrent callers (e.g. two trials resolving an environment in parallel, which parses every cached toolkit's `.install_meta.yaml`) intermittently corrupted it into a misleading `'NoneType' object has no attribute 'anchor'` or a spurious `DuplicateKeyError` against a file that parses cleanly single-threaded. Each read and write now constructs its own loader/dumper. ([#39](https://github.com/alexr314/toolbase/pull/39))

## [0.2.1] — 2026-06-06

### Fixed

- `tb --version` and `toolbase.__version__` now report the installed package version instead of a stale hardcoded `0.1.0`. Both are sourced from `importlib.metadata.version("toolbase")`, so future releases stay in sync with `pyproject.toml` automatically.

## [0.2.0] — 2026-06-05

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
- **Author-controlled tool display names.** A `tools[]` entry in `toolkit.yaml` may now carry an optional `display_name:` field that overrides the agent-visible name on the MCP wire (after the orchestrator's `<toolkit>__` prefix). When absent, the default is the Python class name with the trailing `Tool` suffix stripped, PascalCase preserved — so `InspireSearchTool` advertises as `heptapod__InspireSearch`. Explicit `display_name: search_papers` would advertise as `heptapod__search_papers`. Precedence: yaml `display_name:` > `@define_tool(display_name=...)` in code > derived default. The yaml layer wins because that's what the registry sees and what an author editing the file directly expects to take effect.

### Changed

- **Nothing-active by default.** Installing a toolkit places it in the cache but serves nothing until you `activate` it (conda-style: install ≠ activate). `tb serve` resolves an active profile — there is no "serve everything" fallback.
- `serve.yaml` is now defaults-only: `default.profile` (the active profile) and `default.disabled` (absolute blocklists), with a two-layer user→project merge.
- **Vocabulary:** the author-side intra-toolkit grouping is now a **bundle** (was `tool_groups:` / per-tool `group:`); the user-side curated subset is now a **profile** (was the `groups:` block in `serve.yaml`). The developer unit stays a **toolkit**. `tb serve --enable-bundle` replaces `--enable-group`.
- **Resolved state-config is injected at tool construction time.** Tools declared with required `StateField`s (e.g. a `base_directory` the toolkit author marks `required: true`) no longer fail with a pydantic `ValidationError` on serve startup; values flow from `~/.toolbase/config/<toolkit>.yaml` (and project-layer overrides) into the tool constructor via `_import_explicit_tools`. Required fields with a schema default — literal or template — are now satisfied by the default; previously the default was ignored and the field was flagged missing.
- **Per-tool failures during import / construction now skip just that tool**, emitting a structured `tool_import_skipped` log line to the per-toolkit log. A single misconfigured tool no longer takes down its sibling tools or the whole toolkit host.
- **Agent-visible tool names are now PascalCase by default** (breaking on the wire — old: `heptapod__inspiresearch`, new: `heptapod__InspireSearch`). The toolkit host now sets each instance's `_mcp_display_name` to the class name with the `Tool` suffix stripped (PascalCase preserved), and calls `MCPServer(use_display_names=True)` so MCP advertises it. The previous default — `cls.__name__.removesuffix("Tool").lower()` from `BaseTool.get_name()` — collapsed word boundaries into a single lowercase blob that was both harder for the agent to read and impossible to customise per-tool short of subclassing. Agents that have hard-coded the old lowered form (logs, harness configs, scripts) need updating; agents that read the tool list each turn (Claude Code, Codex) adapt automatically.
- **CLI startup is faster.** `tb --help` / `tb list` and similar no-network commands dropped from ~290 ms to ~50 ms warm by lazy-importing `requests`, `rich.syntax`/`pygments`, `rich.panel` / `table` / `progress`, and dropping a dead `Syntax` import. Heavy modules load only when commands that need them run.
- `config_dir()` and `project_config_dir()` are pure path resolvers; they no longer `mkdir(parents=True, exist_ok=True)` as a side effect. Writers (`save_config` etc.) create parents lazily at write time, so a layered path lookup no longer leaves an empty `<project>/.toolbase/config/` dir behind that looks like a half-done install.

### Fixed

- **Orchestrator's per-tool install-scope and config-gating filter actually fires.** `tb serve` reads each toolkit's `toolkit.yaml` to build a `name_to_bundles` lookup (which bundles each tool belongs to) and consults it for every tool the host advertises. The lookup was keyed by toolkit.yaml's `tools[].name` field — the PascalCase BaseTool subclass name (e.g. `InspireSearchTool`). But the toolkit host calls `orchestral.mcp.MCPServer(..., use_display_names=False)`, which registers each tool under `BaseTool.get_name() = cls.__name__.removesuffix("Tool").lower()` — so MCP advertises `inspiresearch`, not `InspireSearchTool`. Every `name_to_bundles.get(host_advertised_name)` missed → `tool_bundles` came back `[]` → both the install-scope gate and the config-gate short-circuited (they no-op on empty bundle membership). Result: a `tb install heptapod[inspire,pdg]` subset install still surfaced ~30 tools instead of the expected ~14, including tools from bundles whose pip deps weren't installed — those would just blow up at the host's import step with a `tool_import_skipped` log line, but the orchestrator continued to advertise the rest. Normalised `name_to_bundles` keys to match the MCP form so the filter works as documented.
- **`tb config init` scaffold no longer produces unparseable multi-document YAML.** Defaults with non-trivial values — `path` template defaults like `${CWD}`, string/integer/secret defaults — were being rendered via `yaml.safe_dump(scalar)` whose output appends a `\n...` document-end marker that the previous `.strip()` only partially trimmed (trailing newline only, not the marker). The resulting file looked like one document but parsed as two, so the orchestrator dropped the toolkit at serve startup with `config incomplete — invalid: <file> (failed to parse ...: expected a single document in the stream)` and the harness reported `Failed to reconnect to toolbase: -32000` with no obvious cause. Existing broken files don't auto-repair — delete the bare `...` line manually or re-run `tb config init --force` to regenerate.
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
