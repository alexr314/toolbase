"""Shared pieces of the transcript readers: the command record, its stable
id, timestamps, and the per-session result a reader returns.

A reader turns one harness's transcripts into ``Session`` objects, one per
agent session it finds. Each holds the shell commands (and file-writing tool
uses) of that session as ``command`` dicts, plus what export needs to
attribute the session to a trace agent: an ``agent`` id when the transcript
says it directly, an identity ``hint`` found in its text, and the toolkit
calls it made (``mcp``), matched against ``calls.jsonl`` when nothing else
names the agent.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..core import RESULT_HEAD_CHARS

# Conjure's identity block, in every agent's system prompt or first message:
#   - Address id:  `ag-wzw6pxndboggo`
IDENTITY_RE = re.compile(r"Address id:\s*`?(ag-[A-Za-z0-9_-]+)`?")
AGENT_ID_RE = re.compile(r"^ag-[A-Za-z0-9_-]+$")

# Tools that write a named file (exact, not inferred).
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "shell", "exec_command", "local_shell", "container.exec",
               "unified_exec", "shell_command"}


@dataclass
class Session:
    """One agent session's commands, as read from one transcript."""
    source: str                       # "claude" | "codex" | "conjure"
    session: str                      # transcript's own session id
    path: str                         # transcript file (for warnings only)
    agent: Optional[str] = None       # trace agent id, when the transcript says it
    hint: Optional[str] = None        # an agent id found in the transcript text
    commands: List[Dict[str, Any]] = field(default_factory=list)
    # toolkit calls seen in the transcript: (tool name, args, epoch ts)
    mcp: List[Tuple[str, Dict[str, Any], Optional[float]]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def command_id(tool_use_id: str) -> str:
    """``cmd-`` and 10 hex digits of the tool-use id: stable across exports."""
    return "cmd-" + hashlib.sha256(str(tool_use_id).encode("utf-8")).hexdigest()[:10]


def iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return (datetime.fromtimestamp(epoch, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def epoch(ts: Any) -> Optional[float]:
    """Seconds since the epoch from an ISO string, epoch seconds or ms."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        return v / 1000.0 if v > 1e11 else v
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return epoch(float(ts))
            except ValueError:
                return None
    return None


def digest(text: Any) -> Dict[str, Any]:
    s = "" if text is None else (text if isinstance(text, str) else str(text))
    return {"sha256": hashlib.sha256(s.encode("utf-8", "replace")).hexdigest(),
            "head": s[:RESULT_HEAD_CHARS]}


def text_of(content: Any) -> str:
    """A tool result's text: a string, or the text parts of a content list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if isinstance(c.get("text"), str):
                    parts.append(c["text"])
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    return json.dumps(content, default=str)[:RESULT_HEAD_CHARS * 4]


def shell_text(cmd: Any) -> str:
    """The command line from a string or an argv list (``["bash", "-lc", s]``
    gives ``s``)."""
    if isinstance(cmd, str):
        return cmd
    if isinstance(cmd, (list, tuple)):
        parts = [str(c) for c in cmd]
        if len(parts) >= 3 and parts[-2] in ("-c", "-lc", "-ic") and \
                Path(parts[0]).name in ("bash", "sh", "zsh", "dash"):
            return parts[-1]
        return " ".join(parts)
    return "" if cmd is None else str(cmd)


def make_command(
    *,
    source: str,
    session: str,
    tool_use_id: str,
    tool: str,
    command: str,
    ts_start: Optional[float],
    ts_end: Optional[float] = None,
    ok: Optional[bool] = None,
    exit_code: Optional[int] = None,
    output: Any = None,
    cwd: Optional[str] = None,
    agent: Optional[str] = None,
    background: bool = False,
    writes: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "id": command_id(tool_use_id), "kind": "command", "source": source,
        "session": session, "tool_use_id": tool_use_id, "tool": tool,
        "agent": agent, "command": command, "cwd": cwd,
        "ts_start": iso(ts_start), "ts_end": iso(ts_end),
        "ok": ok, "exit_code": exit_code,
        "output": digest(output) if output is not None else None,
        "background": bool(background),
    }
    if writes:
        rec["writes"] = list(writes)
    if description:
        rec["description"] = description
    return rec


def iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    """Records of a JSONL file, skipping blank and torn lines."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def split_mcp_name(name: str) -> Optional[Tuple[Optional[str], str]]:
    """``mcp__toolbase__heptapod__X`` -> (heptapod, X); ``heptapod__X`` ->
    (heptapod, X); anything else -> None."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 4 and parts[1] == "toolbase":
            return parts[2], "__".join(parts[3:])
        if len(parts) >= 3:
            return None, parts[-1]
        return None
    if "__" in name:
        tk, tool = name.split("__", 1)
        if tk and tool and tool[:1].isupper():
            return tk, tool
    return None


def write_paths(tool: str, args: Dict[str, Any]) -> List[str]:
    """Files named by a file-writing tool use."""
    out = []
    for k in ("file_path", "notebook_path", "path"):
        v = args.get(k)
        if isinstance(v, str) and v:
            out.append(v)
    return out


def write_label(tool: str, args: Dict[str, Any]) -> str:
    paths = write_paths(tool, args)
    return f"{tool} {paths[0]}" if paths else tool
