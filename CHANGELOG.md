# Changelog

All notable changes to `toolbase` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
