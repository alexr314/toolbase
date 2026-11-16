"""Provenance trace, phase 4: ``tb trace export``, the transcript readers and
the viewer.

The fixture ``fixtures/trace/hnl-mini`` is a trimmed conversion of a real
Conjure run (``fixtures/trace/conjure_run_to_trace.py --trim``): 7 agents,
15 toolkit calls, the session's spawns and messages, the small files those
calls name, and trimmed Conjure, Claude Code and Codex transcripts. Git does
not keep mtimes, so ``sandbox_mtimes.json`` carries the originals and each
test copies the fixture and restores them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase.trace import readers
from toolbase.trace.export import ExportError, export, file_node_id, parse_size
from toolbase.trace.readers import claude as claude_reader
from toolbase.trace.readers import codex as codex_reader
from toolbase.trace.readers import conjure as conjure_reader
from toolbase.trace.readers.common import command_id

FIX = Path(__file__).parent / "fixtures" / "trace" / "hnl-mini"
BUILD = "ag-zcjbjaxkdtnrc"
SENS = "ag-wzw6pxndboggo"
WRITER = "ag-gcxafk2vu2tbs"
ROOT = "ag-djgp5wfztgppg"


@pytest.fixture
def run(tmp_path):
    """A copy of the fixture with its sandbox mtimes restored."""
    dst = tmp_path / "run"
    shutil.copytree(FIX, dst)
    mtimes = json.loads((dst / "sandbox_mtimes.json").read_text())
    for rel, mt in mtimes.items():
        os.utime(dst / "sandbox" / rel, (mt, mt))
    return dst


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _load(bundle: Path, name: str):
    return json.loads((bundle / name).read_text(encoding="utf-8"))


def _export(run, tmp_path, **kw):
    out = tmp_path / "bundle"
    kw.setdefault("sandbox", run / "sandbox")
    r = export(run / "trace", out, **kw)
    return out, r


# ── export: graph, PROV, files, copies ─────────────────────────────────


def test_export_nodes_and_edges(run, tmp_path):
    out, r = _export(run, tmp_path)
    doc = _load(out, "trace.json")
    assert doc["schema"] == "toolbase-trace-bundle/1"
    nodes = {n["id"]: n for n in doc["nodes"]}
    kinds = {}
    for n in doc["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    assert kinds["call"] == 15
    agents = {i for i, n in nodes.items() if n["kind"] == "agent"}
    assert {ROOT, BUILD, SENS, WRITER} <= agents and "@user" in agents
    assert nodes[BUILD]["parent"] == ROOT and nodes[BUILD]["label"] == "build"
    assert nodes[BUILD]["spawn"] == f"m-spawn-{BUILD}"
    assert nodes[f"m-spawn-{BUILD}"]["initial_message"]  # the brief
    # every edge joins two nodes
    for e in doc["edges"]:
        assert e["from"] in nodes and e["to"] in nodes, e
    ek = {e["kind"] for e in doc["edges"]}
    assert {"spawned", "sent", "received", "called", "used", "generated"} <= ek
    assert any(e["kind"] == "spawned" and e["from"] == ROOT and e["to"] == BUILD for e in doc["edges"])
    # ValidateLagrangian used the model file and generated its logs
    call = "c-45e039abfa"
    used = {nodes[e["to"]]["paths"][0] for e in doc["edges"] if e["from"] == call and e["kind"] == "used"}
    gen = {nodes[e["to"]]["paths"][0] for e in doc["edges"] if e["from"] == call and e["kind"] == "generated"}
    assert used == {"assays/hnl-mu-8tev/models/hnl_mu.fr"}
    assert gen == {"_logs/feynrules_validate/wolframscript_stdout.log",
                   "_logs/feynrules_validate/wolframscript_stderr.log"}
    fr = run / "sandbox/assays/hnl-mu-8tev/models/hnl_mu.fr"
    assert nodes[file_node_id(_sha(fr), "")]["sha256"] == _sha(fr)
    assert r["meta"]["counts"]["calls"] == 15 and r["meta"]["complete"] is True


def test_prov_mapping(run, tmp_path):
    out, _ = _export(run, tmp_path)
    doc = _load(out, "trace.json")
    prov = _load(out, "prov.json")
    assert prov["prefix"]["tb"].startswith("https://")
    allowed = {"prefix", "entity", "activity", "agent", "used", "wasGeneratedBy",
               "wasAssociatedWith", "actedOnBehalfOf", "wasInformedBy", "wasAttributedTo",
               "wasInfluencedBy"}
    assert set(prov) <= allowed
    calls = [n for n in doc["nodes"] if n["kind"] == "call"]
    for c in calls:
        a = prov["activity"]["tb:" + c["id"]]
        assert a["prov:startTime"] == c["ts_start"] and a["prov:endTime"] == c["ts_end"]
        assert a["prov:type"]["$"] == "tb:ToolCall"
        assert a["tb:tool"] == c["tool"]
        # structured values are JSON strings (PROV-JSON values are literals)
        assert json.loads(a["tb:args"]) == c["args"]
    assert prov["agent"]["tb:" + BUILD]["prov:type"]["$"] == "prov:SoftwareAgent"
    assert prov["agent"]["tb:@user"]["prov:type"]["$"] == "prov:Person"
    n_called = sum(1 for e in doc["edges"] if e["kind"] == "called")
    assert len(prov["wasAssociatedWith"]) == n_called
    deleg = list(prov["actedOnBehalfOf"].values())
    assert {"prov:delegate": "tb:" + BUILD, "prov:responsible": "tb:" + ROOT} in [
        {k: d[k] for k in ("prov:delegate", "prov:responsible")} for d in deleg]
    for g in prov["wasGeneratedBy"].values():
        assert g["prov:entity"] in prov["entity"] and g["prov:activity"] in prov["activity"]
    for u in prov["used"].values():
        assert u["prov:entity"] in prov["entity"] and u["prov:activity"] in prov["activity"]
    for rel in prov.values():
        if isinstance(rel, dict):
            assert all(isinstance(k, str) and (k.startswith(("tb:", "_:")) or k == "tb") for k in rel)


def test_files_json_and_unknown_origin(run, tmp_path):
    stray = run / "sandbox" / "notes" / "handwritten.txt"
    stray.parent.mkdir()
    stray.write_text("nothing generated me\n")
    os.utime(stray, (1794779000.0, 1794779000.0))  # before the first record
    out, r = _export(run, tmp_path)
    rows = {f["path"]: f for f in _load(out, "files.json")["files"]}
    for p in (run / "sandbox").rglob("*"):
        if p.is_file():
            rel = p.relative_to(run / "sandbox").as_posix()
            assert rows[rel]["sha256"] == _sha(p) and rows[rel]["bytes"] == p.stat().st_size
    s = rows["notes/handwritten.txt"]
    assert s["origin"] == "unknown" and s["before_trace"] and "node" not in s
    log = rows["_logs/feynrules_validate/wolframscript_stdout.log"]
    assert log["origin"] == "recorded" and log["generated_by"] == ["c-45e039abfa"]
    fr = rows["assays/hnl-mu-8tev/models/hnl_mu.fr"]
    assert fr["origin"] == "unknown" and set(fr["used_by"]) >= {"c-45e039abfa", "c-a1fdee71f9"}
    assert r["meta"]["counts"]["unknown_origin"] >= 2


def test_copy_limits(run, tmp_path):
    big = run / "sandbox/assays/hnl-mu-8tev/models/hnl_mu.fr"
    limit = big.stat().st_size - 1
    out, r = _export(run, tmp_path, copy_limit=limit)
    rows = {f["path"]: f for f in _load(out, "files.json")["files"]}
    fr = rows["assays/hnl-mu-8tev/models/hnl_mu.fr"]
    assert "copied" not in fr and fr["sha256"] == _sha(big)  # listed with hash, not copied
    assert not (out / "files/assays/hnl-mu-8tev/models/hnl_mu.fr").exists()
    for f in rows.values():
        if f.get("node") and f["bytes"] <= limit:
            assert _sha(out / f["copied"]) == f["sha256"]
        else:
            assert "copied" not in f
    log = rows["_logs/feynrules_validate/wolframscript_stderr.log"]
    assert log["copied"] == "files/_logs/feynrules_validate/wolframscript_stderr.log"
    # graph mode copies only files in the graph
    stray = run / "sandbox/other.txt"
    stray.write_text("x")
    out2 = tmp_path / "b2"
    export(run / "trace", out2, sandbox=run / "sandbox")
    assert not (out2 / "files/other.txt").exists()
    out3 = tmp_path / "b3"
    export(run / "trace", out3, sandbox=run / "sandbox", copy_mode="all")
    assert (out3 / "files/other.txt").exists()
    out4 = tmp_path / "b4"
    r4 = export(run / "trace", out4, sandbox=run / "sandbox", copy_mode="none")
    assert not (out4 / "files").exists() and r4["meta"]["counts"]["copied"] == 0
    out5 = tmp_path / "b5"
    r5 = export(run / "trace", out5, sandbox=run / "sandbox", copy_total=10)
    assert r5["meta"]["counts"]["copied_bytes"] <= 10
    assert any("copy budget" in w for w in r5["meta"]["warnings"])


def test_export_without_sandbox_and_refuses_overwrite(run, tmp_path):
    out = tmp_path / "b"
    r = export(run / "trace", out)
    assert any("no --sandbox" in w for w in r["meta"]["warnings"])
    rows = _load(out, "files.json")["files"]
    assert rows and all(not f["present"] for f in rows)
    with pytest.raises(ExportError, match="not empty"):
        export(run / "trace", out)
    export(run / "trace", out, force=True)
    other = tmp_path / "notabundle"
    other.mkdir()
    (other / "keep.txt").write_text("mine")
    with pytest.raises(ExportError, match="not a bundle"):
        export(run / "trace", other, force=True)
    assert (other / "keep.txt").exists()
    with pytest.raises(ExportError, match="not a trace directory"):
        export(tmp_path, tmp_path / "x")


def test_redaction(run, tmp_path):
    sb = run / "sandbox"
    calls = (run / "trace/calls.jsonl").read_text().replace("/home/user/runs/hnl/workspace", str(sb))
    (run / "trace/calls.jsonl").write_text(calls)
    out, _ = _export(run, tmp_path)
    for name in ("trace.json", "files.json", "prov.json", "index.html"):
        assert str(sb) not in (out / name).read_text(encoding="utf-8"), name
    out2 = tmp_path / "raw"
    export(run / "trace", out2, sandbox=sb, redact=False)
    assert str(sb) in (out2 / "trace.json").read_text()


def test_truncated_killed_trace(run, tmp_path):
    t = run / "trace"
    agents = [l for l in (t / "agents.jsonl").read_text().splitlines() if '"agent_ended"' not in l]
    (t / "agents.jsonl").write_text("\n".join(agents) + "\n")
    with open(t / "calls.jsonl", "a") as f:
        f.write('{"schema":"toolbase-call/1","id":"c-torn","agent":"ag-')  # killed mid-line
    with open(t / "messages.jsonl", "a") as f:
        f.write('{"event":"send","ts":"2026-11-16T00:09:00')
    out, r = _export(run, tmp_path)
    meta = r["meta"]
    assert meta["complete"] is False
    w = " ".join(meta["warnings"])
    assert "calls.jsonl: 1 torn" in w and "messages.jsonl: 1 torn" in w and "never ended" in w
    ids = {n["id"] for n in _load(out, "trace.json")["nodes"]}
    assert "c-torn" not in ids and "c-45e039abfa" in ids
    # calls only, no agents.jsonl at all
    (t / "agents.jsonl").unlink()
    (t / "messages.jsonl").unlink()
    out2 = tmp_path / "b2"
    r2 = export(t, out2)
    agents2 = [n for n in _load(out2, "trace.json")["nodes"] if n["kind"] == "agent"]
    assert {a["id"] for a in agents2} >= {BUILD, SENS} and all(a.get("unregistered") for a in agents2)
    assert r2["meta"]["counts"]["calls"] == 15


def test_parent_links_become_child_of(run, tmp_path):
    t = run / "trace"
    rec = {"schema": "toolbase-call/1", "id": "c-inner00001", "agent": SENS,
           "ts_start": "2026-11-15T22:31:10.000Z", "ts_end": "2026-11-15T22:35:00.000Z",
           "toolkit": "heptapod", "version": "3.14.1", "tool": "MadGraphFromRunCard", "args": {},
           "ok": True, "error": None, "result": {"sha256": "0", "head": ""}, "files": [],
           "parent": "c-24353bac4f"}
    with open(t / "calls.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    out, _ = _export(run, tmp_path)
    edges = _load(out, "trace.json")["edges"]
    assert {"kind": "child_of", "from": "c-inner00001", "to": "c-24353bac4f"} in edges
    prov = _load(out, "prov.json")
    assert any(v["prov:informed"] == "tb:c-inner00001" for v in prov["wasInformedBy"].values())


# ── commands: readers, attribution, inference ──────────────────────────


def test_claude_reader(run):
    sessions = claude_reader.read(run / "claude")
    assert len(sessions) == 1
    s = sessions[0]
    assert s.hint == BUILD and s.source == "claude"
    assert s.commands and all(c["id"].startswith("cmd-") for c in s.commands)
    c = s.commands[0]
    assert c["id"] == command_id(c["tool_use_id"]) and c["tool_use_id"].startswith("toolu_")
    assert c["tool"] == "Bash" and c["command"].startswith("cd assays/hnl-mu-8tev")
    assert c["ts_start"] and c["ts_end"] and c["ts_end"] >= c["ts_start"]
    # the real first command failed ("Exit code 1"): && chain on a missing file
    assert c["ok"] is False and c["exit_code"] == 1 and c["output"]["sha256"]
    assert any(x["ok"] is True for x in s.commands)


def test_claude_reader_exit_code_write_tools_and_mcp_matching(tmp_path, run):
    t = tmp_path / "sess.jsonl"
    lines = [
        {"type": "assistant", "timestamp": "2026-11-15T22:02:17.100Z", "cwd": "/w",
         "message": {"content": [
             {"type": "tool_use", "id": "toolu_A", "name": "Bash", "input": {"command": "false"}},
             {"type": "tool_use", "id": "toolu_B", "name": "Write",
              "input": {"file_path": "/w/out.txt", "content": "x"}},
             {"type": "tool_use", "id": "toolu_C", "name": "mcp__toolbase__heptapod__ValidateLagrangian",
              "input": {"fr_path": "assays/hnl-mu-8tev/models/hnl_mu.fr"}},
             {"type": "tool_use", "id": "toolu_D", "name": "Bash", "input": {"command": "sleep 999"}}]}},
        {"type": "user", "timestamp": "2026-11-15T22:02:18.000Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_A", "content": "Exit code 1\n", "is_error": True},
            {"type": "tool_result", "tool_use_id": "toolu_B", "content": "ok"}]}},
        "not json {",
    ]
    t.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n")
    [s] = claude_reader.read(t)
    by = {c["tool_use_id"]: c for c in s.commands}
    assert by["toolu_A"]["ok"] is False and by["toolu_A"]["exit_code"] == 1
    assert by["toolu_B"]["writes"] == ["/w/out.txt"] and by["toolu_B"]["tool"] == "Write"
    assert by["toolu_D"]["ts_end"] is None and by["toolu_D"]["ok"] is None  # killed: no result
    assert s.hint is None and s.mcp[0][0] == "ValidateLagrangian"
    # no identity line: attributed through the toolkit call it made
    calls = [json.loads(l) for l in (run / "trace/calls.jsonl").read_text().splitlines()]
    owner, _ = readers.attribute([s], {}, calls)
    assert owner[f"claude:{s.session}"] == BUILD
    assert s.attributed_by.startswith("toolkit calls")


def test_conjure_reader(run):
    sessions = {s.agent: s for s in conjure_reader.read(run / "conjure")}
    assert BUILD in sessions and WRITER in sessions
    b = sessions[BUILD]
    assert all(c["agent"] == BUILD for c in b.commands)
    assert any(name == "ValidateLagrangian" for name, _a, _t in b.mcp)
    w = sessions[WRITER]  # Codex engine: shell, item ids qualified by agent
    assert w.commands and all(c["tool"] == "shell" for c in w.commands)
    assert all(c["tool_use_id"].startswith(WRITER + ":item_") for c in w.commands)


def test_codex_reader(run, tmp_path):
    [s] = codex_reader.read(run / "codex")
    assert s.agent == WRITER  # from codex_homes/<agent-id>/
    assert s.commands and all(c["tool"] == "exec_command" for c in s.commands)
    c = s.commands[0]
    assert c["exit_code"] == 0 and c["ok"] is True and c["cwd"].endswith("workspace")
    assert not c["command"].startswith("/bin/zsh")  # argv [zsh, -lc, cmd] -> cmd
    # older rollouts: function_call / function_call_output / exec_command_end
    old = tmp_path / "home" / "sessions" / "rollout-old.jsonl"
    old.parent.mkdir(parents=True)
    recs = [
        {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/w"}},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "event_msg", "payload": {"type": "user_message",
            "message": "- Address id:  `ag-old0000000`"}},
        {"timestamp": "2026-01-01T00:00:02Z", "type": "response_item", "payload": {
            "type": "function_call", "name": "shell", "call_id": "call_1",
            "arguments": json.dumps({"command": ["bash", "-lc", "python run.py > out.json"], "workdir": "/w"})}},
        {"timestamp": "2026-01-01T00:00:05Z", "type": "event_msg", "payload": {
            "type": "exec_command_end", "call_id": "call_1", "exit_code": 2}},
        {"timestamp": "2026-01-01T00:00:05Z", "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call_1", "output": "boom"}},
        {"timestamp": "2026-01-01T00:00:06Z", "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "apply_patch", "call_id": "call_2",
            "input": "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch"}},
    ]
    old.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    [o] = codex_reader.read(tmp_path / "home")
    assert o.agent is None and o.hint == "ag-old0000000"
    by = {c["tool_use_id"]: c for c in o.commands}
    assert by["call_1"]["command"] == "python run.py > out.json"
    assert by["call_1"]["exit_code"] == 2 and by["call_1"]["ok"] is False
    assert by["call_1"]["ts_end"] == "2026-01-01T00:00:05.000Z"
    assert by["call_2"]["writes"] == ["src/a.py"]


def test_collect_merges_and_prefers(run):
    cmds, notes, sessions = readers.collect(
        [f"conjure:{run / 'conjure'}", f"claude:{run / 'claude'}", f"codex:{run / 'codex'}"],
        {}, [])
    ids = [c["id"] for c in cmds]
    assert len(ids) == len(set(ids))
    build = [c for c in cmds if c["agent"] == BUILD]
    assert build and all(set(c.get("sources", [])) == {"claude", "conjure"} for c in build if c["tool"] == "Bash")
    # the Codex agent's commands come from its rollout, not Conjure's view
    w = [c for c in cmds if c["agent"] == WRITER]
    assert w and all(c["source"] == "codex" for c in w)
    with pytest.raises(ValueError):
        readers.parse_spec("bogus:/x")


def test_export_with_commands(run, tmp_path):
    out, r = _export(run, tmp_path, commands=[f"conjure:{run / 'conjure'}", f"claude:{run / 'claude'}",
                                              f"codex:{run / 'codex'}"])
    doc = _load(out, "trace.json")
    cmds = [n for n in doc["nodes"] if n["kind"] == "command"]
    assert cmds and all(n["id"].startswith("cmd-") for n in cmds)
    agents = {n["id"] for n in doc["nodes"] if n["kind"] == "agent"}
    assert all(c["agent"] in agents for c in cmds)
    called = {(e["from"], e["to"]) for e in doc["edges"] if e["kind"] == "called"}
    assert all((c["agent"], c["id"]) in called for c in cmds)
    assert r["meta"]["counts"]["commands"] == len(cmds)


def test_command_inference(tmp_path):
    """A command that writes a file it names gets it (target+mtime); one that
    runs in a named directory gets the files written there in its window;
    nothing gets a file no command or call names."""
    sb = tmp_path / "sb"
    (sb / "scripts").mkdir(parents=True)
    (sb / "out").mkdir()
    script = sb / "scripts" / "agg.py"
    script.write_text("print(1)\n")
    os.utime(script, (1000.0, 1000.0))
    for name, mt in (("out/r.json", 2005.0), ("out/side.json", 2006.0), ("other.dat", 2007.0)):
        (sb / name).write_text(name)
        os.utime(sb / name, (mt, mt))
    tdir = tmp_path / "trace"
    tdir.mkdir()
    (tdir / "agents.jsonl").write_text(json.dumps({"event": "agent_started", "id": "ag-a", "label": "a",
                                                   "parent": None, "ts": "1970-01-01T00:16:40.000Z"}) + "\n")
    (tdir / "calls.jsonl").write_text("")
    tr = tmp_path / "t.jsonl"
    iso = lambda t: f"1970-01-01T00:{int(t) // 60:02d}:{int(t) % 60:02d}.000Z"
    tr.write_text("\n".join(json.dumps(x) for x in [
        {"type": "assistant", "timestamp": iso(2000), "cwd": str(sb), "message": {"content": [
            {"type": "tool_use", "id": "toolu_X", "name": "Bash",
             "input": {"command": "cd out && python ../scripts/agg.py > r.json"}}]}},
        {"type": "user", "timestamp": iso(2010), "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_X", "content": ""}]}},
    ]) + "\n")
    out = tmp_path / "b"
    export(tdir, out, sandbox=sb, commands=[f"claude:{tr}"])
    rows = {f["path"]: f for f in _load(out, "files.json")["files"]}
    cid = command_id("toolu_X")
    assert rows["out/r.json"]["generated_by"] == [cid] and rows["out/r.json"]["basis"] == "target+mtime"
    assert rows["out/side.json"]["generated_by"] == [cid] and rows["out/side.json"]["basis"] == "dir+mtime"
    assert rows["other.dat"]["origin"] == "unknown" and rows["other.dat"]["candidates"] == [cid]
    assert rows["scripts/agg.py"]["used_by"] == [cid]
    nodes = {n["id"]: n for n in _load(out, "trace.json")["nodes"]}
    assert nodes[cid]["agent"] == "ag-a"  # the trace's only agent
    assert nodes[cid]["script"]["path"] == "scripts/agg.py"
    assert nodes[cid]["script"]["sha256"] == _sha(script)
    gen = [e for e in _load(out, "trace.json")["edges"] if e["kind"] == "generated"]
    assert gen and all(e["inferred"] for e in gen)


# ── viewer ─────────────────────────────────────────────────────────────


def _embedded(html: str):
    m = re.search(r'<script type="application/json" id="trace-data">(.*?)</script>', html, re.S)
    assert m, "no embedded data"
    return json.loads(m.group(1))


def test_viewer_embeds_data_and_anchors(run, tmp_path):
    t = run / "trace" / "messages.jsonl"
    with open(t, "a") as f:  # a body that would end the script element
        f.write(json.dumps({"event": "send", "ts": "2026-11-16T00:09:00.000Z", "from": ROOT, "to": BUILD,
                            "body": "</script><script>alert(1)</script> <!-- x", "msg_id": "msg-evil"}) + "\n")
    out, _ = _export(run, tmp_path, commands=[f"conjure:{run / 'conjure'}"])
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "</script><script>alert(1)" not in html and "<!-- x" not in html and "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"<(script|link)[^>]+(src|href)=", html)  # nothing loaded from outside
    data = _embedded(html)
    doc = _load(out, "trace.json")
    assert len(data["nodes"]) == len(doc["nodes"]) and len(data["edges"]) == len(doc["edges"])
    assert len(data["files"]["rows"]) == len(_load(out, "files.json")["files"])
    assert any(str(n.get("body")).startswith("</script>") for n in data["nodes"] if n["kind"] == "message")
    ids = {n["id"] for n in data["nodes"]}
    assert "c-45e039abfa" in ids and BUILD in ids and any(i.startswith("cmd-") for i in ids)
    assert any(i.startswith("f-") for i in ids)
    # the anchor router knows each prefix
    for prefix in ("c|cmd", "^f-", "location.hash", "hashchange"):
        assert prefix in html
    assert len(html) < 2_000_000


def test_viewer_js_parses(run, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out, _ = _export(run, tmp_path)
    html = (out / "index.html").read_text(encoding="utf-8")
    js = html.rsplit("<script>", 1)[1].rsplit("</script>", 1)[0]
    p = tmp_path / "v.js"
    p.write_text(js)
    res = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


# ── CLI and isolation ──────────────────────────────────────────────────


def test_cli_export(run, tmp_path):
    from toolbase.cli import main
    out = tmp_path / "bundle"
    res = CliRunner().invoke(main, ["trace", "export", str(run / "trace"), "-o", str(out),
                                    "--sandbox", str(run / "sandbox"), "--copy-limit", "4k",
                                    "--commands", f"conjure:{run / 'conjure'}"])
    assert res.exit_code == 0, res.output
    assert "15 calls" in res.output and "index.html" in res.output
    assert (out / "index.html").is_file() and (out / "prov.json").is_file()
    res = CliRunner().invoke(main, ["trace", "export", str(run / "trace"), "-o", str(out)])
    assert res.exit_code == 1 and "--force" in res.output
    res = CliRunner().invoke(main, ["trace", "export", str(run / "trace"), "-o", str(tmp_path / "c"),
                                    "--commands", "nope:/x"])
    assert res.exit_code == 1 and "kind:path" in res.output.lower().replace("<kind>:<path>", "kind:path")


def test_parse_size():
    assert parse_size("1MB") == 1 << 20 and parse_size("500k") == 500 * 1024
    assert parse_size("0") == 0 and parse_size("2G") == 2 << 30
    with pytest.raises(ValueError):
        parse_size("lots")


def test_serve_path_does_not_import_export():
    code = ("import sys, toolbase.trace, toolbase.serve.orchestrator; "
            "print([m for m in sys.modules if m.startswith('toolbase.trace.')])")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert res.returncode == 0, res.stderr
    mods = eval(res.stdout.strip())
    assert not any(m.startswith(("toolbase.trace.export", "toolbase.trace.readers",
                                 "toolbase.trace.viewer")) for m in mods), mods
