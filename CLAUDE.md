# Toolbase Package — Context for AI Agents

This file is the package-specific entry point. **Read these in order before
touching code** (all live in the parent `toolbase/` dir except the in-repo
serve doc):

1. **`../RENAME_CONSTANTS.md`** — the canonical scitoolkit→toolbase naming map
   (Toolbase is the rebrand of scitoolkit). What changed and what intentionally
   didn't (API host stays `api.scitoolkit.org` until Alex's cutover, etc.).
2. **`../STATUS.md`** — project-wide forward-state. The "Named principles"
   section (flag-equivalence, config-file canonical) and the "Architecture
   decisions (locked)" table are binding constraints on new work; "Known debt"
   is the backlog.
3. **`../MESSAGES_TO_AGENTS/HANDOFF_package_agent.md`** — this agent's handoff:
   binding conventions, current state, candidate work.
4. **`docs/SERVE_ARCHITECTURE.md`** (this directory) — how `toolbase serve`
   works in detail, with rationale for the orchestrator-plus-per-toolkit
   subprocess design. NOTE: it carries a "superseded" banner for the transport
   question — the orchestrator↔subprocess wire is now persistent stdio, not the
   HTTP-loopback the body describes.

The rest of this file is short on purpose. `../STATUS.md` is the live forward
document; this file is the index.

---

## What this package is

`toolbase` — Python CLI for the Toolbase registry. Lets authors
create, publish, install, and serve AI agent toolkits across any domain
(science is a strong example category, not the identity).

- **Audience:** developers and researchers comfortable with Python, not
  packaging experts.
- **Coding-agent users are the primary surface.** Every state-modifying
  CLI action must be reachable via flags (no required interactive prompts);
  see STATUS.md §"Named principles."
- **Persistent toolkit configuration lives in human-editable files** at
  `~/.toolbase/config/<toolkit>.yaml`. Prompts are scaffolding, not the
  authoritative path.

## Hard rules

1. **Python 3.12+** (orchestral-ai requirement; do not lower).
2. **No emojis in CLI output, code, or comments.** Hard rule from Alex.
   Only `✓` and `✗` are permitted.
3. **`toolbase serve` owns stdin/stdout** for MCP JSON-RPC. Anything
   printed to stdout from inside serve corrupts the wire. Use
   `Console(stderr=True)`.
4. **Don't add the toolkit dir to `sys.path`.** See the comment in
   `_toolkit_host.py::_import_tools_package` (the `spec_from_file_location`
   discipline); pinned by a regression test.

(A former hard rule #5 — "don't add a trailing slash to the `/mcp` MCPClient
URL" — was dropped: the HTTP-loopback URL it guarded, and its sentinel test
`test_orchestrator_url_no_slash.py`, were both retired in the 0.4.1 stdio
cutover. The orchestrator↔subprocess wire has no URL now.)

## Repo layout

Index, not exhaustive — the tree is larger than this. The repo dir is
`toolbase-package/` (its remote is `alexr314/toolbase.git`; the `-package`
suffix only disambiguates the local sibling dirs).

