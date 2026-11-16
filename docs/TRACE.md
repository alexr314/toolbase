# Provenance trace

An optional record of every toolkit call `toolbase` serves: which agent made
it, which tool at which toolkit version, the arguments, the outcome, and the
files it named. Off by default. With it off, nothing below happens and serve
behaves exactly as before.

Phase 1 is capture (below); `tb trace export` (phase 4, [Export](#export-tb-trace-export))
turns a trace directory into a shareable bundle with an HTML viewer.

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
share a directory, and a killed run keeps every record up to the kill. The
directory is created with the first record.

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
- `parent`: null for calls the orchestrator records; set by toolkits for
  work they record on a call's behalf (below).

### The call id in the result

With tracing on, the text returned to the agent gains one last line,
`[toolbase call <id>]`, so the agent can cite the call. If the record could
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

## For toolkits: the current call and linked records

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

To record work done later on a call's behalf, linked to it by `parent`:

```python
# heptapod SubmitJob.execute (runs inside the traced SubmitJob call)
ctx = trace.current()                     # parent = this SubmitJob call
job = spawn_worker(tool_name, tool_args, trace_ctx=ctx)   # store ctx with the job
return f"submitted job {job.id}"

# the worker, later, in any thread or process
with trace.child_call(ctx, tool_name, tool_args) as call:
    call.result = run_tool(tool_name, tool_args)          # text result
    call.files += manifest_paths                          # declared outputs
# record written: parent = SubmitJob's id, same agent/toolkit/version,
# ok false (and the exception re-raised) if the block raised.
# call.id is known up front, e.g. for the job's status output.
```

`child_call(ctx, tool, args=None, *, toolkit=None, version=None, files=None)`
times the block, binds `current()` to the inner call inside it (so nested work
links to the inner call), and writes the record when the block exits. Set
`call.result`, optionally `call.ok` / `call.error`, and extend `call.files`
with paths the tool declares (a manifest); paths named in `args` and the
result are found as for any call. With `ctx` None it does nothing.

The lower-level `trace.record_call(ctx, tool=…, args=…, ok=…, error=…,
result=…, ts_start=…, ts_end=…, toolkit=…, version=…, files=…, parent=…,
id=…)` writes one record and returns its id; it never raises (returns None on
failure or when `ctx` is None). `parent` defaults to `ctx["call_id"]`.

## Limits (phase 1)

- Subagents inside one harness session share its MCP connection; their calls
  are attributed to the session's agent.
- Files a tool writes but neither names in its arguments or result nor
  declares through `child_call` are not seen during the run.
- A call still running when the process dies has no record (records are
  written when a call ends).

## Export: `tb trace export`

```
tb trace export <session|dir> -o <bundle>
    [--sandbox <dir>]                  # hash its files, match them to calls and commands
    [--commands claude:<path>]         # shell commands from Claude Code transcripts
    [--commands codex:<path>]          # ... from Codex rollouts
    [--commands conjure:<session dir>] # ... from Conjure's per-agent logs (exact agent ids)
    [--copy graph|all|none] [--copy-limit 1MB] [--copy-total 256MB]
    [--hash-limit SIZE] [--no-redact] [--force]
```

Offline: it reads the JSONL files only, so it works on a run that finished,
crashed or was killed (torn last lines are skipped and counted, agents with no
`agent_ended` are reported). Nothing on the serve path imports it.

### The bundle

| File | Contents |
|---|---|
| `index.html` | the viewer, CSS, JS and data inlined; opens from disk, loads nothing from the network |
| `trace.json` | `{"schema": "toolbase-trace-bundle/1", "meta", "nodes", "edges"}` |
| `prov.json` | the same graph as a W3C PROV-JSON document |
| `files.json` | `{"schema": "toolbase-trace-files/1", "files": [...]}`, one row per file version |
| `files/<path>` | copies of small files, at their sandbox-relative paths |

**Nodes** (`kind`, `id`):

- `agent`, `ag-…` (and `@user`, `@system` for Conjure's external senders): label,
  parent, model, engine, harness, `ts_start`/`ts_end`, `spawn` (the id of the
  spawn message holding the brief), `terminated`.
- `call`, `c-…`: the `calls.jsonl` record (agent, toolkit, version, tool, args,
  ok, error, result head and hash, parent).
- `command`, `cmd-<10 hex>`: a shell command or file-writing tool use from a
  transcript. The id is the sha256 of the harness's tool-use id, so it is stable
  across exports. Command text, agent, source reader(s), cwd, times, ok, exit
  code, output head and hash, `script` (the first named script file with its
  hash at export), `writes` for Write/Edit/apply_patch.
- `file`, `f-<12 hex of sha256>`: one content version; `paths`, `bytes`,
  `sha256`, `mtime`, `origin` (recorded, inferred, unknown), `present` (this
  version is in the sandbox now), `copied`. A file with no hash (over
  `--hash-limit`, or no sandbox) is `f-p<11 hex>` of path, size and mtime.
- `message`: `m-<msg id>` for a send (from, to, body, thread),
  `m-spawn-<agent>` for a spawn (role prompt, initial message, model, engine,
  toolkits).

**Edges** (`kind`, `from`, `to`, optional `ts`, `basis`, `inferred`):

| kind | from → to | meaning |
|---|---|---|
| `spawned` | parent agent → child agent | |
| `sent` | agent → message | |
| `received` | message → agent | |
| `called` | agent → call or command | |
| `used` | call or command → file | existed before it started |
| `generated` | call or command → file | written in its window |
| `child_of` | call → call | a toolkit's inner call (`parent`) |

A call's recorded files are `used` when their mtime is before the call started
and `generated` when it falls in `[start − 0.5 s, end + 1 s]` (`basis:
recorded`). Commands' edges are always `inferred: true`:

- `used`, `basis: named`: a sandbox file the command names (resolved against its
  cwd and any `cd`), unchanged since before it started.
- `generated`, `basis: named` for Write/Edit/apply_patch paths.
- `generated` by mtime, for every sandbox file nothing generated directly:
  among the commands whose window holds the file's mtime, and the calls whose
  window holds it and whose arguments name one of its directories, the one
  that names it as a write target (`target+mtime`: `>`, `tee`, `-o`,
  `open(…, "w")`, `savefig(…)`) wins, then one that names the file
  (`named+mtime`), then the deepest named directory (`dir+mtime`), then the
  shortest window. A file written in a command's window that the command does
  not name is not attributed; `files.json` lists the commands as `candidates`.

Only versions some call or command names become graph nodes; every other file
is a row in `files.json`, with its inferred generator if any.

**files.json rows**: `path` (sandbox-relative), `bytes`, `mtime`, `sha256`,
`present`, `node` (graph node id, if any), `origin` (`recorded`, `inferred`,
`unknown`), `generated_by`, `basis`, `used_by`, `copied` (bundle path),
`candidates`, `before_trace` (unknown origin and older than the trace), `note`
(a recorded version no longer in the sandbox).

**Copies**: with `--copy graph` (default), the present files that are graph
nodes and at most `--copy-limit` bytes; `--copy all` copies every sandbox file
under the limit. Copying stops at `--copy-total`. Larger files are listed with
their hash, so the bundle verifies them without containing them.

**Redaction** (default): the sandbox's absolute path becomes `<sandbox>`, the
trace directory's `<trace>`, and the home directory `~`, in every node and row.
`--no-redact` keeps them.

### PROV mapping (`prov.json`)

Everything is in the `tb:` namespace (`https://toolbase.dev/ns/trace#`); ids are
`tb:<node id>`, relations are blank nodes `_:e<n>`.

| trace | PROV-JSON |
|---|---|
| agent | `agent`, `prov:type` `prov:SoftwareAgent` (`prov:Person` for `@user`), `prov:label` |
| call | `activity`, `prov:type` `tb:ToolCall`, `prov:startTime`, `prov:endTime` |
| command | `activity`, `prov:type` `tb:ShellCommand` |
| file | `entity`, `prov:type` `tb:File`, `prov:location` (first path) |
| message | `entity`, `prov:type` `tb:Message` or `tb:Spawn`, `prov:value` (body or brief) |
| called | `wasAssociatedWith` (activity, agent) |
| used | `used` (activity, entity) |
| generated | `wasGeneratedBy` (entity, activity) |
| spawned | `actedOnBehalfOf` (delegate child, responsible parent) |
| child_of | `wasInformedBy` (informed child, informant parent) |
| sent | `wasAttributedTo` (message, sender) |
| received | `wasInfluencedBy` (influencee recipient, influencer message) |

Other fields become `tb:<field>` attributes; structured values (arguments,
toolkits) are JSON strings, since PROV-JSON attribute values are literals.
Inferred edges carry `tb:inferred` and `tb:basis`.

### Shell commands (`toolbase/trace/readers/`)

| `--commands` | Reads | Agent |
|---|---|---|
| `conjure:<session dir>` | `agents/<agent-id>.jsonl`: `stream_end` `tool_calls` and `tool` results | the file name (exact) |
| `claude:<path>` | Claude Code `~/.claude/projects/<escaped cwd>/*.jsonl` (and `subagents/`): `tool_use` / `tool_result` | Conjure's identity line (`Address id: …`) in the transcript, else its toolkit calls matched to `calls.jsonl` (tool, arguments, time), else the trace's only agent |
| `codex:<path>` | `rollout-*.jsonl` under a Codex home: `CommandExecution` items, or older `function_call`/`exec_command_end` | the home's directory name (`codex_homes/<agent-id>/`), else the identity line, else as above |

A session nothing attributes gets a placeholder agent `ag-<reader>-<session>`
and a note. The same tool use seen by two readers (Conjure and Claude Code
share `toolu_…` ids) is merged; for a Codex agent, the rollout's commands
replace Conjure's per-turn view.

### The viewer

`index.html` has a timeline (one lane per agent in spawn order; calls coloured
by toolkit, commands below them, failures red; spawns and messages drawn
between lanes; wheel zoom, drag pan), a provenance view (from the selected node,
its inputs to a chosen depth on the left and what used it on the right; dashed
edges are inferred; optionally agents and messages), an activity list, a file
index of every file with its origin, and the messages. Filters: search, agent,
toolkit, tool, time window, failed only. The detail panel shows arguments,
result head, files with hashes, a spawn's brief, a message's body.

Anchors select a node on load: `index.html#c-<id>`, `#cmd-<id>`, `#ag-<id>`,
`#m-<id>` and `#f-<sha256 prefix>` (8 hex digits or more; a file that is not a
graph node opens its row). A citation can link straight to a call.

### Limits

- Files written by a toolkit's background job (heptapod `SubmitJob`) are not
  matched until the toolkit records the job's work as child calls
  (`child_call`); their outputs show as unknown origin.
- Command windows are when the harness saw the command start and return; a
  background command's later writes fall outside it.
- A script's hash is taken at export; `changed_since` marks a script modified
  after the command ran.
