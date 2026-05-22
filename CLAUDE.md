# Toolbase Package — Context for AI Agents

This file is the package-specific entry point. **Read these in order before
touching code:**

1. **`/Users/adroman/research/agents/toolbase/HANDOFF.md`** — package-side
   gotchas (six load-bearing ones; do not "clean them up"), open-bugs ledger,
   repo layout. Authoritative for the CLI's current state.
2. **`/Users/adroman/research/agents/toolbase/STATUS.md`** — project-wide
   status. The "Named principles" section (flag-equivalence, config-file
   canonical) and the "Architecture decisions (locked)" table are binding
   constraints on new work.
3. **`docs/SERVE_ARCHITECTURE.md`** (this directory) — how `toolbase serve`
   works in detail, with rationale for the orchestrator-plus-per-toolkit
   subprocess design.

The rest of this file is short on purpose. STATUS.md and HANDOFF.md are
the live documents; this file is the index.

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
4. **Don't add the toolkit dir to `sys.path`.** See HANDOFF.md gotcha #2
   and the comment in `_toolkit_host.py::_import_tools_package`.
5. **Don't add a trailing slash to `/mcp`** in the orchestrator's MCPClient
   URL. See HANDOFF.md gotcha #1; pinned by
   `tests/test_orchestrator_url_no_slash.py`.

## Repo layout

```
tb-package/
├── pyproject.toml                # Python 3.12+, click+rich+requests+orchestral+mcp
├── toolbase/
│   ├── cli.py                    # Click commands — main entrypoint
│   ├── config.py                 # CONFIG_DIR, TOOLKITS_DIR, LOGS_DIR
│   ├── validation.py             # Pydantic schemas + categories-from-API
│   ├── toolkit.py                # init scaffolding logic
│   ├── _toolkit_host.py          # per-toolkit subprocess entrypoint
│   ├── logging/logger.py         # ToolLogger + serve.log
│   ├── serve/
│   │   ├── orchestrator.py       # discovery → spawn → serve MCP stdio
│   │   └── proxy_tool.py         # synthesized BaseTool that forwards via MCP
│   └── templates/                # init scaffolding
├── tests/
│   ├── test_categories_api.py
│   ├── test_interactive_flags.py
│   ├── test_orchestrator_url_no_slash.py     # sentinel for /mcp gotcha
│   └── e2e/                                  # mocked-registry harnesses
└── docs/
    ├── SERVE_ARCHITECTURE.md     # canonical serve design
    ├── SETUP_SYSTEM_SPEC.md      # Phase 3C (file-first per 2026-05-06 revision)
    └── SETUP_RECIPES.md
```

## Useful commands

```bash
# Activate dev venv
source tb-package/test-venv-312/bin/activate

# Run unit tests
python -m pytest tb-package/tests/ --ignore=tb-package/tests/e2e -q

# Run sentinel test (gotcha #1)
python -m pytest tb-package/tests/test_orchestrator_url_no_slash.py -v

# Full e2e (network-free, ~15s)
python tb-package/tests/e2e/run_install_e2e.py
python tb-package/tests/e2e/run_serve_e2e.py

# Quick CLI sanity
toolbase list
toolbase logs --no-follow -n 30      # tail serve.log

# Local install (editable)
pip install -e tb-package/
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

- **A bug, a question about something I shouldn't change:** HANDOFF.md.
- **A future-direction or "what's next" question:** STATUS.md.
- **A new architectural principle:** STATUS.md §"Named principles," after
  manager review.
- **An inter-agent change request (e.g. "frontend wants X"):**
  `MESSAGES_TO_AGENTS/<date>_to_<agent>__<topic>.md`.

---

**Last updated:** 2026-05-06.