```
toolbase-package/
├── pyproject.toml                # Python 3.12+, click+rich+requests+orchestral+mcp
├── toolbase/
│   ├── cli.py                    # Click commands — main entrypoint (large)
│   ├── config.py                 # CONFIG/TOOLKITS/LOGS dirs; DEFAULT_API_URL + _api_url()
│   ├── auth.py                   # tb_user_ token prefix logic (stk_/sct_ retired)
│   ├── validation.py             # Pydantic schemas + categories-from-API
│   ├── toolkit.py                # init scaffolding logic
│   ├── ingest.py / versioning.py / skills.py
│   ├── _toolkit_host.py          # per-toolkit subprocess entrypoint
│   ├── _setup_host.py            # setup-system subprocess entrypoint
│   ├── astro.py / hep.py / neutrino.py / quantum.py   # example-category helpers
│   ├── logging/logger.py         # ToolLogger + serve.log
│   ├── envs/                     # cache + manifest + config layout
│   │   ├── cache.py              # LEGACY_META_FILE (.tb_meta.json) lives here
│   │   ├── manifest.py / config.py / discovery.py / schema.py / paths.py
│   ├── setup/                    # Phase 3C setup system
│   │   ├── runner.py             # reads legacy meta for env/python_path/env_name
│   │   ├── declarative.py / context.py / downloads.py / storage.py / _rpc.py …
│   ├── serve/
│   │   ├── orchestrator.py       # discovery → spawn → serve MCP stdio (persistent stdio)
│   │   ├── proxy_tool.py         # synthesized BaseTool that forwards via MCPClient
│   │   ├── config.py             # serve.yaml: default.profile + default.disabled (defaults only)
│   │   ├── profiles.py           # per-file profiles, resolution chain, tool_is_served
│   │   ├── profile_scaffold.py   # ruamel round-trip engine behind tb activate/deactivate
│   │   ├── bundles.py            # author bundle-availability gating (was tool_groups.py)
│   ├── connect/                  # tb connect: client-wiring adapters
│   │   ├── base.py / claude_code.py   # ClientAdapter + Claude Code (.mcp.json / ~/.claude.json)
│   └── templates/                # init scaffolding (toolkit.yaml, tool_example, mcp/, skills/)
├── tests/                        # 51 unit modules (980 tests)
│   ├── test_categories_api.py / test_interactive_flags.py
│   ├── test_envs_*.py / test_setup_*.py / test_orchestrator_*.py
│   └── e2e/                      # network-free run_*_e2e.py harnesses + fixture toolkits
└── docs/
    ├── SERVE_ARCHITECTURE.md     # serve design (transport section superseded → stdio)
    ├── SETUP_SYSTEM_SPEC.md      # Phase 3C (file-first per 2026-05-06 revision)
    └── SETUP_RECIPES.md
```

## Useful commands

Run from the repo root (`toolbase-package/`). The dev venv is `.polish-venv`
(gitignored, Python 3.12).

```bash
# Run unit tests (980, ~2.5 min)
.polish-venv/bin/python -m pytest tests/ --ignore=tests/e2e -q

# Run a focused subset
.polish-venv/bin/python -m pytest tests/ --ignore=tests/e2e -q -k "orchestrat or serve"

# Network-free e2e harnesses (each is a runnable script)
.polish-venv/bin/python tests/e2e/run_install_e2e.py
.polish-venv/bin/python tests/e2e/run_serve_e2e.py

# Quick CLI sanity (entry points: toolbase + tb)
.polish-venv/bin/toolbase list
.polish-venv/bin/toolbase logs --no-follow -n 30      # tail serve.log

# Local install (editable)
.polish-venv/bin/pip install -e .
```

## Conventions for this package

- **Click commands.** State-modifying commands carry `@_interactive_options`
  for `--yes/-y`, `--no`, `--no-input`. Use `_resolve_prompt_mode()` to
  reduce the flags + TTY status to one of `"yes" | "no" | "skip" | "ask"`,
  then call `_confirm()` or `_require_input()` rather than `click.confirm`
  / `click.prompt` directly. Mark destructive prompts `consequential=True`.
- **Subprocess pip output.** Use `_run_pip_with_progress()` instead of
  swallowing pip via `--quiet` — surfaces "Collecting <pkg>" / "Building
  wheel for <pkg>" / "Installing collected packages: …" into a Rich Status
  spinner so the user can see motion.
- **Logging.** `ToolLogger.log_event(...)` for orchestrator-level events,
  `log_tool_start/output/complete` for per-call traces. Pass `serve_log=True`
  on first construction (only `serve` does this) to mirror to
  `~/.toolbase/logs/serve.log`. Tail it with `toolbase logs`.

## Where things go

- **A future-direction or "what's next" question:** `../STATUS.md`.
- **Known debt / candidate work:** `../STATUS.md` §"Known debt".
- **A new architectural principle:** `../STATUS.md` §"Named principles," after
  manager review.
- **An inter-agent change request (e.g. "frontend wants X"):**
  `../MESSAGES_TO_AGENTS/<date>_to_<agent>__<topic>.md`.
- **Deep "why is it like this" gotcha history:** the old scitoolkit `STATUS.md`
  (referenced from `../STATUS.md`'s header) — not carried into this repo.

---

**Last updated:** 2026-05-22 (paths/layout reconciled to the toolbase-package
tree; stale `/Users/adroman/research/agents/toolbase/` doc paths and the retired
`/mcp` URL hard-rule removed).
