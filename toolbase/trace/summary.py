"""Read-side summaries of trace directories for ``tb trace list`` / ``show``."""

from __future__ import annotations

from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import AGENTS_FILE, CALLS_FILE, read_jsonl


def is_trace_dir(path: Path) -> bool:
    return (path / CALLS_FILE).is_file() or (path / AGENTS_FILE).is_file()


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _span(calls: List[Dict[str, Any]], agents: List[Dict[str, Any]]):
    stamps = [t for t in (_parse(c.get(k)) for c in calls for k in ("ts_start", "ts_end")) if t]
    stamps += [t for t in (_parse(a.get("ts")) for a in agents) if t]
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _fmt_ts(t: Optional[datetime]) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S UTC") if t else "-"


def summarize(path: Path) -> Dict[str, Any]:
    calls = read_jsonl(path / CALLS_FILE)
    events = read_jsonl(path / AGENTS_FILE)
    start, end = _span(calls, events)
    agent_ids = {e.get("id") for e in events if e.get("event") == "agent_started"}
    agent_ids |= {c.get("agent") for c in calls}
    agent_ids.discard(None)
    return {
        "dir": path, "calls": calls, "events": events,
        "n_calls": len(calls), "n_agents": len(agent_ids),
        "n_failed": sum(1 for c in calls if not c.get("ok", True)),
        "start": start, "end": end,
    }


def list_sessions(root: Path) -> List[Dict[str, Any]]:
    if not root.is_dir():
        return []
    out = [summarize(p) for p in sorted(root.iterdir()) if p.is_dir() and is_trace_dir(p)]
    return out


def format_list(root: Path, sessions: List[Dict[str, Any]]) -> str:
    if not sessions:
        return f"No traces in {root}"
    rows = [("SESSION", "AGENTS", "CALLS", "FAILED", "START", "SPAN")]
    for s in sessions:
        span = (fmt_duration((s["end"] - s["start"]).total_seconds())
                if s["start"] and s["end"] else "-")
        rows.append((s["dir"].name, str(s["n_agents"]), str(s["n_calls"]),
                     str(s["n_failed"]), _fmt_ts(s["start"]), span))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = [f"Traces in {root}"]
    lines += ["  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    return "\n".join(lines)


def format_show(path: Path, max_failures: int = 20) -> str:
    s = summarize(path)
    calls, events = s["calls"], s["events"]
    agents: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for e in events:
        aid = e.get("id")
        if not aid:
            continue
        a = agents.setdefault(aid, {"id": aid})
        if e.get("event") == "agent_started":
            a.update({k: e.get(k) for k in ("label", "parent", "model", "engine", "harness")})
            a["started"] = e.get("ts")
        elif e.get("event") == "agent_ended":
            a["ended"] = e.get("ts")
    for c in calls:
        aid = c.get("agent") or "(none)"
        agents.setdefault(aid, {"id": aid, "label": "(unregistered)"})

    per_agent = Counter(c.get("agent") or "(none)" for c in calls)
    failed_agent = Counter(c.get("agent") or "(none)" for c in calls if not c.get("ok", True))

    span = ""
    if s["start"] and s["end"]:
        span = f", {_fmt_ts(s['start'])} to {_fmt_ts(s['end'])} " \
               f"({fmt_duration((s['end'] - s['start']).total_seconds())})"
    lines = [f"Trace {path}",
             f"{len(agents)} agent(s), {s['n_calls']} call(s), "
             f"{s['n_failed']} failed{span}", "", "Agents"]
    for aid, a in agents.items():
        bits = [b for b in (a.get("label"),) if b]
        extra = ", ".join(f"{k} {a[k]}" for k in ("parent", "model", "engine", "harness") if a.get(k))
        if extra:
            bits.append(f"({extra})")
        if a.get("ended"):
            bits.append("[ended]")
        lines.append(f"  {aid}  {' '.join(bits)}  calls {per_agent.get(aid, 0)}, "
                     f"failed {failed_agent.get(aid, 0)}")

    lines += ["", "Calls by agent and tool"]
    by_tool: "OrderedDict[str, Counter]" = OrderedDict()
    fails: Counter = Counter()
    versions: Dict[tuple, set] = {}
    for c in calls:
        aid = c.get("agent") or "(none)"
        tool = f"{c.get('toolkit')}.{c.get('tool')}"
        by_tool.setdefault(aid, Counter())[tool] += 1
        if not c.get("ok", True):
            fails[(aid, tool)] += 1
        if c.get("version"):
            versions.setdefault((aid, tool), set()).add(str(c["version"]))
    if not by_tool:
        lines.append("  (no calls)")
    for aid, counter in by_tool.items():
        lines.append(f"  {aid}")
        for tool, n in counter.most_common():
            v = versions.get((aid, tool))
            vs = f" @{','.join(sorted(v))}" if v else ""
            f = fails.get((aid, tool), 0)
            lines.append(f"    {tool}{vs}  {n}" + (f" ({f} failed)" if f else ""))

    failures = [c for c in calls if not c.get("ok", True)]
    if failures:
        lines += ["", "Failures"]
        for c in failures[:max_failures]:
            err = " ".join(str(c.get("error") or "").split())
            if len(err) > 160:
                err = err[:157] + "..."
            lines.append(f"  {c.get('id')}  {c.get('agent')}  "
                         f"{c.get('toolkit')}.{c.get('tool')}  {err}")
        if len(failures) > max_failures:
            lines.append(f"  ... {len(failures) - max_failures} more")
    children = sum(1 for c in calls if c.get("parent"))
    if children:
        lines += ["", f"{children} call(s) recorded by toolkits with a parent call"]
    return "\n".join(lines)
