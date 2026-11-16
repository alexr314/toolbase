"""Readers of harness transcripts, for ``tb trace export --commands``.

Shell commands an agent runs in its own shell never pass through toolbase;
these readers recover them (and the file-writing tool uses) from the
harness's own logs at export time. Nothing here is imported by ``serve``.

``--commands`` takes ``<kind>:<path>``:

``claude:<path>``
    Claude Code session transcripts (a ``.jsonl`` file or a
    ``~/.claude/projects/<escaped cwd>/`` directory). See ``claude``.
``codex:<path>``
    Codex rollouts (a ``rollout-*.jsonl``, a ``sessions/`` tree, a Codex home,
    or Conjure's ``codex_homes/`` holding one home per agent). See ``codex``.
``conjure:<path>``
    Conjure's per-agent logs (a session directory with ``agents/``, or a run
    directory). Exact agent ids; preferred when available. See ``conjure``.

``collect`` reads every spec, attributes each session to a trace agent and
merges duplicates (the same tool use seen by two readers).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .common import SHELL_TOOLS, Session, command_id, epoch

KINDS = ("claude", "codex", "conjure")
# On a duplicate tool use, fields are taken from the first reader that has them.
_PREFERENCE = {"conjure": 0, "claude": 1, "codex": 2}


def parse_spec(spec: str) -> Tuple[str, Path]:
    kind, sep, path = spec.partition(":")
    if not sep or kind not in KINDS or not path:
        raise ValueError(f"--commands expects <kind>:<path> with kind one of "
                         f"{', '.join(KINDS)}; got {spec!r}")
    return kind, Path(path).expanduser()


def read_spec(spec: str) -> List[Session]:
    kind, path = parse_spec(spec)
    if not path.exists():
        raise FileNotFoundError(f"--commands {kind}: {path} does not exist")
    if kind == "claude":
        from . import claude as mod
    elif kind == "codex":
        from . import codex as mod  # type: ignore[no-redef]
    else:
        from . import conjure as mod  # type: ignore[no-redef]
    return mod.read(path)


def _args_match(seen: Dict[str, Any], recorded: Dict[str, Any]) -> bool:
    keys = [k for k, v in seen.items() if isinstance(v, (str, int, float, bool))]
    if not keys:
        return False
    for k in keys:
        if k not in recorded:
            return False
        a, b = seen[k], recorded[k]
        if isinstance(a, str) and isinstance(b, str):
            # the trace cuts long strings and redacts secrets
            if a != b and not (len(b) < len(a) and a.startswith(b[:200])) and "REDACTED" not in b:
                return False
        elif a != b:
            return False
    return True


def attribute(sessions: List[Session], agents: Dict[str, Dict[str, Any]],
              calls: List[Dict[str, Any]]) -> Tuple[Dict[str, str], List[str]]:
    """``{session key: agent id}`` and how each was decided (warnings for the
    weak cases). Order: the reader's own agent id, Conjure's identity line,
    toolkit calls matched against the trace, the trace's only agent; else a
    placeholder agent for the session."""
    by_tool: Dict[str, List[Dict[str, Any]]] = {}
    for c in calls:
        by_tool.setdefault(str(c.get("tool")), []).append(c)
    out: Dict[str, str] = {}
    notes: List[str] = []
    real_agents = [a for a in agents if not agents[a].get("placeholder")]
    for s in sessions:
        key = f"{s.source}:{s.session}"
        how = None
        aid = None
        if s.agent:
            aid, how = s.agent, "reader"
        elif s.hint:
            aid, how = s.hint, "identity line"
        else:
            votes: Dict[str, int] = {}
            for tool, args, ts in s.mcp:
                for c in by_tool.get(tool, []):
                    cts = epoch(c.get("ts_start"))
                    close = ts is not None and cts is not None and abs(ts - cts) < 5.0
                    if close or _args_match(args, c.get("args") or {}):
                        if c.get("agent"):
                            votes[c["agent"]] = votes.get(c["agent"], 0) + 1
                        break
            if votes:
                aid = max(votes, key=lambda k: votes[k])
                how = f"toolkit calls ({votes[aid]} matched)"
                if len(votes) > 1:
                    notes.append(f"{key}: toolkit calls match several agents {votes}; chose {aid}")
            elif len(real_agents) == 1:
                aid, how = real_agents[0], "only agent"
        if aid is None:
            aid = f"ag-{s.source}-{s.session[:8]}"
            how = "unattributed"
            notes.append(f"{key}: no agent found ({len(s.commands)} commands); "
                         f"placed under placeholder {aid}")
        out[key] = aid
        s.attributed_by = how  # type: ignore[attr-defined]
    return out, notes


def collect(specs: Iterable[str], agents: Dict[str, Dict[str, Any]],
            calls: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], List[Session]]:
    """Read every spec; return the merged command records (each with its
    ``agent`` and ``attributed_by``), notes, and the sessions read."""
    sessions: List[Session] = []
    notes: List[str] = []
    for spec in specs:
        got = read_spec(spec)
        if not got:
            notes.append(f"--commands {spec}: no transcripts with commands found")
        sessions += got
    owner, more = attribute(sessions, agents, calls)
    notes += more
    merged: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        aid = owner[f"{s.source}:{s.session}"]
        for cmd in s.commands:
            cmd = dict(cmd)
            cmd["agent"] = cmd.get("agent") or aid
            cmd["attributed_by"] = getattr(s, "attributed_by", None)
            prev = merged.get(cmd["id"])
            if prev is None:
                merged[cmd["id"]] = cmd
                continue
            first, second = sorted((prev, cmd), key=lambda c: _PREFERENCE.get(c["source"], 9))
            both = dict(second)
            both.update({k: v for k, v in first.items() if v is not None})
            both["sources"] = sorted({prev["source"], cmd["source"]}
                                     | set(prev.get("sources", [])))
            merged[cmd["id"]] = both
    # A Codex agent logged by Conjure and by its rollout: the rollout has the
    # real commands (exit codes, cwd, one per exec), Conjure only its own
    # per-turn view; keep the rollout's.
    codex_agents = {c["agent"] for c in merged.values() if c["source"] == "codex"}
    out = [c for c in merged.values()
           if not (c["source"] == "conjure" and c["agent"] in codex_agents
                   and c["tool"] in SHELL_TOOLS and not str(c["tool_use_id"]).startswith("toolu_"))]
    out.sort(key=lambda c: (c.get("ts_start") or "", c["id"]))
    return out, notes, sessions


__all__ = ["KINDS", "Session", "attribute", "collect", "command_id", "parse_spec", "read_spec"]
