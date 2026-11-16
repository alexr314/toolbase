"""Claude Code session transcripts: ``~/.claude/projects/<escaped cwd>/*.jsonl``.

Each line is one event. An ``assistant`` line's ``message.content`` holds
``tool_use`` parts (``id``, ``name``, ``input``); the matching ``tool_result``
(``tool_use_id``, ``content``, ``is_error``) arrives in a later ``user`` line,
with the shell's stdout and stderr also under ``toolUseResult``. Times are each
line's ``timestamp``. A ``Bash`` use becomes a command; ``Write``, ``Edit``,
``MultiEdit`` and ``NotebookEdit`` become commands that name the file they
write. Subagent transcripts (``<session>/subagents/*.jsonl``) are read as
part of their parent session.

The agent: Conjure's identity line (``Address id: `ag-…```) in the system
prompt or first message, when present; otherwise export matches the
session's toolkit calls against the trace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .common import (
    IDENTITY_RE, SHELL_TOOLS, WRITE_TOOLS, Session, epoch, iter_jsonl,
    make_command, split_mcp_name, text_of, write_label, write_paths,
)


def transcript_files(path: Path) -> List[Path]:
    """A ``.jsonl`` file, or every ``.jsonl`` under a directory (a Claude Code
    project directory, or a single session's directory)."""
    path = Path(path).expanduser()
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob("*.jsonl") if p.is_file())
    return []


def _session_key(path: Path, root: Path) -> str:
    """Subagent transcripts belong to their parent session: the first path
    component under the project directory names it."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return path.stem
    return rel.parts[0].removesuffix(".jsonl") if len(rel.parts) > 1 else path.stem


def read(path: Path) -> List[Session]:
    path = Path(path).expanduser()
    root = path if path.is_dir() else path.parent
    sessions: Dict[str, Session] = {}
    for f in transcript_files(path):
        key = _session_key(f, root)
        s = sessions.get(key)
        if s is None:
            s = sessions[key] = Session(source="claude", session=key, path=str(f))
        _read_file(f, s, sidechain=(f.stem != key))
    return [s for s in sessions.values() if s.commands or s.mcp or s.hint]


def _read_file(f: Path, s: Session, sidechain: bool) -> None:
    uses: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    results: Dict[str, Dict[str, Any]] = {}
    for rec in iter_jsonl(f):
        typ = rec.get("type")
        if s.hint is None:
            m = _identity(rec)
            if m:
                s.hint = m
        if rec.get("sessionId") and s.session == f.stem and not sidechain:
            s.session = str(rec["sessionId"])
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        ts = epoch(rec.get("timestamp"))
        if typ == "assistant":
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("id"):
                    tid = str(part["id"])
                    if tid not in uses:
                        order.append(tid)
                    uses[tid] = {"name": str(part.get("name") or ""),
                                 "input": part.get("input") if isinstance(part.get("input"), dict) else {},
                                 "ts": ts, "cwd": rec.get("cwd"),
                                 "sidechain": bool(rec.get("isSidechain")) or sidechain}
        elif typ == "user":
            tur = rec.get("toolUseResult")
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id"):
                    results[str(part["tool_use_id"])] = {
                        "text": text_of(part.get("content")),
                        "is_error": bool(part.get("is_error")),
                        "ts": ts, "tur": tur if isinstance(tur, dict) else None,
                    }
    for tid in order:
        u = uses[tid]
        name, args = u["name"], u["input"]
        res = results.get(tid)
        mcp = split_mcp_name(name)
        if mcp is not None:
            s.mcp.append((mcp[1], args, u["ts"]))
            continue
        if name in SHELL_TOOLS:
            cmd = args.get("command")
            if not isinstance(cmd, str):
                continue
            ok = None if res is None else not res["is_error"]
            exit_code = None
            out = res["text"] if res else None
            if res and res["tur"]:
                t = res["tur"]
                if isinstance(t.get("stdout"), str) or isinstance(t.get("stderr"), str):
                    out = "\n".join(x for x in (t.get("stdout"), t.get("stderr")) if x)
                if t.get("interrupted"):
                    ok = False
            if res and exit_code is None:
                exit_code = _exit_code(res["text"])
                if exit_code is not None and exit_code != 0:
                    ok = False
            s.commands.append(make_command(
                source="claude", session=s.session, tool_use_id=tid, tool=name,
                command=cmd, ts_start=u["ts"], ts_end=res["ts"] if res else None,
                ok=ok, exit_code=exit_code, output=out, cwd=u["cwd"],
                background=bool(args.get("run_in_background")),
                description=args.get("description") if isinstance(args.get("description"), str) else None,
            ))
            if u["sidechain"]:
                s.commands[-1]["sidechain"] = True
        elif name in WRITE_TOOLS:
            s.commands.append(make_command(
                source="claude", session=s.session, tool_use_id=tid, tool=name,
                command=write_label(name, args), ts_start=u["ts"],
                ts_end=res["ts"] if res else None,
                ok=None if res is None else not res["is_error"],
                output=res["text"] if res else None, cwd=u["cwd"],
                writes=write_paths(name, args),
            ))


def _identity(rec: Dict[str, Any]) -> Any:
    """Conjure's identity line, looked for in the request shape (system
    prompt), queue operations and user messages."""
    for key in ("shape", "content", "message"):
        v = rec.get(key)
        if v is None:
            continue
        text = v if isinstance(v, str) else _flat(v)
        m = IDENTITY_RE.search(text[:200000])
        if m:
            return m.group(1)
    return None


def _flat(v: Any, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return "\n".join(_flat(x, depth + 1) for x in v.values())
    if isinstance(v, list):
        return "\n".join(_flat(x, depth + 1) for x in v[:50])
    return ""


def _exit_code(text: str) -> Any:
    """Claude Code reports a failing shell as ``Exit code N`` on the result's
    first line."""
    if text.startswith("Exit code "):
        head = text[len("Exit code "):].split(None, 1)
        if head and head[0].lstrip("-").isdigit():
            return int(head[0])
    return None
