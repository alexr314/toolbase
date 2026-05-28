# Manual post-ship verification: arxiv-search

**Purpose:** Catch drift between dev work and the live registry by
exercising the full create → install → serve → call loop against
production. The automated e2e harnesses use mocked-registry
fixtures; this checklist hits the real `api.scitoolkit.org`.

**When to run:**
- Before each PyPI release of `toolbase` (release ritual).
- Whenever a CLI change touches `install`, `serve`, or
  authentication — even passing automated e2es leave room for
  registry-side drift.
- After every backend deploy that changes the publish/upload or
  fetch endpoints.

**Time required:** ~5 minutes.

**Non-CI:** these steps require the live registry, network, and
optionally a fresh shell. Don't try to bake them into pytest.

---

## Pre-flight

- [ ] You're on a clean dev venv with the just-built toolbase
      installed. Check with:
      ```bash
      toolbase --version
      ```
- [ ] Network reachable to `api.scitoolkit.org`. There's no
      dedicated `/api/health` endpoint; instead probe a real one:
      ```bash
      curl -sf https://api.scitoolkit.org/api/toolkits/arxiv-search \
        > /dev/null && echo OK
      ```
- [ ] If `arxiv-search` is already installed, that's fine — the
      install step will reinstall in place. Reinstall preserves
      no user state because arxiv-search ships no `config:` block.
      Confirm with:
      ```bash
      toolbase list | grep -i arxiv
      ```

---

## Step 1 — Confirm the registry has the toolkit

`toolbase search` is still a placeholder as of 0.3.0
("Search is not available yet."). Until it ships,
this step is a direct API check or a website browse:

```bash
curl -s https://api.scitoolkit.org/api/toolkits/arxiv-search \
  | python -c "
import json, sys
d = json.load(sys.stdin)
print(f'name={d[\"name\"]}, version={d[\"version\"]}, '
      f'category={d[\"category\"]}, downloads={d[\"downloads\"]}')
"
```

**Expected:** prints something like `name=arxiv-search,
version=0.1.0, category=other, downloads=N`.

- [ ] Registry confirms toolkit exists with non-empty version
- [ ] (Optional) browse https://toolbase-ai.com/toolkit/arxiv-search
      and confirm the detail page renders

---

## Step 2 — Install

```bash
toolbase install arxiv-search --no-input
```

**Expected:**
- Downloads tarball from the registry (progress bar)
- Creates a venv under `~/.toolbase/toolkits/arxiv-search/.venv`
- Installs deps + orchestral-ai + mcp
- No "Phase 3C-2 not yet runnable" warning (lifted in 3C-2 close)
- "Successfully installed arxiv-search vX.Y.Z" final line

Check artifacts:
- [ ] `~/.toolbase/toolkits/arxiv-search/.tb_meta.json` exists
- [ ] `~/.toolbase/toolkits/arxiv-search/tools/__init__.py` exists
- [ ] No `~/.toolbase/config/arxiv-search.yaml` (arxiv-search
      ships no `config:` block — its absence is the sentinel that
      the no-config path still works)

---

## Step 3 — Serve

In one terminal:

```bash
toolbase serve --no-tui
```

**Expected:**
- "Checking installed toolkits..." line
- "✓ arxiv-search ready (2 tools)" — exactly 2 tools (search + get)
- "Starting MCP server with 1 toolkit (2 tools)..." final line
- The process holds open (waiting for MCP stdio client)

In a second terminal, tail the log:

```bash
tail -n 50 ~/.toolbase/logs/serve.log
```

- [ ] Log shows orchestrator startup with no errors
- [ ] No "validate(ctx) failed" warning (arxiv-search has no setup.py)

---

## Step 4 — Call a tool

Two paths. The Claude Code path exercises end-to-end including
MCP stdio; the in-process path exercises just the orchestrator
+ tool subprocess. Run whichever fits your situation.

**Path A — Claude Code (full end-to-end):**

With Claude Code already configured with `toolbase serve` as
an MCP server, prompt:

> Search arxiv for recent papers on "transformer attention".

**Expected:** Claude calls `arxiv-search__search_arxiv`, gets a
JSON response with paper titles, summarizes them.

**Path B — orchestrator in-process (no MCP layer):**

