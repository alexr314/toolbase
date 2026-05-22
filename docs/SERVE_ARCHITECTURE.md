# `toolbase serve` — Architecture (Implemented)

> **✅ IMPLEMENTED as of 2026-05-05 (MVP).** This document is the canonical reference for how serve works. The architecture described here is the shipped architecture. Sections explicitly marked "later" or "deferred" (TUI, restart logic, `--call-timeout` flag) are still future work; the rest is live.

**Status:** Implemented (Phase 3A complete)
**Author:** Package Agent
**Date:** 2026-05-04 (design) → 2026-05-05 (implemented and verified end-to-end)
**Scope:** Phase 3A serve command — `--no-tui` mode (TUI deferred post-MVP).
**Out of scope:** Docker mode (3B), setup-system implementation (3C), TUI implementation, hot reload.

---

## 0. TL;DR

The shape your previous direction laid out — orchestrator process + per-toolkit
subprocess running the toolkit's own interpreter, JSON-RPC over stdio between
them — is essentially correct. **The concrete recommendation is to use
MCP-over-HTTP as that JSON-RPC protocol**, because Orchestral 1.3.0 already
ships a working server (`create_fastmcp_server`) and a persistent-session
client (`MCPClient(url=...)`) that does this exact thing. Inventing a parallel
JSON-RPC protocol would duplicate what Orchestral already provides, and the
shipped stdio transport in `MCPClient` does not maintain persistent sessions
(it spawns a fresh subprocess per call, which breaks stateful tools).

This means the runtime topology is:

```
┌─ Claude Code (MCP client) ─┐
            │ MCP stdio
┌───────────▼─────────────────────────────┐
│ toolbase serve (orchestrator)         │
│   • exposes MCP stdio server upstream   │
│   • multiplexes to per-toolkit subprocs │
│   • namespaces tool names               │
└─┬───────────────┬───────────────────────┘
  │ MCP HTTP      │ MCP HTTP
  │ (loopback)    │ (loopback)
┌─▼─────────────┐ ┌─▼─────────────┐ …
│ aster subproc │ │ heptapod subp │
│ (its venv)    │ │ (its conda)   │
│ FastMCP HTTP  │ │ FastMCP HTTP  │
│ + Orchestral  │ │ + Orchestral  │
│ tool insts    │ │ tool insts    │
└───────────────┘ └───────────────┘
```

A few things I want flagged for your review before writing code:

1. **HTTP, not stdio, between orchestrator and per-toolkit subprocesses.**
   Reason in §1.2.
2. **Orchestral's `Agent` class has no `tool_config=` parameter as of 1.3.0.**
   The "stateful tools proposal" exists in `BaseTool` (StateField, `_setup`,
   `_restore_state_field_values`) but the Agent-level injection sugar isn't
   there. We do state injection ourselves by setting attributes on tool
   instances. This works today; details in §2.3.
3. **`@define_tool` does not accept a `state=[…]` argument** in shipped 1.3.0.
   Authors using state today must subclass `BaseTool` directly and declare
   `StateField` class attributes. If toolkit authors are expected to use
   `@define_tool(state=…)`, that's an Orchestral feature gap to file upstream.
   Details in §2.3.

If any of those three blocks the design, we should resolve them before I build.

---

## 1. Process model

### 1.1 Orchestrator + per-toolkit subprocess

A single **orchestrator** process is what `toolbase serve` launches. It is
the long-running parent that:

- Discovers installed toolkits by walking `~/.toolbase/toolkits/*/.stk_meta.json`.
- Launches one **per-toolkit subprocess** for each healthy toolkit, using that
  toolkit's own Python interpreter (its venv or conda env).
- Exposes its own MCP stdio server upward (to Claude Code).
- Routes tool calls down to the correct per-toolkit subprocess.
- Owns the lifecycle: start, monitor, restart, shutdown.

Each per-toolkit subprocess:

- Imports the toolkit's tools (via `tools/__init__.py`, the convention the
  templates already establish).
