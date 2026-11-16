"""Convert a finished Conjure run into toolbase trace records (test data only).

Phase-1 capture postdates the trial runs, so no real trace exists yet. This
script rebuilds what capture would have written from what Conjure logged:

- ``agents.jsonl`` and ``messages.jsonl`` from the session journal (spawns,
  sends, terminations, observed models);
- ``calls.jsonl`` from each agent's log: every toolkit tool use
  (``mcp__toolbase__<toolkit>__<Tool>``, or ``<toolkit>__<Tool>`` from the
  Codex engine) paired with its result, files found with toolbase's own
  ``collect_files`` against the sandbox. A file modified after the call
  ended is left out (capture would have hashed a version that is gone).

It reads the run and never writes to it.

    python conjure_run_to_trace.py <run> <out>           # full: <out>/trace/
    python conjure_run_to_trace.py <run> <out> --trim    # the committed fixture

``--trim`` keeps a few calls and commands per agent, copies the small files
those calls name into ``<out>/sandbox`` (with their original mtimes in
``sandbox_mtimes.json``, since git does not keep mtimes), writes trimmed
Conjure, Claude Code and Codex transcripts beside them, and replaces
home-directory paths throughout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[3]))  # the toolbase checkout

from toolbase.trace.core import build_call_record, collect_files  # noqa: E402

CALL_RE = re.compile(r"^(?:mcp__toolbase__)?([a-z][a-z0-9_]*)__([A-Z]\w*)$")
FAKE_HOME = "/home/user"
FAKE_RUN = "/home/user/runs/hnl"


def iso(t):
    return (datetime.fromtimestamp(t, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def write_jsonl(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


class Scrub:
    def __init__(self, run: Path, on: bool):
        self.on = on
        self.pairs = [(str(run), FAKE_RUN), (str(Path.home()), FAKE_HOME)]
        # The account's full name and login (note authors, `ls -l` owners).
        try:
            import pwd
            pw = pwd.getpwuid(os.getuid())
            names = [n for n in (pw.pw_gecos.split(",")[0], pw.pw_name) if n and len(n) > 2]
        except (ImportError, KeyError):
            names = []
        self.names = [re.compile(r"\b" + re.escape(n) + r"\b") for n in names]

    def __call__(self, v):
        if not self.on:
            return v
        if isinstance(v, str):
            for a, b in self.pairs:
                v = v.replace(a, b)
            for rx in self.names:
                v = rx.sub("user", v)
            return v
        if isinstance(v, dict):
            return {k: self(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self(x) for x in v]
        return v


def versions(run: Path):
    try:
        return json.loads((run / "preflight.json").read_text()).get("served") or {}
    except (OSError, ValueError):
        return {}


def convert(run: Path, out: Path, trim: bool, per_agent: int = 4):
    sess_dir = next((run / "conjure-native" / "sessions").iterdir())
    sandbox = run / "workspace"
    scrub = Scrub(run, trim)
    served = versions(run)
    journal = jsonl(sess_dir / "journal.jsonl")

    # ── agents and messages from the journal ──
    models, agents, messages = {}, [], []
    for r in journal:
        if r["kind"] == "agent_meta" and r["payload"].get("meta", {}).get("model"):
            models.setdefault(r["payload"]["addr"], r["payload"]["meta"]["model"])
    for r in journal:
        p, ts = r["payload"], iso(r["ts"])
        if r["kind"] == "spawn":
            aid, spec = p["addr"]["id"], p["spec"]
            parent = (p.get("parent") or {}).get("id")
            agents.append({"event": "agent_started", "id": aid, "label": spec.get("label"),
                           "parent": parent, "model": models.get(aid, spec.get("model")),
                           "engine": spec.get("engine"), "harness": "conjure", "ts": ts})
            tk = spec.get("toolkits")
            messages.append({"event": "spawn", "ts": ts, "from": parent, "to": aid,
                             "label": spec.get("label"),
                             "role_prompt": spec.get("role_prompt"),
                             "initial_message": spec.get("initial_message"),
                             "model": models.get(aid, spec.get("model")),
                             "engine": spec.get("engine"),
                             "toolkits": tk if isinstance(tk, dict) else (
                                 spec.get("toolbase_loadout") or tk)})
        elif r["kind"] == "send":
            env = p["envelope"]
            messages.append({"event": "send", "ts": ts, "from": env["from"]["id"],
                             "to": env["to"]["id"], "body": env.get("body"),
                             "msg_id": env.get("msg_id"), "thread_id": env.get("thread_id")})
        elif r["kind"] == "terminate":
            aid = p["addr"]["id"]
            messages.append({"event": "terminate", "ts": ts, "from": p.get("requested_by"),
                             "to": aid, "cascade": p.get("cascade")})
            agents.append({"event": "agent_ended", "id": aid, "ts": ts})

    # Conjure ends every live agent when the session shuts down (end_all).
    ended = {a["id"] for a in agents if a["event"] == "agent_ended"}
    for a in [a for a in agents if a["event"] == "agent_started" and a["id"] not in ended]:
        agents.append({"event": "agent_ended", "id": a["id"], "ts": iso(journal[-1]["ts"])})

    # ── toolkit calls from each agent's log ──
    calls, kept_ids = [], {}
    agent_logs = sorted((sess_dir / "agents").glob("ag-*.jsonl"))
    for f in agent_logs:
        aid = f.stem
        recs = jsonl(f)
        uses, results = {}, {}
        for r in recs:
            if r.get("kind") == "stream_end":
                for tc in r.get("tool_calls") or []:
                    uses.setdefault(tc["id"], (r["ts"], tc))
            elif r.get("kind") == "tool":
                results[r["tool_call_id"]] = r
        n_calls = n_cmds = 0
        keep = set()
        for tid, (t0, tc) in sorted(uses.items(), key=lambda kv: kv[1][0]):
            m = CALL_RE.match(tc["name"])
            res = results.get(tid)
            if not m:
                if tc["name"] in ("Bash", "shell", "Write", "Edit") and n_cmds < per_agent:
                    n_cmds += 1
                    keep.add(tid)
                continue
            if trim and n_calls >= per_agent:
                continue
            n_calls += 1
            keep.add(tid)
            toolkit, tool = m.group(1), m.group(2)
            t1 = res["ts"] if res else t0
            text = (res or {}).get("text") or ""
            failed = bool((res or {}).get("failed")) or text.startswith(
                ("Error:", "Input validation error"))
            files = collect_files(tc.get("args") or {}, text, base=sandbox)
            # A file rewritten since the call ended: capture would have hashed
            # the version it saw, which is gone, so leave it out.
            files = [e for e in files if os.stat(
                e["path"] if os.path.isabs(e["path"]) else sandbox / e["path"]).st_mtime <= t1 + 1.0]
            cid = "c-" + hashlib.sha256(tid.encode()).hexdigest()[:10]
            calls.append(build_call_record(
                id=cid, agent=aid, toolkit=toolkit, version=served.get(toolkit),
                tool=tool, args=tc.get("args") or {}, ok=not failed,
                error=text[:500] if failed else None,
                result=text,
                ts_start=iso(t0), ts_end=iso(t1), files=files))
        kept_ids[aid] = keep
    calls.sort(key=lambda c: c["ts_start"])

    tdir = out / "trace"
    if not trim:
        write_jsonl(tdir / "agents.jsonl", agents)
        write_jsonl(tdir / "calls.jsonl", calls)
        write_jsonl(tdir / "messages.jsonl", messages)
        print(f"{tdir}: {len(agents)} agent events, {len(calls)} calls, {len(messages)} messages")
        return

    # ── trimmed fixture ──
    def cut(s, n=400):
        return s if not isinstance(s, str) or len(s) <= n else s[:n] + "…"

    for m in messages:
        for k in ("role_prompt", "initial_message", "body"):
            if k in m:
                m[k] = cut(m[k])
    sb = out / "sandbox"
    if sb.exists():
        shutil.rmtree(sb)
    mtimes = {}
    for c in calls:
        kept = []
        for e in c["files"]:
            if os.path.isabs(e["path"]):
                continue
            src = sandbox / e["path"]
            st = src.stat()
            if st.st_size > 16 * 1024:
                continue
            dst = sb / e["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            dst.write_text(scrub(data), encoding="utf-8")
            mtimes[e["path"]] = st.st_mtime
            if e["sha256"] is not None:
                e["sha256"] = hashlib.sha256(dst.read_bytes()).hexdigest()
                e["bytes"] = dst.stat().st_size
            kept.append(e)
        c["files"] = kept
        c["result"]["head"] = cut(c["result"]["head"], 600)
        for k, v in list(c["args"].items()):
            c["args"][k] = cut(v, 600)
    (out / "sandbox_mtimes.json").write_text(json.dumps(mtimes, indent=1, sort_keys=True))
    write_jsonl(tdir / "agents.jsonl", scrub(agents))
    write_jsonl(tdir / "calls.jsonl", scrub(calls))
    write_jsonl(tdir / "messages.jsonl", scrub(messages))

    # Conjure agent logs, only the kept tool uses
    for f in agent_logs:
        keep = kept_ids[f.stem]
        recs = []
        for r in jsonl(f):
            if r.get("kind") == "stream_end":
                tcs = [dict(tc, args={k: cut(v, 600) for k, v in (tc.get("args") or {}).items()})
                       for tc in r.get("tool_calls") or [] if tc["id"] in keep]
                if tcs:
                    recs.append({"kind": "stream_end", "tool_calls": tcs, "ts": r["ts"]})
            elif r.get("kind") == "tool" and r["tool_call_id"] in keep:
                recs.append(dict(r, text=cut(r.get("text"), 300)))
        write_jsonl(out / "conjure" / "agents" / f.name, scrub(recs))

    # One Claude Code transcript (the build agent), only kept tool uses.
    proj = Path.home() / ".claude" / "projects" / ("-" + str(sandbox).strip("/").replace("/", "-"))
    build = next((a["id"] for a in agents if a.get("label") == "build"), None)
    for f in sorted(proj.glob("*.jsonl")):
        text = f.read_text(encoding="utf-8", errors="replace")
        if f"Address id:  `{build}`" not in text:
            continue
        keep = kept_ids.get(build, set())
        recs = [{"type": "api-request-shape", "sessionId": "sess-build",
                 "shape": {"system": [{"type": "text", "text": f"# Conjure agent\n- Address id:  `{build}`\n"}]}}]
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            msg = r.get("message") if isinstance(r.get("message"), dict) else None
            if not msg or not isinstance(msg.get("content"), list) or r.get("type") not in ("assistant", "user"):
                continue
            parts = []
            for p in msg["content"]:
                if p.get("type") == "tool_use" and p.get("id") in keep:
                    parts.append({"type": "tool_use", "id": p["id"], "name": p["name"],
                                  "input": {k: cut(v, 600) for k, v in p["input"].items()}})
                elif p.get("type") == "tool_result" and p.get("tool_use_id") in keep:
                    parts.append({"type": "tool_result", "tool_use_id": p["tool_use_id"],
                                  "content": cut(p.get("content") if isinstance(p.get("content"), str) else "", 300),
                                  "is_error": bool(p.get("is_error"))})
            if parts:
                recs.append({"type": r["type"], "sessionId": "sess-build", "timestamp": r.get("timestamp"),
                             "cwd": r.get("cwd"), "isSidechain": False,
                             "message": {"role": msg.get("role"), "content": parts}})
        write_jsonl(out / "claude" / "sess-build.jsonl", scrub(recs))
        break

    # The Codex agent's rollout: a few command executions and one toolkit call.
    for f in sorted((run / "conjure-native" / "codex_homes").glob("ag-*/sessions/**/rollout-*.jsonl")):
        aid = f.relative_to(run / "conjure-native" / "codex_homes").parts[0]
        recs, n_exec, n_mcp = [], 0, 0
        for r in jsonl(f):
            p = r.get("payload") or {}
            if r.get("type") == "session_meta":
                recs.append({"timestamp": r["timestamp"], "type": "session_meta",
                             "payload": {"id": p.get("id"), "cwd": p.get("cwd"), "originator": p.get("originator")}})
            elif p.get("type") == "item_completed":
                it = p["item"]
                if it.get("type") == "CommandExecution" and n_exec < per_agent:
                    n_exec += 1
                    recs.append({"timestamp": r["timestamp"], "type": "event_msg", "payload": {
                        "type": "item_completed", "started_at_ms": p.get("started_at_ms"),
                        "completed_at_ms": p.get("completed_at_ms"),
                        "item": {k: (cut(v, 300) if k == "aggregated_output" else v) for k, v in it.items()
                                 if k in ("type", "id", "command", "cwd", "status", "aggregated_output", "exit_code")}}})
                elif it.get("type") == "McpToolCall" and it.get("server") != "conjure" and n_mcp < 1:
                    n_mcp += 1
                    recs.append({"timestamp": r["timestamp"], "type": "event_msg", "payload": {
                        "type": "item_completed", "started_at_ms": p.get("started_at_ms"),
                        "item": {"type": "McpToolCall", "id": it.get("id"), "server": it.get("server"),
                                 "tool": it.get("tool"),
                                 "arguments": {k: cut(v, 300) for k, v in (it.get("arguments") or {}).items()}}}})
        dst = out / "codex" / aid / "sessions" / f.relative_to(f.parents[3])
        write_jsonl(dst, scrub(recs))
    print(f"fixture in {out}: {len(calls)} calls, {len(messages)} messages, {len(mtimes)} sandbox files")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--trim", action="store_true")
    ap.add_argument("--per-agent", type=int, default=4)
    a = ap.parse_args()
    convert(a.run.resolve(), a.out, a.trim, a.per_agent)


if __name__ == "__main__":
    main()