```bash
python -c "
from toolbase.serve.orchestrator import Orchestrator
from pathlib import Path
orch = Orchestrator(toolkits_dir=Path.home() / '.toolbase' / 'toolkits')
orch.start()
proxies = {p.get_name(): p for p in orch._proxy_tools}
result = proxies['arxiv-search__search_arxiv'].execute(
    query='transformer attention', max_results=2,
)
print(result[:300])
orch.shutdown()
"
```

(Note: piping a single JSON-RPC line into `toolbase serve` won't
work as the original checklist suggested — MCP servers expect an
`initialize` handshake first. The in-process path above sidesteps
that complexity for the smoke-test goal.)

- [ ] Tool returned a non-empty JSON response
- [ ] Response contains paper records (`arxiv_id`, `title`,
      `authors`, `summary` fields)
- [ ] `~/.toolbase/logs/serve.log` shows `tool_complete` (Path A)
      or `tool_invoked` (Path B) for the call

---

## Step 5 — Stop and clean up (truly optional)

Skip this step if you actually use `arxiv-search` from Claude
Code — uninstalling will detach Claude Code's tool calls. Run
only on a dedicated dev machine or if you don't mind reinstalling.

```bash
# Ctrl-C the serve process in terminal 1
toolbase uninstall arxiv-search --yes
```

- [ ] Uninstall removes the toolkit dir cleanly
- [ ] `toolbase list` no longer shows arxiv-search

---

## What "drift" looks like

Common ways this checklist catches problems automated e2es miss:

- **Backend schema change:** the registry returns a field shape the
  CLI doesn't recognize; install crashes with a Pydantic validation
  error. Caught at Step 2.
- **Token format change:** if `auth.py` changes and a registry that
  expected the old format rejects the request, search/install fail
  with HTTP 401. Caught at Step 1.
- **MCP serialization drift:** a serialization change in Orchestral
  or toolbase's proxy_tool that breaks tool invocation through
  MCP stdio. Caught at Step 4.
- **arxiv-search version regression:** if the live arxiv-search was
  somehow republished with a broken config: block or setup.py, this
  catches it before a user does.

---

## If a step fails

- Capture the full output. The failure mode dictates the fix:
  - Step 1 / 2 failures → backend or auth issue. Coordinate with
    Backend Agent.
  - Step 3 failures → orchestrator or host wiring. Check serve.log,
    likely a package-side issue.
  - Step 4 failures → MCP layer or tool injection. Check
    `~/.toolbase/logs/arxiv-search.log` (per-toolkit log) for
    subprocess stderr.
- Don't ship the release until the failure is understood and
  fixed — registry-touching breakage hits every user immediately.
- Open a `MESSAGES_TO_AGENTS/<date>_to_<role>__arxiv_postship_drift.md`
  with the captured output if it's something that needs other
  agents.

---

## History

- **2026-05-06:** Checklist established alongside Phase 3C-3 close.
  Origin: STATUS.md "release ritual" concept (manager).
- **2026-05-06 (same day):** First smoke-test run on the in-dev
  toolbase (just past 3C-3 close). Findings folded back in:
  - Pre-flight: dropped the `/api/health` probe (no such endpoint;
    use `/api/toolkits/<name>` instead).
  - Step 1: `toolbase search` is still a placeholder; replaced
    with a direct `/api/toolkits/<name>` curl + optional website
    browse.
  - Step 4: the raw single-line MCP JSON-RPC pipe doesn't work
    against a real MCP server (initialize handshake is required);
    replaced with an in-process orchestrator path that's easier
    to drive from a terminal.
  - Step 5: marked as "truly optional" — uninstalling detaches
    Claude Code if the user actually uses the toolkit.

  All other steps verified working: install, serve, tool call
  (returns real arXiv papers). The checklist is now safe for
  release-ritual use.
- **2026-05-06 (later):** Re-run against the wheel-built CLI as
  step 7 of the 0.3.0 release plan. All four active steps pass
  cleanly under `pip install toolbase-0.3.0-py3-none-any.whl`
  in a fresh venv. One Step-1 quoting fix folded in
  (`python -c '...d[\"name\"]...'` was zsh-fragile; switched to
  a heredoc-style block). External flake observed: arxiv API
  occasionally times out — the platform handles it correctly
  (returns a structured `{"status": "error", ...}` JSON
  response), retry succeeds.