- Performs state injection on the tool instances using values loaded from a
  toolkit-local config (env file or yaml — exact source is Phase 3C's call).
- Runs `create_fastmcp_server(tools, ...).run(transport="streamable-http")` on
  a loopback port allocated by the orchestrator.
- Lives until the orchestrator tells it to shut down or it crashes.

I confirm the model is workable. The two trade-offs I considered and dropped:

- **In-process (everything in the orchestrator's interpreter).** Loses the
  ability to use the toolkit's own Python version and isolated deps, which is
  the whole point of the venv/conda install split. Hard no.
- **Subprocess-per-call.** Orchestral's `MCPClient` stdio mode does this today.
  Re-runs `_setup()` and re-restores state on every call. Slow if state setup
  is non-trivial (e.g., loading a 100MB ephemeris file). Hard no for a
  scientific toolkit serving framework.

### 1.2 Why MCP-over-HTTP between orchestrator and subprocesses, not stdio

This is the most consequential decision in the doc. Orchestral 1.3.0 ships
both transports, but their stdio support has a subtle but critical limitation
for our use case:

> `MCPClient._call_tool_stdio_async` — "Call a tool via STDIO transport
> (ephemeral subprocess per call)."
> ([orchestral/mcp/client.py:195](file:///path/to/orchestral/mcp/client.py))

That is, Orchestral's stdio MCPClient **respawns the server subprocess on
every `call_tool`**, throwing away any state the tool has built up. The HTTP
client, by contrast, holds a persistent session in a background event loop
thread for the life of the connection. So:

- If we use stdio: every tool call costs a fresh interpreter startup + tool
  re-import + `_setup()`. For a stateful tool with `_setup` that loads big
  reference data, that's seconds per call. And state set via direct attribute
  assignment doesn't persist to the next call.
- If we use HTTP (loopback only): the per-toolkit subprocess runs continuously,
  tool instances are long-lived, state persists across calls. Cost is one
  loopback TCP port per active toolkit and a per-call HTTP roundtrip on
  localhost (sub-millisecond).

We could write our own persistent-stdio MCPClient — the upstream HTTP one
shows the pattern clearly — but that's a parallel implementation we'd have to
maintain. Better to either:

- (a) Use HTTP loopback today (recommended). Works with Orchestral as-shipped.
- (b) File an Orchestral feature request for "persistent-stdio MCPClient" and
      switch when it lands. No urgency.

**Recommendation: (a).** Loopback HTTP on auto-allocated ports. Local-only,
no firewall implications, the cost is one bound socket per toolkit.

### 1.3 Subprocess launch mechanics

Per toolkit, the orchestrator computes the launch command from `.stk_meta.json`:

```python
if meta["environment"] == "venv":
    python_exe = meta["python_path"]  # "<toolkit>/.venv/bin/python"
    cmd = [python_exe, "-m", "toolbase._toolkit_host", "--toolkit", name, "--port", str(port)]
elif meta["environment"] == "conda":
    env_name = meta["env_name"]      # "toolbase-<name>"
    cmd = ["conda", "run", "--no-capture-output", "-n", env_name,
           "python", "-m", "toolbase._toolkit_host", "--toolkit", name, "--port", str(port)]
```

Notes:

- `toolbase._toolkit_host` is a small new module we ship inside `toolbase`
  itself. It's the entrypoint that runs inside the toolkit's interpreter. It
  only needs `orchestral-ai`, `mcp`, and Python stdlib — all of which install
  alongside the toolkit's deps already (we install `orchestral-ai` into every
  toolkit env at install time; see `setup_venv_environment` and
  `setup_conda_environment` in `cli.py`). The orchestrator does not need to
  import or vendor the host module across env boundaries; it just needs to be
  importable from a `python -m` inside the toolkit's environment.
- `conda run --no-capture-output` is required so that the subprocess's stderr
  reaches our orchestrator for diagnostics; without it, conda buffers stderr
  until the subprocess exits.
- Port allocation: orchestrator picks `0`, binds, reads back the actual port
  from a small handshake the host writes to its stdout's first line (so the
  subprocess can actually be told what port it bound to). Or simpler: have the
  host claim a port from the OS by binding `127.0.0.1:0`, then write the bound
  port back to its stdout as JSON, and the orchestrator reads that line before
  attempting to connect.

The handshake protocol on stdout's first line is just:
`{"port": 53991, "tools": ["analyze_star", "plot_transit"]}` — emitted by
`_toolkit_host` after FastMCP has bound but before `mcp.run()`. After that
line the subprocess goes silent on stdout (subsequent stderr is captured to
`~/.toolbase/logs/<toolkit>.log`).

### 1.4 Wire protocol summary

| Hop | Direction | Protocol | Transport |
|---|---|---|---|
| Claude Code ↔ orchestrator | bidirectional | MCP | stdio |
| Orchestrator ↔ host | bidirectional | MCP | HTTP loopback |
| Orchestrator → host (lifecycle) | one-way | JSON line | host's stdin (close = shutdown) |

The "JSON line on stdin" channel is for orchestrator-initiated shutdown only
(e.g., `{"action": "shutdown"}`). Closing the pipe also terminates the host —
that's the primary shutdown path. The line-based channel is a graceful
escape.

### 1.5 Why not roll a custom JSON-RPC

We could define our own protocol (`{"method": "list_tools"}`, etc.). I dropped
it because:

- MCP *is* JSON-RPC. We'd be reinventing it.
- Orchestral's FastMCP server already turns Orchestral tools into a working
  MCP endpoint. Free.
- Future-proofing: if someone wants to point a non-Orchestral MCP-aware
  client at a single per-toolkit subprocess directly (e.g., for debugging),
  the subprocess is already a standards-compliant MCP server.

---

## 2. MCP layer

### 2.1 Upstream: orchestrator as MCP stdio server

The orchestrator runs Orchestral's `MCPServer` (`orchestral.mcp.server`) in
stdio mode. The tools it exposes are not native Orchestral tools — they are
proxy tools generated from each child's tool list. See §2.2.

Wiring is:

```python
from orchestral.mcp import MCPServer

proxy_tools = orchestrator.get_aggregated_proxy_tools()
server = MCPServer(tools=proxy_tools, name="toolbase", use_display_names=False)
server.run()  # blocks on stdio
```

`use_display_names=False` because we already namespace with `<toolkit>__<tool>`
in §2.3, and we do not want a second PascalCase rename layered on top.

### 2.2 Downstream: orchestrator as MCP HTTP client to each child

For each per-toolkit subprocess, the orchestrator holds an
`MCPClient(url="http://127.0.0.1:<port>/mcp")` with a persistent session
(connected once, kept open). It calls `client.get_orchestral_tools()` once at
connection time to discover the child's tools, then wraps each one as a
proxy tool with a namespaced name. When Claude Code calls the namespaced
name, the orchestrator's proxy tool invokes the child via the persistent
client session.

`MCPClient`'s persistent HTTP session runs its own thread + event loop, which
matches what we need: each per-toolkit subprocess is independently long-lived
and the orchestrator should be able to call them concurrently without
blocking.

### 2.3 Tool name namespacing

Names are `<toolkit_name>__<tool_name>` (double underscore). So `aster`'s
`analyze_star` becomes `aster__analyze_star`. The double underscore is unlikely
to collide with anything legal in a Python tool name (single underscores are
common; double underscores in identifiers are rare and usually intentional).

**Mechanism:** Orchestral's `MCPServer` uses `tool.get_name()` (from
`BaseTool.get_name()`, which by default returns the class name lowercased).
We can't trivially override the *served* name from outside the tool class
without rebuilding the proxy tool. So the cleanest approach is: each proxy
tool is a tiny `BaseTool` subclass we synthesize at orchestrator startup,
with `get_name()` returning the namespaced name.

```python
class ProxyTool(BaseTool):
    def get_name(self) -> str:
        return self._namespaced_name
    def _run(self):
        return self._child_client.call_tool(self._upstream_name, self._kwargs)
```

The schema for the proxy is forwarded straight from the child's MCP listing
(input schema, description), so the LLM sees what the child sees. State
fields are already filtered out of input schemas by Orchestral's
`SchemaGenerator` before they ever leave the child, so the orchestrator
doesn't need to worry about state-field redaction.

### 2.4 State injection (where it actually happens)

Stateful tools need their state fields populated *before* being served. This
happens **inside the per-toolkit subprocess (`_toolkit_host`)**, not in the
orchestrator. The host:

1. Imports the toolkit's tools.
2. Loads a state config (file path TBD by 3C — could be `<toolkit>/.env`,
   `<toolkit>/toolbase.config.yaml`, or a user-level config). For now, the
   host accepts an empty state config and skips injection — the failure mode
   is that stateful tools run with default state-field values.
3. For each tool, for each state field declared on it, sets the corresponding
   attribute on the tool instance and re-runs `_setup()` if values changed.
4. Hands the now-configured tool instances to `create_fastmcp_server`.

The orchestrator does not see state. It only sees the schema-redacted
runtime-field interface that the child exposes via MCP.

**Caveat to flag:** Orchestral 1.3.0's `@define_tool` does not accept
`state=[…]` — that signature was mentioned in earlier direction but is not
shipped. Today's pattern is to subclass `BaseTool` and declare state fields
via `StateField(...)`. If a toolkit author *does* use the not-yet-shipped
`@define_tool(state=[...])` syntax, our host will gracefully ignore the
unknown kwarg (the decorator already accepts arbitrary kwargs in
`_define_tool_impl` via `**dkwargs` flow). When Orchestral ships the feature,
no toolbase changes are needed — `define_tool` will start producing classes
with state fields, and our existing injection loop picks them up.

### 2.5 Schema generation

For stateless tools: schema is what `BaseTool.get_tool_spec().input_schema`
produces — Orchestral does this from Pydantic field annotations.

For stateful tools: same, because `SchemaGenerator.generate_input_schema`
already excludes any field with `is_state_field=True` in its
`json_schema_extra`. Confirmed by reading
`tools/base/schema_generator.py:22-26`.

So neither the orchestrator nor toolbase needs to do schema munging. We
forward what we get.

---

## 3. Lifecycle

### 3.1 Startup

1. `toolbase serve` parses args (e.g., `--no-tui`, `--toolkit aster`
   to scope to one for debugging).
2. Walks `~/.toolbase/toolkits/*/.stk_meta.json`. Skips:
   - Directories without metadata (broken installs — same as `list`).
   - Toolkits with `meta["environment"] == "docker"` (Phase 3B). Logs a clear
     "skipping, docker mode not supported yet" line.
   - Toolkits with `meta["needs_setup"] == True` (these are the
     `setup.py`-bearing toolkits we already detect during install). Logs a
     "skipping, setup not yet run" line. This is the **forward-compat hook
     for 3C** — when 3C lands, this gate becomes `validate(ctx)` instead of a
     simple `needs_setup` check.
3. For each remaining toolkit, **launches its host subprocess eagerly**.
   Reasoning below.
4. Reads each subprocess's first stdout line (the handshake) to learn its
   bound port.
5. Connects an `MCPClient` to each subprocess with timeout. Aggregates tools.
6. Builds proxy tools, namespaces names, instantiates `MCPServer` on stdio,
   blocks on `server.run()`.

If step 5 fails for a toolkit (subprocess started but client can't connect or
list tools), the orchestrator **shuts that subprocess down and excludes it
from the served tools**, but keeps serving the rest. Logs the failure clearly.

**Eager vs. lazy launch.** Eager wins on first impressions: when Claude Code
calls `tools/list`, the orchestrator already has the full set ready, no
multi-second pause to spin up subprocesses on demand. Lazy wins on memory if
the user has 20 installed toolkits and only uses 2. For now, eager is right —
toolkit count is small, scientists are not running a casual chat with 20
toolkits open. Lazy can be a later flag if it becomes a real problem.

### 3.2 Per-call routing

```
Claude Code → tools/call({"name": "aster__analyze_star", "args": {...}})
        ↓ MCP stdio
orchestrator: looks up aster__analyze_star → child client for aster
        ↓ MCP HTTP loopback
aster host: dispatches to analyze_star tool instance, executes, returns string
        ↑
orchestrator: wraps in MCP TextContent, returns
        ↑
Claude Code: receives result
```

The orchestrator does no business logic per call. It's a name-namespacing
proxy.

### 3.3 Subprocess crash mid-call

Two cases:

**A. Subprocess died but the call hadn't returned yet.** The HTTP request
fails with a connection error. The proxy tool catches it and returns an MCP
error result with `isError=True`. The orchestrator marks the toolkit as
unhealthy and triggers async restart in the background.

**B. Subprocess died between calls (e.g., OOM killed by the system).** Next
call gets a connection-refused error. Same treatment: error result, async
restart.

Restart policy: at most 3 restarts per toolkit per orchestrator session,
exponential backoff (1s, 4s, 16s). After 3 failures the toolkit is marked
permanently unavailable and excluded from the namespace. This is conservative
on purpose — a tool that crashes repeatedly is more likely a bug than a
transient flake, and silently restarting forever masks the bug.

Tools/list must be regenerated when toolkit availability changes. MCP supports
sending a `notifications/tools/list_changed` notification to the client, which
Claude Code respects. The orchestrator emits one when a toolkit is removed
from the namespace.

### 3.4 Tool call timeout

Default: **60 seconds per call.** Configurable via `--call-timeout` flag.
Rationale: scientific tools can be expensive (a forward-modeling call on
exoplanet data may legitimately take a minute), but a 60-second cap on Claude
Code's request side is roughly the typical agent's patience. If the call
exceeds the timeout, the orchestrator returns an MCP error and the per-toolkit
subprocess is left alone (it may complete its own work; we just don't wait).

If the same tool times out repeatedly we may want to raise the cap, but that's
a per-toolkit override and we can defer to a later iteration.

### 3.5 Tool call exception inside the subprocess

Already handled by Orchestral's `MCPServer._handle_tools_call` (see
`orchestral/mcp/server.py:140-150`): exceptions are caught and turned into
`{"isError": True, "content": [{"text": "Error: …"}]}`. We don't need to do
anything extra. This propagates correctly through the orchestrator's proxy
because we just forward the result.

### 3.6 Orchestrator dies

If `toolbase serve` itself dies (Ctrl-C, OOM, segfault), Claude Code loses
its MCP connection and surfaces that to the user. The per-toolkit subprocesses
are children of the orchestrator and will receive SIGTERM via the OS process
group on graceful exit, or SIGKILL on hard kill. Either way, no zombies.

For graceful Ctrl-C: orchestrator catches `SIGINT`, sends shutdown over each
child's stdin (the JSON-line lifecycle channel), waits up to 5 seconds, then
falls back to SIGTERM, then SIGKILL.

### 3.7 Runtime enable/disable (TUI hook)

Each toolkit goes through a small state machine in the orchestrator:

```
DISCOVERED → STARTING → READY ─ (call) → READY
                       ╲      ╲
                        ╲      └→ CRASHED → STARTING (if restart budget)
                         └→ FAILED (no restart budget, terminal)
                READY → STOPPING → STOPPED  (user toggle off)
              STOPPED → STARTING            (user toggle on)
```

The TUI (later) reads this state and renders the toolkit list with status
icons. It can request transitions via an in-process API — see §6.

### 3.8 Lifecycle diagram

```
                ┌─────────────────────┐
                │ toolbase serve    │
                │  starts             │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │ walk TOOLKITS_DIR   │
                │ filter docker, etc. │
                └──────────┬──────────┘
                           │
                ┌──────────▼─────────────────────┐
                │ for each healthy toolkit:      │
                │   spawn host subprocess        │
                │   read handshake (port, tools) │
                │   connect MCPClient (HTTP)     │
                │   build proxy tools            │
                └──────────┬─────────────────────┘
                           │
                ┌──────────▼──────────────────────┐
                │ MCPServer(stdio).run()  ◄──── claude code talks here
                │ blocks until SIGINT             │
                └──────────┬──────────────────────┘
                           │
                ┌──────────▼─────────────────────┐
                │ on shutdown: graceful → term  │
                │ → kill, waitpid, cleanup      │
                └────────────────────────────────┘
```

---

## 4. Failure modes

A non-exhaustive list, with deliberate behavior for each.

| Failure | Symptom seen by orchestrator | Action |
|---|---|---|
| Subprocess fails to spawn (bad interpreter path) | `subprocess.Popen` raises | Skip toolkit, log error, keep serving rest |
| Subprocess imports fail (missing dep, syntax error) | Subprocess exits non-zero before handshake | Read stderr, log it, skip toolkit, keep serving |
| Subprocess never writes handshake | Handshake read times out (5s) | Kill subprocess, skip toolkit, log timeout |
| MCPClient can't connect | `connect()` raises `TimeoutError` | Kill subprocess, skip toolkit, log |
| `tools/list` from child returns empty | Connection succeeded, no tools | Log warning, keep child running for parity (so a future tool registration works), but expose nothing |
| Tool call times out | HTTP call exceeds `--call-timeout` | Return MCP error, leave child alone |
| Tool call raises in child | Child returns `isError=True` MCP result | Forward as-is to upstream |
| Subprocess crashes mid-call | HTTP raises connection error | Return MCP error, mark child unhealthy, schedule restart |
| Subprocess crashes between calls | Next HTTP call gets connection refused | Same as above |
| Orchestrator can't bind upstream stdio | MCPServer.run() raises | Fatal; print error and exit 1 |
| Restart budget exhausted | 3 failures | Mark toolkit permanently unavailable, send `tools/list_changed` |
| Port collision (another process grabbed our chosen port) | Bind error in child | Child exits, orchestrator sees no handshake → skip; kernel-allocated port should make this rare |

The pattern: **one bad toolkit never poisons the rest of the server.** This
matters for users with 5-10 toolkits installed — losing one shouldn't take
down the others.

---

## 5. Setup system hook (forward compat for Phase 3C)

The hook point is at orchestrator startup, in step 2 of §3.1. Today the gate
is:

```python
if meta.get("needs_setup"):
    skip_with_message(name, "setup not yet run; install with --run-setup once 3C lands")
    continue
```

When 3C lands, the gate becomes:

```python
ctx = SetupContext(toolkit_dir=path, config=load_setup_config(path))
result = setup_module.validate(ctx)  # provided by toolkit's setup.py
if not result.ok:
    skip_with_message(name, f"setup invalid: {result.reason}")
    continue
```

The change touches one function in the orchestrator (`should_load_toolkit` or
similar). Nothing else in the architecture moves. The setup-config dict
loaded by 3C also feeds the host's state-injection step (§2.4), so the wiring
is: orchestrator loads config → passes it to host as a CLI arg or env var →
host injects it into tool instances before serving.

**Concrete forward-compat shape:**

```python
# orchestrator side
host_cmd = [..., "--state-config", json.dumps(loaded_setup_config)]

# host side
if args.state_config:
    state = json.loads(args.state_config)
    for tool in tools:
        for field_name, value in state.items():
            if hasattr(tool, field_name) and field_name in tool._get_state_fields():
                setattr(tool, field_name, value)
    for tool in tools:
        tool._setup()  # re-run with restored state
```

That host snippet works *today* with an empty `state` dict. 3C just fills it
in.

---

## 6. TUI hook (forward compat for later in 3A)

The architecture exposes one in-process API for TUI consumption. Sketch:

```python
class Orchestrator:
    def list_toolkits(self) -> list[ToolkitStatus]: ...
    def get_tool_call_log(self, since_id: int = 0) -> list[CallLogEntry]: ...
    def request_toolkit_state(self, name: str, target: Literal["enabled", "disabled"]) -> None: ...
    def subscribe_events(self) -> AsyncIterator[OrchestratorEvent]: ...
```

Where:

- `ToolkitStatus` carries `(name, version, env_type, state, tools_count, error?)`.
- `CallLogEntry` carries `(id, timestamp, toolkit, tool, args_summary, status, duration_ms)`.
- `OrchestratorEvent` is a small union: `ToolkitStateChanged | ToolCallStarted |
  ToolCallCompleted | ToolCallFailed`.

Why these specifically:

- The TUI's left panel reads `list_toolkits()` for the toolkit table.
- The TUI's right panel reads `subscribe_events()` for the live log.
- The TUI's space-bar toggle calls `request_toolkit_state()`.

When `--no-tui` is in effect, none of these are exercised. But by having the
orchestrator's internal state already organized this way, the TUI is "just
another consumer" — no refactor needed when we add it.

The events stream is in-process only (no IPC overhead). The TUI runs in the
same Python process as the orchestrator, in a separate Textual `App` thread,
talking to the orchestrator over a `queue.Queue` or `asyncio.Queue`. The MCP
stdio stays bound to the parent process's stdin/stdout — Textual's rendering
goes to `/dev/tty` directly when needed (it does this already in standalone
mode), or is suppressed when `--no-tui`.

I'm flagging one risk here: Textual's default mode reads from stdin, which is
also where the MCP stdio server reads from. **The TUI mode and the MCP stdio
mode cannot share stdin.** When TUI is active, the MCP transport must be
something else — probably HTTP loopback on a fixed port, with Claude Code
configured against that URL instead. This was implicit in the earlier plan
("--no-tui" as escape hatch for debugging) but worth making explicit:

- **`toolbase serve --no-tui`**: stdio MCP, no TUI. This is what Claude Code
  will use day-to-day.
- **`toolbase serve` (with TUI)**: HTTP MCP on a fixed port, TUI on stdio.
  Used for development / observation.

Or alternatively, the TUI runs in a *different terminal* and talks to a
running headless serve via its in-process API exposed over a unix socket.
Cleaner separation, more plumbing. We can pick this up when we actually build
the TUI.

---

## 7. Things to flag (open questions for you)

These are the specific things I can't resolve unilaterally:

1. **Orchestral's stdio MCPClient is ephemeral-per-call.** Use HTTP loopback
   (recommended in §1.2), or file an upstream feature request, or roll our
   own persistent-stdio client?
2. **Orchestral 1.3.0 has no `Agent(tool_config=…)` and no `@define_tool(state=[…])`.**
   The `BaseTool` + `StateField` machinery is there. The injection sugar
   isn't. Do we proceed with manual injection (works today), or wait for
   Orchestral to ship the convenience API?
3. **Eager vs. lazy subprocess launch.** I picked eager in §3.1. If you've
   seen real toolkits with 30-second cold starts, lazy may be worth it.
4. **Restart budget of 3 with exponential backoff.** Conservative; happy to
   tune if you've got a different intuition.
5. **Default tool call timeout of 60s.** Round number. Real scientific
   workflows may want longer; we can per-toolkit-override later.
6. **TUI / MCP stdio collision (§6 last paragraph).** We need to commit to
   either "TUI uses HTTP MCP" or "TUI runs in a separate process" before
   the TUI work begins. Doesn't block the `--no-tui` MVP.

---

## 8. What I'd build first if this lands

For your review only — not asking permission yet. After this doc is approved
I'd build, in this order:

1. **`toolbase/_toolkit_host.py`** — the per-toolkit subprocess entrypoint.
   Imports tools, runs FastMCP HTTP, emits handshake. Maybe 80 lines.
2. **`toolbase/serve/orchestrator.py`** — discover toolkits, spawn hosts,
   connect clients, build proxy tools, start MCPServer(stdio). Maybe 200 lines.
3. **`toolbase/serve/proxy_tool.py`** — the synthesized BaseTool subclass
   that forwards a call to a child MCPClient. Maybe 50 lines.
4. **Wire `cli.serve` to invoke the orchestrator** with `--no-tui`. Replace
   the placeholder.
5. **End-to-end test against a real installed toolkit**: install a
   2-tool synthetic toolkit, point Claude Code's MCP config at
   `toolbase serve`, verify both tools list and one returns a result.
6. **Then** tune restart/timeout/error reporting against real failure modes.
7. **Then** consider the TUI.

Conda mode for the host process should work without extra code (the launch
command in §1.3 already covers it), but I'd verify it as a separate vertical
once venv mode is solid.

---

## 9. What I'm not building

- **Anything in the doc above that says "Phase 3B" or "Phase 3C" or "later".**
- **Hot reload of toolkits.** Restart `toolbase serve` to pick up new
  installs. We can revisit if it becomes a real friction point.
- **Distributed serve** (orchestrator on one host, toolkits on another). Out
  of scope; the MCP HTTP transport could in principle support it but the
  install/auth model isn't built for it.

---

End of proposal. Ready for review.
