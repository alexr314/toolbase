# Provenance trace

An optional trace of every toolkit call `toolbase` serves: which agent made
it, which tool at which toolkit version, the arguments, the outcome, and the
files it named. Off by default. With it off, nothing below happens and serve
behaves exactly as before.

toolbase writes the trace; [tooltrace](https://github.com/tonymenzo/tooltrace)
reads it. tooltrace owns the trace format (its `docs/FORMAT.md`, which this
page's entry sections follow) and turns a trace into the run's record, a shareable directory with
an offline viewer. See [Export and viewing](#export-and-viewing).

## Turning it on

| How | Scope |
|---|---|
| `tb trace on` / `tb trace off` | the project: writes `trace: true\|false` to `<project>/.toolbase/serve.yaml`; every `toolbase serve` started there afterwards traces. A running serve picks it up at its next start. |
| `TOOLBASE_TRACE=1` (or `0` to force off) | one session, e.g. `TOOLBASE_TRACE=1 claude`; the harness passes its environment to the serve it starts |
| `TOOLBASE_TRACE_DIR=<dir>` | where that session writes (the trace directory itself); does not turn tracing on by itself |
| `toolbase serve --trace [DIR]` | one hand-started serve |
| `Orchestrator(..., trace=Trace(dir, agent=...))` | in-process embedders (Conjure) |

Precedence for `serve`: `--trace`, then `TOOLBASE_TRACE`, then serve.yaml
(project over user). Directory: `--trace DIR`, then `TOOLBASE_TRACE_DIR`, then
`<project>/.toolbase/traces/<session-id>/` with a sortable session id such as
`20261115T221803Z-3fa9c2`. Each serve start is one session.

`tb trace status` says whether a serve started here would trace and why;
`tb trace list` lists sessions (agents, calls, failures, time span);
`tb trace show <session|dir>` summarizes one (agents, calls per agent and
tool with versions, failures).

## What is written

A trace directory holds two append-only JSONL files. Every line is written
with one `write(2)` on an `O_APPEND` descriptor under an exclusive `flock`,
so threads, several orchestrators in one process and several processes can
share a directory, and a killed run keeps every entry up to the kill. The
directory is created with the first entry.

### `agents.jsonl`

```json
{"event": "agent_started", "id": "ag-…", "label": "…", "parent": "ag-…" | null,
 "model": "…" | null, "engine": "…" | null, "harness": "…" | null, "ts": "…Z"}
{"event": "agent_ended", "id": "ag-…", "ts": "…Z"}
```

A plain `toolbase serve` registers one agent at its first tool call, labelled
from the MCP client's `initialize` info (`label` "<name> <version>",
`harness` the client name), and ends it at shutdown. Embedders register their
own agents (below); the orchestrator never registers one for them.

### `calls.jsonl` (schema `toolbase-call/1`)

```json
{"schema": "toolbase-call/1", "id": "c-7f3a9e01bc", "agent": "ag-…",
 "ts_start": "2026-11-15T22:18:03.120Z", "ts_end": "2026-11-15T22:19:41.002Z",
 "toolkit": "heptapod", "version": "3.14.1", "tool": "MadGraphFromRunCard",
 "args": {"…": "…"}, "ok": true, "error": null,
 "result": {"sha256": "…", "head": "first 2048 characters"},
 "files": [{"path": "samples/x/unweighted_events.lhe.gz", "bytes": 183000000,
            "mtime": "…Z", "sha256": "…" | null}],
 "parent": null}
```

- `id`: `c-` and 10 hex digits, unique per call. Treat as opaque.
- `tool`: the toolkit's own tool name (not the `<toolkit>__` wire name).
- `version`: the version actually served. An editable slot reports its
  checkout's `toolkit.yaml` version as `<v>+editable`.
- `args`: secrets redacted by the call log's sanitiser (`toolbase/_sanitize.py`);
  unlike the call log, lists are kept whole and strings over 16 kB are cut.
- `ok`: false on an exception, an MCP `isError`, an orchestral `Error: …`
  result, or a call that never reached the toolkit (restarting, crashed);
  `error` then holds the message.
- `result`: sha256 of the full text the tool returned, and its first 2048
  characters. The trailing call-id line is not included.
- `files`: regular files named in the arguments (any string value, nested)
  or the result text (path-like tokens) that exist when the call ends.
  Relative names resolve against the toolkit's `base_directory` (Conjure's
  sandbox), else the `Trace`'s `base` (for `serve`, the project), else the
  working directory. A file under that base is stored relative to
  it, any other by absolute path. Hashed when at most `hash_limit` bytes
  (default 100 MB; `Trace(hash_limit=…)` or `TOOLBASE_TRACE_HASH_LIMIT`),
  else `sha256` is null. At most 200 files per call.
- `parent`: null for calls the orchestrator traces; set by toolkits for
  work they trace on a call's behalf (below).

### The call id in the result

With tracing on, the text returned to the agent gains one last line,
`[toolbase call <id>]`, so the agent can cite the call. If the entry could
not be written, no line is added.

### Failure never reaches the call

Any error inside the trace (unwritable directory, full disk, an odd result) is
caught, logged to `serve.log` as `trace_error` (the first ten per
orchestrator), and the call's own result is returned unchanged.

## For harnesses (Conjure)

```python
from toolbase.trace import Trace
from toolbase.serve.orchestrator import Orchestrator

trace_dir = run_dir / ".toolbase" / "traces" / session_id
lead = Trace(trace_dir, agent="ag-lead")
lead.register_agent(label="lead", parent=None, model="claude-…",
                    engine="claude", harness="conjure")

child = lead.for_agent("ag-child")            # same directory, other agent
child.register_agent(label="sensitivity", parent="ag-lead", model="gpt-…",
                     engine="codex", harness="conjure")

# one Orchestrator per toolkit per agent, all sharing that agent's Trace
orch = Orchestrator(loadout=…, config_overrides={"base_directory": str(sandbox)},
                    trace=child)
...
child.end_agent()
```

`register_agent(id=None, label=None, parent=None, model=None, engine=None,
harness=None)` and `end_agent(id=None)` default `id` to the handle's agent.
Module-level `toolbase.trace.register_agent(dir, id, …)` and
`end_agent(dir, id)` do the same without a handle. A harness may write its own
files (`messages.jsonl`) into the same directory; toolbase ignores them.

## For toolkits: the current call and linked entries

With tracing on, the orchestrator sends each call's trace context in the MCP
request `_meta` under `"toolbase/trace"`, and the toolkit host makes it
available to the tool while it runs:

```python
from toolbase import trace

trace.current()
# {"call_id": "c-…", "dir": "/abs/trace/dir", "agent": "ag-…",
#  "toolkit": "heptapod", "version": "3.14.1", "base": "/abs/sandbox"}
# or None when tracing is off
```

`toolbase.trace` is stdlib-only and importable in the toolkit's interpreter
(the host's `PYTHONPATH` carries the toolbase package). `current()` is a
context variable: visible in the tool's `execute` and anything it calls in the
same thread, not in threads or processes it starts. Hand it over explicitly:

- capture `ctx = trace.current()` inside the tool, before returning;
- in a worker thread, `with trace.bind(ctx): …` makes it current again;
- for a worker process, pass `env={**os.environ, **trace.child_env(ctx)}`;
  `current()` in that process reads `TOOLBASE_TRACE_CALL`,
  `TOOLBASE_TRACE_DIR` and `TOOLBASE_TRACE_AGENT`.

To trace work done later on a call's behalf, linked to it by `parent`:

```python
# heptapod SubmitJob.execute (runs inside the traced SubmitJob call)
ctx = trace.current()                     # parent = this SubmitJob call
job = spawn_worker(tool_name, tool_args, trace_ctx=ctx)   # store ctx with the job
return f"submitted job {job.id}"

# the worker, later, in any thread or process
with trace.child_call(ctx, tool_name, tool_args) as call:
    call.result = run_tool(tool_name, tool_args)          # text result
    call.files += manifest_paths                          # declared outputs
# entry written: parent = SubmitJob's id, same agent/toolkit/version,
# ok false (and the exception re-raised) if the block raised.
# call.id is known up front, e.g. for the job's status output.
```

`child_call(ctx, tool, args=None, *, toolkit=None, version=None, files=None)`
times the block, binds `current()` to the inner call inside it (so nested work
links to the inner call), and writes the entry when the block exits. Set
`call.result`, optionally `call.ok` / `call.error`, and extend `call.files`
with paths the tool declares (a manifest); paths named in `args` and the
result are found as for any call. With `ctx` None it does nothing.

The lower-level `trace.record_call(ctx, tool=…, args=…, ok=…, error=…,
result=…, ts_start=…, ts_end=…, toolkit=…, version=…, files=…, parent=…,
id=…)` writes one entry and returns its id; it never raises (returns None on
failure or when `ctx` is None). `parent` defaults to `ctx["call_id"]`.

## Limits

- Subagents inside one harness session share its MCP connection; their calls
  are attributed to the session's agent.
- Files a tool writes but neither names in its arguments or result nor
  declares through `child_call` are not seen during the run.
- A call still running when the process dies has no entry (entries are
  written when a call ends).

## Export and viewing

Export and the viewer live in a separate package,
[tooltrace](https://github.com/tonymenzo/tooltrace), so toolbase stays the
capture side only and other harnesses' logs can be traced too.

```
tb trace export <session|dir> -o record/ [--sandbox DIR] [--commands claude:PATH] [--title NAME]
```

hands everything after the session to `tooltrace export` when tooltrace is
importable, and otherwise says how to install it. `tooltrace export`,
`tooltrace open` and `tooltrace validate` work on a trace directory directly.
The format's required keys are checked against toolbase's own entries in
`tests/test_trace.py` (a vendored copy of tooltrace's spec, so the test needs
no tooltrace).
