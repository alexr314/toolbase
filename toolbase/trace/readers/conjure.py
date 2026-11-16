"""Conjure's per-agent logs: ``<session>/agents/<agent-id>.jsonl``.

Conjure logs every agent's tool calls in its own format, for every engine:
``{"kind": "stream_end", "tool_calls": [{"id", "name", "args"}], "ts"}`` when
the model asks for tools, and ``{"kind": "tool", "tool_call_id", "text",
"failed", "ts"}`` when each returns (``ts`` in epoch seconds). The file name is
the agent id, so attribution is exact. ``Bash`` and ``shell`` uses become
commands; ``Write``/``Edit``/``MultiEdit``/``NotebookEdit`` commands that name
their file.

Accepts the session directory (holding ``agents/``), the ``agents/``
directory itself, or a run directory with ``conjure-native/sessions/*/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .common import (
    AGENT_ID_RE, SHELL_TOOLS, WRITE_TOOLS, Session, epoch, iter_jsonl,
    make_command, shell_text, split_mcp_name, write_label, write_paths,
)


def agent_files(path: Path) -> List[Path]:
    path = Path(path).expanduser()
    if path.is_file():
        return [path]
    for d in (path / "agents", path):
        if d.is_dir():
            fs = sorted(p for p in d.glob("ag-*.jsonl") if p.is_file())
            if fs:
                return fs
    out: List[Path] = []
    for d in sorted(path.glob("conjure-native/sessions/*/agents")):
        out += sorted(d.glob("ag-*.jsonl"))
    return out


def read(path: Path) -> List[Session]:
    out = []
    for f in agent_files(path):
        aid = f.stem if AGENT_ID_RE.match(f.stem) else None
        s = Session(source="conjure", session=f.stem, path=str(f), agent=aid)
        _read_file(f, s)
        out.append(s)
    return out


def _read_file(f: Path, s: Session) -> None:
    uses: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    results: Dict[str, Dict[str, Any]] = {}
    engine = None
    for rec in iter_jsonl(f):
        kind = rec.get("kind")
        if kind == "stream_end":
            for tc in rec.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    tid = str(tc["id"])
                    if tid not in uses:
                        order.append(tid)
                    uses[tid] = {"name": str(tc.get("name") or ""),
                                 "args": tc.get("args") if isinstance(tc.get("args"), dict) else {},
                                 "ts": epoch(rec.get("ts"))}
        elif kind == "tool" and rec.get("tool_call_id"):
            results[str(rec["tool_call_id"])] = {
                "text": rec.get("text"), "failed": bool(rec.get("failed")),
                "ts": epoch(rec.get("ts"))}
        elif kind == "system_prompt" and engine is None:
            engine = rec.get("engine")
    for tid in order:
        u = uses[tid]
        name, args = u["name"], u["args"]
        res = results.get(tid)
        mcp = split_mcp_name(name)
        if mcp is not None:
            s.mcp.append((mcp[1], args, u["ts"]))
            continue
        # Conjure's Codex engine numbers its items per turn (``item_3``), so
        # its ids are only unique within the agent: qualify them.
        uid = tid if tid.startswith(("toolu_", "call_")) else f"{s.session}:{tid}"
        if name in SHELL_TOOLS:
            cmd = args.get("command", args.get("cmd"))
            if cmd is None:
                continue
            s.commands.append(make_command(
                source="conjure", session=s.session, tool_use_id=uid, tool=name,
                command=shell_text(cmd), ts_start=u["ts"],
                ts_end=res["ts"] if res else None,
                ok=None if res is None else not res["failed"],
                output=res["text"] if res else None, agent=s.agent,
                background=bool(args.get("run_in_background")),
                description=args.get("description") if isinstance(args.get("description"), str) else None,
            ))
            s.commands[-1]["native_id"] = tid
        elif name in WRITE_TOOLS:
            s.commands.append(make_command(
                source="conjure", session=s.session, tool_use_id=uid, tool=name,
                command=write_label(name, args), ts_start=u["ts"],
                ts_end=res["ts"] if res else None,
                ok=None if res is None else not res["failed"],
                output=res["text"] if res else None, agent=s.agent,
                writes=write_paths(name, args),
            ))
            s.commands[-1]["native_id"] = tid
