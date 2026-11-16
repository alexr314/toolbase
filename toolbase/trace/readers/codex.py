"""Codex rollouts: ``<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-*.jsonl``.

Each line is ``{"timestamp", "type", "payload"}``. Shell commands appear in
two shapes, both read:

- current Codex: ``event_msg`` ``item_completed`` with an item of type
  ``CommandExecution`` (``id``, ``command`` as argv or string, ``cwd``,
  ``aggregated_output``, ``exit_code``, ``status``), timed by the payload's
  ``started_at_ms`` and ``completed_at_ms``;
- older Codex: ``response_item`` ``function_call`` (``name`` shell,
  ``exec_command`` or ``local_shell``; ``arguments`` a JSON string with
  ``command`` or ``cmd``; ``call_id``), its output in a ``function_call_output``
  with the same ``call_id``, and ``exec_command_end`` events with exit codes.

``apply_patch`` (a ``custom_tool_call`` or ``function_call``) becomes a
command naming the files the patch adds or updates. Toolkit calls
(``McpToolCall`` items, ``mcp__…`` function calls) are kept for agent matching.

The agent: Conjure keeps one Codex home per agent at
``<run>/conjure-native/codex_homes/<agent-id>/``, so a home named ``ag-…``
gives the agent; otherwise Conjure's identity line in the first message.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import (
    AGENT_ID_RE, IDENTITY_RE, Session, epoch, iter_jsonl, make_command,
    shell_text, split_mcp_name, text_of,
)

_SHELL_FUNCS = {"shell", "exec_command", "local_shell", "container.exec", "shell_command"}
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.M)


def rollout_files(path: Path) -> List[Path]:
    """A rollout file, or every ``rollout-*.jsonl`` under a directory (a
    ``sessions/`` tree, a Codex home, or a directory of homes)."""
    path = Path(path).expanduser()
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.rglob("rollout-*.jsonl"))
    return []


def _home_agent(f: Path) -> Optional[str]:
    """``…/codex_homes/<ag-id>/sessions/…`` -> ``ag-id``."""
    parts = f.parts
    for i, p in enumerate(parts):
        if p == "sessions" and i > 0 and AGENT_ID_RE.match(parts[i - 1]):
            return parts[i - 1]
    return None


def read(path: Path) -> List[Session]:
    out = []
    for f in rollout_files(path):
        s = _read_file(f)
        if s.commands or s.mcp or s.hint or s.agent:
            out.append(s)
    return out


def _read_file(f: Path) -> Session:
    s = Session(source="codex", session=f.stem.removeprefix("rollout-"), path=str(f),
                agent=_home_agent(f))
    calls: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    outputs: Dict[str, Dict[str, Any]] = {}
    ends: Dict[str, Dict[str, Any]] = {}
    seen_items = set()
    cwd = None
    for rec in iter_jsonl(f):
        typ, p = rec.get("type"), rec.get("payload")
        if not isinstance(p, dict):
            continue
        ts = epoch(rec.get("timestamp"))
        ptype = p.get("type")
        if typ == "session_meta":
            s.session = str(p.get("id") or p.get("session_id") or s.session)
            cwd = p.get("cwd") or cwd
            continue
        if typ == "turn_context" and p.get("cwd"):
            cwd = p["cwd"]
        if s.hint is None and (ptype in ("message", "item_completed", "user_message")):
            m = IDENTITY_RE.search(json.dumps(p, default=str)[:200000])
            if m:
                s.hint = m.group(1)
        if typ == "event_msg" and ptype == "item_completed":
            item = p.get("item") or {}
            itype = item.get("type")
            if itype == "CommandExecution" and item.get("id") and item["id"] not in seen_items:
                seen_items.add(item["id"])
                code = item.get("exit_code")
                status = item.get("status")
                ok = (code == 0) if isinstance(code, int) else (
                    None if status is None else status == "completed")
                s.commands.append(make_command(
                    source="codex", session=s.session, tool_use_id=str(item["id"]),
                    tool="exec_command", command=shell_text(item.get("command")),
                    ts_start=epoch(p.get("started_at_ms")) or ts,
                    ts_end=epoch(p.get("completed_at_ms")) or ts,
                    ok=ok, exit_code=code if isinstance(code, int) else None,
                    output=item.get("aggregated_output"),
                    cwd=_cwd(item.get("cwd")) or cwd,
                ))
            elif itype == "McpToolCall":
                tool = str(item.get("tool") or "")
                sp = split_mcp_name(tool) or split_mcp_name(f"{item.get('server')}__{tool}")
                args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                if sp is not None or item.get("server") == "toolbase":
                    s.mcp.append(((sp[1] if sp else tool), args, epoch(p.get("started_at_ms")) or ts))
            elif itype == "FileChange" and item.get("id"):
                paths = [c.get("path") for c in (item.get("changes") or []) if isinstance(c, dict)]
                paths = [x for x in paths if isinstance(x, str)]
                if paths:
                    s.commands.append(make_command(
                        source="codex", session=s.session, tool_use_id=str(item["id"]),
                        tool="apply_patch", command="apply_patch " + " ".join(paths),
                        ts_start=epoch(p.get("started_at_ms")) or ts,
                        ts_end=epoch(p.get("completed_at_ms")) or ts,
                        ok=item.get("status") in (None, "completed"), cwd=cwd, writes=paths,
                    ))
            continue
        if typ == "event_msg" and ptype == "exec_command_end" and p.get("call_id"):
            ends[str(p["call_id"])] = {"ts": ts, "exit_code": p.get("exit_code"),
                                       "output": p.get("aggregated_output") or p.get("stdout")}
            continue
        if typ != "response_item":
            continue
        if ptype in ("function_call", "custom_tool_call", "local_shell_call") and p.get("call_id"):
            cid = str(p["call_id"])
            if cid not in calls:
                order.append(cid)
            calls[cid] = {"name": str(p.get("name") or ptype), "ptype": ptype,
                          "arguments": p.get("arguments"), "input": p.get("input"),
                          "action": p.get("action"), "ts": ts}
        elif ptype in ("function_call_output", "custom_tool_call_output") and p.get("call_id"):
            outputs[str(p["call_id"])] = {"ts": ts, "output": p.get("output")}

    for cid in order:
        c = calls[cid]
        name = c["name"]
        out = outputs.get(cid)
        end = ends.get(cid)
        t_end = (end or {}).get("ts") or (out or {}).get("ts")
        if name == "apply_patch":
            patch = c["input"] if isinstance(c["input"], str) else _args(c["arguments"]).get("input", "")
            paths = _PATCH_FILE_RE.findall(patch or "")
            if paths:
                s.commands.append(make_command(
                    source="codex", session=s.session, tool_use_id=cid, tool="apply_patch",
                    command="apply_patch " + " ".join(p.strip() for p in paths),
                    ts_start=c["ts"], ts_end=t_end, ok=None if out is None else True,
                    output=_out_text(out), cwd=cwd, writes=[p.strip() for p in paths],
                ))
            continue
        sp = split_mcp_name(name)
        if sp is not None:
            s.mcp.append((sp[1], _args(c["arguments"]), c["ts"]))
            continue
        if name in _SHELL_FUNCS or c["ptype"] == "local_shell_call":
            a = _args(c["arguments"])
            if c["ptype"] == "local_shell_call" and isinstance(c["action"], dict):
                a = c["action"]
            cmd = a.get("command", a.get("cmd"))
            if cmd is None:
                continue
            code = (end or {}).get("exit_code")
            text = (end or {}).get("output") or _out_text(out)
            if code is None and isinstance(text, str):
                m = re.search(r'"exit_code"\s*:\s*(-?\d+)', text[:4000]) or \
                    re.search(r"Process exited with code (-?\d+)", text[:4000])
                if m:
                    code = int(m.group(1))
            s.commands.append(make_command(
                source="codex", session=s.session, tool_use_id=cid, tool=name,
                command=shell_text(cmd), ts_start=c["ts"], ts_end=t_end,
                ok=None if (out is None and end is None) else (code in (None, 0)),
                exit_code=code if isinstance(code, int) else None, output=text,
                cwd=_cwd(a.get("workdir") or a.get("cwd")) or cwd,
            ))
    return s


def _args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}
    return {}


def _out_text(out: Optional[Dict[str, Any]]) -> Optional[str]:
    if not out:
        return None
    o = out.get("output")
    if isinstance(o, dict):
        return text_of(o.get("content")) or json.dumps(o, default=str)
    return text_of(o)


def _cwd(v: Any) -> Optional[str]:
    if isinstance(v, str) and v:
        return v[len("file://"):] if v.startswith("file://") else v
    return None
