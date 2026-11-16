"""Provenance trace (phase 1): capture at the orchestrator call site, the
agent registry, files and hashes, the call id in results, ``current()``
inside a real toolkit host, the ``tb trace`` CLI, and robustness.

Most tests mock the MCPClient the way ``test_orchestrator_restart`` does; one
spawns a real host subprocess to check the ``_meta`` round trip.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from toolbase import trace as tbtrace
from toolbase.serve import orchestrator
from toolbase.serve.orchestrator import (
    Orchestrator, ToolkitDiscovery, ToolkitRuntime, ToolkitState,
)
from toolbase.trace import Trace, read_jsonl

ID_LINE = re.compile(r"\n\[toolbase call (c-[0-9a-f]+)\]$")


# ── helpers ────────────────────────────────────────────────────────────


def _client(result="ok"):
    client = MagicMock()
    client._subprocess_died = False
    client._session = None  # no persistent session: call_with_meta falls back
    client.call_tool.return_value = result
    return client


def _orch(tmp_path, trace=None, name="demo", result="ok", version="1.2.3",
          base=None):
    o = Orchestrator(toolkits_dir=tmp_path / "no-toolkits", trace=trace)
    disc = ToolkitDiscovery(name=name, path=tmp_path / name,
                            meta={"environment": "venv", "version": version})
    client = _client(result)
    o._runtimes[name] = ToolkitRuntime(
        name=name, path=tmp_path / name, upstream_tool_names=["Tool"],
        mcp_client=client, state=ToolkitState.READY, discovery=disc,
    )
    if base is not None:
        o._trace_bases[name] = str(base)
    return o, o._make_forwarder(name), client


# ── off path ───────────────────────────────────────────────────────────


def test_off_path_returns_result_unchanged(tmp_path):
    o, fwd, client = _orch(tmp_path, trace=None, result="exact result")
    assert fwd("Tool", {"x": 1}) == "exact result"
    client.call_tool.assert_called_once_with("Tool", {"x": 1})
    assert not (tmp_path / "trace").exists()


def test_off_path_errors_unchanged(tmp_path):
    o, fwd, client = _orch(tmp_path)
    client.call_tool.side_effect = ValueError("bad input")
    out = fwd("Tool", {})
    assert out.startswith("Tool failed after ") and out.endswith("s: bad input")


def test_off_path_host_env_has_no_trace_flag(tmp_path, monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, server_command, env, timeout):
            seen["env"] = env

        def connect(self):
            raise RuntimeError("stop here")

        def disconnect(self):
            pass

    import orchestral.mcp
    monkeypatch.setattr(orchestral.mcp, "MCPClient", FakeClient)
    disc = ToolkitDiscovery(name="demo", path=tmp_path,
                            meta={"environment": "venv", "python_path": sys.executable})
    for tr, expect in ((None, False), (Trace(tmp_path / "t"), True)):
        o = Orchestrator(toolkits_dir=tmp_path / "x", trace=tr)
        o._spawn_and_connect(disc, state_config={"base_directory": "/sb"})
        assert ("TOOLBASE_TRACE_HOST" in seen["env"]) is expect
    assert o._trace_bases == {"demo": "/sb"}


def test_logger_sanitiser_unchanged():
    from toolbase.logging.logger import ToolLogger
    lg = ToolLogger.__new__(ToolLogger)
    args = {"api_key": "s", "n": 1, "xs": list(range(11)), "few": [1, 2],
            "d": {"token": "t", "v": "x"}, "o": object()}
    assert lg._sanitize_args(args) == {
        "api_key": "***REDACTED***", "n": 1, "xs": "<list of 11 items>",
        "few": [1, 2], "d": {"token": "***REDACTED***", "v": "x"}, "o": "<object>",
    }


def test_serve_yaml_without_trace_round_trips_unchanged(tmp_path):
    from toolbase.serve.config import load_serve_config, save_serve_config
    p = tmp_path / "serve.yaml"
    p.write_text("default:\n  loadout: paper\n")
    cfg = load_serve_config(p)
    assert cfg.trace is None
    save_serve_config(cfg, p)
    assert p.read_text() == "default:\n  loadout: paper\n"


# ── records and attribution ────────────────────────────────────────────


def test_two_orchestrators_attribute_calls_per_agent(tmp_path):
    d = tmp_path / "trace"
    lead, worker = Trace(d, agent="ag-lead"), Trace(d, agent="ag-worker")
    lead.register_agent(label="lead", model="m1", engine="claude", harness="conjure")
    worker.register_agent(label="worker", parent="ag-lead", model="m2",
                          engine="codex", harness="conjure")
    _, f1, _ = _orch(tmp_path, lead, name="heptapod", version="3.14.1")
    _, f2, _ = _orch(tmp_path, worker, name="feyntable", version="0.2.0")
    f1("Tool", {"a": 1})
    f2("Tool", {"b": 2})
    f1("Tool", {"a": 3})
    worker.end_agent()

    calls = read_jsonl(d / "calls.jsonl")
    assert [(c["agent"], c["toolkit"], c["version"]) for c in calls] == [
        ("ag-lead", "heptapod", "3.14.1"),
        ("ag-worker", "feyntable", "0.2.0"),
        ("ag-lead", "heptapod", "3.14.1"),
    ]
    c = calls[0]
    assert list(c) == ["schema", "id", "agent", "ts_start", "ts_end", "toolkit",
                       "version", "tool", "args", "ok", "error", "result",
                       "files", "parent"]
    assert c["schema"] == "toolbase-call/1"
    assert c["tool"] == "Tool" and c["args"] == {"a": 1}
    assert c["ok"] is True and c["error"] is None and c["parent"] is None
    assert c["result"]["head"] == "ok" and len(c["result"]["sha256"]) == 64
    assert c["ts_start"].endswith("Z") and c["ts_end"] >= c["ts_start"]

    events = read_jsonl(d / "agents.jsonl")
    assert events[0] == {**events[0], "event": "agent_started", "id": "ag-lead",
                         "label": "lead", "parent": None, "model": "m1",
                         "engine": "claude", "harness": "conjure"}
    assert events[1]["parent"] == "ag-lead"
    assert events[2]["event"] == "agent_ended" and events[2]["id"] == "ag-worker"
    assert set(events[2]) == {"event", "id", "ts"}


def test_id_line_appended_and_matches_record(tmp_path):
    tr = Trace(tmp_path / "t", agent="ag-1")
    _, fwd, _ = _orch(tmp_path, tr, result="the result")
    out = fwd("Tool", {})
    m = ID_LINE.search(out)
    assert m and out.startswith("the result\n")
    assert read_jsonl(tr.calls_path)[0]["id"] == m.group(1)


def test_failures_recorded(tmp_path):
    tr = Trace(tmp_path / "t", agent="ag-1")
    o, fwd, client = _orch(tmp_path, tr)
    client.call_tool.side_effect = ValueError("bad input")
    out1 = fwd("Tool", {})
    client.call_tool.side_effect = None
    client.call_tool.return_value = "Error: Execution Error\n- Reason: kaboom"
    fwd("Tool", {})
    o._runtimes["demo"].state = ToolkitState.FAILED
    fwd("Tool", {})
    recs = read_jsonl(tr.calls_path)
    assert [r["ok"] for r in recs] == [False, False, False]
    assert recs[0]["error"] == "bad input"
    assert "kaboom" in recs[1]["error"]
    assert recs[2]["error"].startswith("Tool unavailable")
    assert ID_LINE.search(out1)


def test_secrets_redacted_lists_kept(tmp_path):
    tr = Trace(tmp_path / "t")
    _, fwd, _ = _orch(tmp_path, tr)
    fwd("Tool", {"api_key": "sekrit", "masses": list(range(20)),
                 "blob": "x" * 20000})
    args = read_jsonl(tr.calls_path)[0]["args"]
    assert args["api_key"] == "***REDACTED***"
    assert args["masses"] == list(range(20))
    assert len(args["blob"]) < 17000 and "truncated" in args["blob"]


def test_concurrent_writers_produce_whole_lines(tmp_path):
    tr = Trace(tmp_path / "t")
    rec = {"pad": "y" * 5000}

    def work(i):
        for j in range(50):
            tr.write_call({"i": i, "j": j, **rec})

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = tr.calls_path.read_text().splitlines()
    assert len(lines) == 400
    assert all(json.loads(line)["pad"] == rec["pad"] for line in lines)


# ── files ──────────────────────────────────────────────────────────────


def test_files_from_args_and_result(tmp_path):
    base = tmp_path / "sandbox"
    (base / "samples").mkdir(parents=True)
    (base / "card.dat").write_text("card")
    (base / "samples" / "events.lhe").write_text("events")
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("o")
    tr = Trace(tmp_path / "t")
    result = (f"Wrote samples/events.lhe and {outside}. "
              "Also see output=samples/events.lhe; missing/nothing.txt")
    _, fwd, _ = _orch(tmp_path, tr, result=result, base=base)
    fwd("Tool", {"card": "card.dat", "dir": str(base / "samples"),
                 "process": "p p > mu+ n1", "nested": [{"path": str(base / "card.dat")}]})
    files = {f["path"]: f for f in read_jsonl(tr.calls_path)[0]["files"]}
    assert set(files) == {"card.dat", "samples/events.lhe", str(outside)}
    import hashlib
    assert files["card.dat"]["sha256"] == hashlib.sha256(b"card").hexdigest()
    assert files["card.dat"]["bytes"] == 4
    assert files["card.dat"]["mtime"].endswith("Z")


def test_files_over_hash_limit_have_no_hash(tmp_path):
    base = tmp_path / "sb"
    base.mkdir()
    (base / "big.bin").write_bytes(b"0" * 1000)
    tr = Trace(tmp_path / "t", hash_limit=100)
    _, fwd, _ = _orch(tmp_path, tr, base=base)
    fwd("Tool", {"f": "big.bin"})
    (f,) = read_jsonl(tr.calls_path)[0]["files"]
    assert f == {**f, "path": "big.bin", "bytes": 1000, "sha256": None}


def test_hash_limit_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOLBASE_TRACE_HASH_LIMIT", "7")
    assert Trace(tmp_path).hash_limit == 7


# ── robustness ─────────────────────────────────────────────────────────


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="needs POSIX permissions, not root")
def test_unwritable_trace_dir_never_breaks_the_call(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        tr = Trace(ro / "trace")
        o, fwd, _ = _orch(tmp_path, tr, result="fine")
        assert fwd("Tool", {}) == "fine"   # no id line: no record exists
        assert o._trace_errors == 1
    finally:
        os.chmod(ro, 0o700)


def test_trace_dir_under_a_file_never_breaks_the_call(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    o, fwd, _ = _orch(tmp_path, Trace(f / "trace"), result="fine")
    assert fwd("Tool", {}) == "fine"


def test_capture_start_failure_falls_back(tmp_path, monkeypatch):
    from toolbase.trace import capture
    monkeypatch.setattr(capture, "new_call_id", lambda: 1 / 0)
    o, fwd, client = _orch(tmp_path, Trace(tmp_path / "t"), result="fine")
    assert fwd("Tool", {"a": 1}) == "fine"
    client.call_tool.assert_called_once_with("Tool", {"a": 1})


def test_record_call_never_raises(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    assert tbtrace.record_call({"call_id": "c-1", "dir": str(f / "t")}, tool="X") is None
    assert tbtrace.record_call(None, tool="X") is None


# ── serve session agent ────────────────────────────────────────────────


def test_serve_registers_one_agent_from_client_info(tmp_path):
    tr = Trace(tmp_path / "t", agent="ag-s")
    o = Orchestrator(toolkits_dir=tmp_path / "x", trace=tr)
    o._register_serve_agent({"name": "claude-code", "version": "2.1.0"})
    o._register_serve_agent({"name": "again", "version": "x"})
    o.shutdown()
    ev = read_jsonl(tr.agents_path)
    assert [(e["event"], e["id"]) for e in ev] == [("agent_started", "ag-s"),
                                                    ("agent_ended", "ag-s")]
    assert ev[0]["label"] == "claude-code 2.1.0" and ev[0]["harness"] == "claude-code"


def test_serve_server_class_calls_hook_once():
    import asyncio
    from toolbase.trace.mcp_hooks import serve_server_class

    class Base:
        async def _on_call_tool(self, ctx, params):
            return "done"

    seen = []
    cls = serve_server_class(Base, seen.append)
    ctx = MagicMock()
    ctx.session.client_params.client_info.name = "codex"
    ctx.session.client_params.client_info.version = "1.0"
    srv = cls()
    assert asyncio.run(srv._on_call_tool(ctx, None)) == "done"
    asyncio.run(srv._on_call_tool(ctx, None))
    assert seen == [{"name": "codex", "version": "1.0"}]


# ── enabling ───────────────────────────────────────────────────────────


def test_session_resolution_precedence(tmp_path):
    from toolbase.trace.session import resolve
    proj = tmp_path / "proj"
    (proj / ".toolbase").mkdir(parents=True)
    assert resolve(proj, None, {})[0] is None
    (proj / ".toolbase" / "serve.yaml").write_text("trace: true\n")
    d, why = resolve(proj, None, {})
    assert d.parent == proj / ".toolbase" / "traces" and why == "serve.yaml trace"
    assert resolve(proj, None, {"TOOLBASE_TRACE": "0"})[0] is None
    d, _ = resolve(proj, None, {"TOOLBASE_TRACE": "1", "TOOLBASE_TRACE_DIR": str(tmp_path / "x")})
    assert d == tmp_path / "x"
    (proj / ".toolbase" / "serve.yaml").write_text("trace: false\n")
    assert resolve(proj, "", {})[0].parent == proj / ".toolbase" / "traces"
    assert resolve(proj, str(tmp_path / "y"), {"TOOLBASE_TRACE": "0"})[0] == tmp_path / "y"


def test_toolkit_yaml_version_for_editable(tmp_path):
    (tmp_path / "toolkit.yaml").write_text("name: x\nversion: 3.15.0\n")
    disc = ToolkitDiscovery(name="x", path=tmp_path, meta={"version": "editable"})
    assert orchestrator._served_version(disc) == "3.15.0+editable"


# ── toolkit-side API ───────────────────────────────────────────────────


def test_child_call_links_parent_and_binds_current(tmp_path):
    ctx = {"call_id": "c-outer", "dir": str(tmp_path / "t"), "agent": "ag-1",
           "toolkit": "heptapod", "version": "3.14.1", "base": str(tmp_path)}
    (tmp_path / "out.txt").write_text("x")
    with tbtrace.child_call(ctx, "MadGraphFromRunCard", {"card": "c"}) as cc:
        assert tbtrace.current()["call_id"] == cc.id
        cc.result = "wrote out.txt"
    with pytest.raises(RuntimeError):
        with tbtrace.child_call(ctx, "Boom"):
            raise RuntimeError("worker died")
    assert tbtrace.current() is None
    a, b = read_jsonl(tmp_path / "t" / "calls.jsonl")
    assert a["parent"] == "c-outer" and a["id"] == cc.id
    assert a["agent"] == "ag-1" and a["toolkit"] == "heptapod" and a["version"] == "3.14.1"
    assert [f["path"] for f in a["files"]] == ["out.txt"]
    assert b["ok"] is False and b["error"] == "worker died"


def test_child_env_round_trip(monkeypatch, tmp_path):
    ctx = {"call_id": "c-9", "dir": str(tmp_path), "agent": "ag-2"}
    env = tbtrace.child_env(ctx)
    assert tbtrace.child_env(None) == {}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    cur = tbtrace.current()
    assert cur["call_id"] == "c-9" and cur["dir"] == str(tmp_path) and cur["agent"] == "ag-2"


# ── a real toolkit host ────────────────────────────────────────────────

_TOOLS = '''
import json, threading
from orchestral import define_tool
from toolbase import trace

@define_tool
def where_am_i() -> str:
    """Return the trace context."""
    return json.dumps(trace.current())

@define_tool
def submit(card: str) -> str:
    """Run an inner call in a worker thread, linked to this call."""
    ctx = trace.current()
    out = {}
    def worker():
        with trace.child_call(ctx, "Inner", {"card": card}) as cc:
            cc.result = "inner done"
            out["id"] = cc.id
    t = threading.Thread(target=worker)
    t.start(); t.join()
    return "submitted " + str(out.get("id"))
'''


def test_current_inside_real_toolkit_host(tmp_path):
    tk = tmp_path / "toolkits" / "demo"
    (tk / "tools").mkdir(parents=True)
    (tk / ".tb_meta.json").write_text(json.dumps({
        "name": "demo", "version": "1.2.3", "environment": "venv",
        "python_path": sys.executable}))
    (tk / "toolkit.yaml").write_text("name: demo\nversion: 1.2.3\n")
    (tk / "tools" / "__init__.py").write_text(_TOOLS)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    tr = Trace(tmp_path / "trace", agent="ag-host")
    o = Orchestrator(toolkits_dir=tmp_path / "toolkits", trace=tr,
                     config_overrides={"base_directory": str(sandbox)})
    try:
        tools = {t.get_name(): t for t in o.start()}
        out = tools["demo__WhereAmI"].execute()
        m = ID_LINE.search(out)
        cur = json.loads(out[:m.start()])
        assert cur == {"call_id": m.group(1), "dir": str(tr.dir), "agent": "ag-host",
                       "toolkit": "demo", "version": "1.2.3", "base": str(sandbox)}
        out2 = tools["demo__Submit"].execute(card="run_card.dat")
        outer_id = ID_LINE.search(out2).group(1)
    finally:
        o.shutdown()

    recs = {r["tool"]: r for r in read_jsonl(tr.calls_path)}
    assert recs["Inner"]["parent"] == outer_id
    assert recs["Inner"]["agent"] == "ag-host" and recs["Inner"]["version"] == "1.2.3"
    assert recs["Submit"]["parent"] is None
    assert out2.startswith("submitted " + recs["Inner"]["id"])


# ── CLI ────────────────────────────────────────────────────────────────


def _cli(tmp_path, *args):
    from toolbase.cli import main
    return CliRunner().invoke(main, ["--project-dir", str(tmp_path), *args])


def test_cli_on_off_status(tmp_path, monkeypatch):
    monkeypatch.delenv("TOOLBASE_TRACE", raising=False)
    monkeypatch.delenv("TOOLBASE_TRACE_DIR", raising=False)
    r = _cli(tmp_path, "trace", "status")
    assert r.exit_code == 0 and "Tracing: off" in r.output
    r = _cli(tmp_path, "trace", "on")
    assert r.exit_code == 0, r.output
    assert "trace: true" in (tmp_path / ".toolbase" / "serve.yaml").read_text()
    assert "Tracing: on (serve.yaml trace: true)" in _cli(tmp_path, "trace", "status").output
    monkeypatch.setenv("TOOLBASE_TRACE", "0")
    assert "Tracing: off (TOOLBASE_TRACE=0)" in _cli(tmp_path, "trace", "status").output
    monkeypatch.delenv("TOOLBASE_TRACE")
    assert _cli(tmp_path, "trace", "off").exit_code == 0
    assert "trace: false" in (tmp_path / ".toolbase" / "serve.yaml").read_text()


def test_cli_on_keeps_other_serve_settings(tmp_path):
    (tmp_path / ".toolbase").mkdir()
    p = tmp_path / ".toolbase" / "serve.yaml"
    p.write_text("default:\n  loadout: paper\n")
    _cli(tmp_path, "trace", "on")
    from toolbase.serve.config import load_serve_config
    cfg = load_serve_config(p)
    assert cfg.default.loadout == "paper" and cfg.trace is True


def test_cli_list_and_show(tmp_path):
    root = tmp_path / ".toolbase" / "traces"
    s1 = root / "20261115T000000Z-aaaaaa"
    lead, worker = Trace(s1, agent="ag-lead"), Trace(s1, agent="ag-w")
    lead.register_agent(label="lead", harness="conjure")
    worker.register_agent(label="worker", parent="ag-lead")
    _, f1, c1 = _orch(tmp_path, lead, name="heptapod")
    _, f2, c2 = _orch(tmp_path, worker, name="phenodex")
    f1("Tool", {})
    f1("Tool", {})
    c2.call_tool.side_effect = ValueError("nope")
    f2("Tool", {})
    (root / "20261116T000000Z-bbbbbb").mkdir()
    Trace(root / "20261116T000000Z-bbbbbb").register_agent(label="x")

    r = _cli(tmp_path, "trace", "list")
    assert r.exit_code == 0, r.output
    line = next(l for l in r.output.splitlines() if "aaaaaa" in l)
    assert line.split()[1:4] == ["2", "3", "1"]
    assert "bbbbbb" in r.output

    r = _cli(tmp_path, "trace", "show", "20261115")
    assert r.exit_code == 0, r.output
    out = r.output
    assert "2 agent(s), 3 call(s), 1 failed" in out
    assert "ag-w  worker (parent ag-lead)  calls 1, failed 1" in out
    assert "heptapod.Tool @1.2.3  2" in out
    assert "phenodex.Tool @1.2.3  1 (1 failed)" in out
    assert "Failures" in out and "nope" in out

    assert _cli(tmp_path, "trace", "show", str(s1)).exit_code == 0
    assert _cli(tmp_path, "trace", "show", "nope").exit_code == 1
    assert _cli(tmp_path, "trace", "show", "2026").exit_code == 1  # ambiguous


def test_cli_list_empty(tmp_path):
    r = _cli(tmp_path, "trace", "list")
    assert r.exit_code == 0 and "No traces" in r.output


def test_cli_serve_trace_flag_passes_a_trace(tmp_path, monkeypatch):
    from toolbase.serve import loadouts
    seen = {}
    monkeypatch.setattr(loadouts, "resolve_loadout", lambda root, cli_loadout=None: object())
    monkeypatch.setattr(orchestrator, "serve",
                        lambda **kw: seen.update(kw) or 0)
    monkeypatch.delenv("TOOLBASE_TRACE", raising=False)
    monkeypatch.delenv("TOOLBASE_TRACE_DIR", raising=False)
    r = _cli(tmp_path, "serve", "--trace", str(tmp_path / "tr"))
    assert r.exit_code == 0, r.output
    assert seen["trace"].dir == tmp_path / "tr"
    assert seen["trace"].base == tmp_path

    r = _cli(tmp_path, "serve")
    assert r.exit_code == 0 and seen["trace"] is None

    monkeypatch.setenv("TOOLBASE_TRACE", "1")
    r = _cli(tmp_path, "serve")
    assert seen["trace"].dir.parent == tmp_path / ".toolbase" / "traces"


# ── the written records follow the trace format (tooltrace docs/FORMAT.md) ──

# A vendored copy of the format's required keys (tooltrace.spec), so this
# check needs no tooltrace. Keep in step with FORMAT.md.
FORMAT_AGENT_STARTED = ("event", "id", "label", "parent", "model", "engine", "harness", "ts")
FORMAT_AGENT_ENDED = ("event", "id", "ts")
FORMAT_CALL = ("schema", "id", "agent", "ts_start", "ts_end", "toolkit", "version", "tool",
               "args", "ok", "error", "result", "files", "parent")
FORMAT_CALL_RESULT = ("sha256", "head")
FORMAT_CALL_FILE = ("path", "bytes", "mtime", "sha256")


def _iso_z(v):
    from datetime import datetime
    assert isinstance(v, str) and v.endswith("Z"), v
    datetime.fromisoformat(v.replace("Z", "+00:00"))


def test_records_follow_the_trace_format(tmp_path):
    d = tmp_path / "trace"
    lead = Trace(d, agent="ag-lead")
    lead.register_agent(label="lead", model="m", engine="claude", harness="conjure")
    (tmp_path / "in.txt").write_text("x")
    _, fwd, client = _orch(tmp_path, lead, name="heptapod", base=tmp_path)
    fwd("Tool", {"path": "in.txt", "api_key": "s"})
    client.call_tool.side_effect = ValueError("boom")
    fwd("Tool", {})
    ctx = {"call_id": "c-outer", "dir": str(d), "agent": "ag-lead", "toolkit": "heptapod",
           "version": "1", "base": str(tmp_path)}
    with tbtrace.child_call(ctx, "Inner", {"a": 1}) as cc:
        cc.result = "ok"
    lead.end_agent()

    events = read_jsonl(d / "agents.jsonl")
    assert [e["event"] for e in events] == ["agent_started", "agent_ended"]
    for e in events:
        keys = FORMAT_AGENT_STARTED if e["event"] == "agent_started" else FORMAT_AGENT_ENDED
        assert all(k in e for k in keys), (keys, e)
        assert isinstance(e["id"], str) and e["id"]
        _iso_z(e["ts"])
    calls = read_jsonl(d / "calls.jsonl")
    assert len(calls) == 3
    for c in calls:
        assert all(k in c for k in FORMAT_CALL), c
        name, _, major = c["schema"].rpartition("/")
        assert (name, major) == ("toolbase-call", "1")
        _iso_z(c["ts_start"])
        _iso_z(c["ts_end"])
        assert isinstance(c["ok"], bool) and isinstance(c["args"], dict)
        assert all(k in c["result"] for k in FORMAT_CALL_RESULT)
        assert isinstance(c["files"], list)
        for f in c["files"]:
            assert all(k in f for k in FORMAT_CALL_FILE), f
    assert calls[0]["files"][0]["path"] == "in.txt"
    assert calls[1]["ok"] is False and calls[1]["error"]
    assert calls[2]["parent"] == "c-outer"
    assert len({c["id"] for c in calls}) == 3


# ── tb trace export delegates to tooltrace ─────────────────────────────


def test_cli_export_hint_without_tooltrace(tmp_path, monkeypatch):
    d = tmp_path / "t"
    Trace(d, agent="ag-1").register_agent(label="x")
    monkeypatch.setitem(sys.modules, "tooltrace", None)
    monkeypatch.setitem(sys.modules, "tooltrace.cli", None)
    r = _cli(tmp_path, "trace", "export", str(d), "-o", str(tmp_path / "b"))
    assert r.exit_code == 1
    assert "tooltrace" in r.output and "pip install git+https://github.com/tonymenzo/tooltrace" in r.output


def test_cli_export_delegates_to_tooltrace(tmp_path, monkeypatch):
    import types
    seen = {}
    fake = types.ModuleType("tooltrace.cli")
    fake.main = lambda argv: seen.setdefault("argv", argv) and 0
    monkeypatch.setitem(sys.modules, "tooltrace", types.ModuleType("tooltrace"))
    monkeypatch.setitem(sys.modules, "tooltrace.cli", fake)
    d = tmp_path / "t"
    Trace(d, agent="ag-1").register_agent(label="x")
    r = _cli(tmp_path, "trace", "export", str(d), "-o", "b", "--sandbox", "w", "--title", "T")
    assert r.exit_code == 0, r.output
    assert seen["argv"] == ["export", str(d), "-o", "b", "--sandbox", "w", "--title", "T"]
