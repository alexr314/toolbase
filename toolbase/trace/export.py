"""``tb trace export``: a finished (or crashed, or killed) run's trace
directory as a self-contained, shareable bundle.

Offline: reads ``agents.jsonl``, ``calls.jsonl`` and, when present,
``messages.jsonl`` from the trace directory; optionally hashes a sandbox and
reads shell commands from harness transcripts (``readers``). Writes::

    <bundle>/
      index.html    the viewer, data inlined; opens from disk
      trace.json    nodes and edges (schema toolbase-trace-bundle/1)
      prov.json     the same graph as W3C PROV-JSON
      files.json    every file: path, size, hash, the node that generated it
      files/        copies of small files, at their sandbox-relative paths

Imported only by ``tb trace export``; nothing on the serve path loads it.
See ``docs/TRACE.md`` for the formats and the PROV mapping.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import shutil
import stat as statmod
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .core import AGENTS_FILE, CALLS_FILE, _arg_strings

BUNDLE_SCHEMA = "toolbase-trace-bundle/1"
FILES_SCHEMA = "toolbase-trace-files/1"
MESSAGES_FILE = "messages.jsonl"
DEFAULT_COPY_LIMIT = 1024 * 1024          # bytes per copied file
DEFAULT_COPY_TOTAL = 256 * 1024 * 1024    # bytes copied in all
COPY_MODES = ("graph", "all", "none")
PROV_NS = "https://toolbase.dev/ns/trace#"

# A file whose mtime falls in [start - BEFORE, end + AFTER] of an activity
# was written during it. ``AFTER`` covers the record being written after the
# tool returns; ``BEFORE`` only clock granularity.
SLACK_BEFORE = 0.5
SLACK_AFTER = 1.0
MAX_NAMED_PER_COMMAND = 40
SCRIPT_EXTS = {".py", ".sh", ".bash", ".zsh", ".jl", ".r", ".R", ".m", ".wl", ".wls",
               ".C", ".cc", ".cpp", ".cxx", ".pl", ".rb", ".js", ".mjs", ".mg5",
               ".cmnd", ".ipynb", ".f", ".f90", ".tex", ".mac"}
_TOKEN_RE = re.compile(r"[^\s\"'`<>()\[\]{},;|&=]+")
# Write targets in a command: shell redirections, tee, -o/--output, and the
# usual Python writes in an inline script.
_TARGET_RES = [
    re.compile(r"(?:^|[\s;&|])(?:\d?>>?|&>)\s*([^\s;&|<>()'\"]+)"),
    re.compile(r"\btee\s+(?:-a\s+)?([^\s;&|<>()'\"]+)"),
    re.compile(r"(?:\s-o|--output|--out)[=\s]+([^\s;&|<>()'\"]+)"),
    re.compile(r"open\(\s*[rf]?['\"]([^'\"]+)['\"]\s*,\s*['\"][wax]"),
    re.compile(r"(?:savefig|to_csv|to_json|savetxt|save|dump_json|write_json)\(\s*[rf]?['\"]([^'\"]+)['\"]"),
    re.compile(r"Path\(\s*[rf]?['\"]([^'\"]+)['\"]\s*\)\.write_(?:text|bytes)"),
]
_CD_RE = re.compile(r"(?:^|[;&|(]\s*|\bthen\s+|\bdo\s+)cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|)]+)")


class ExportError(RuntimeError):
    pass


# ── reading ────────────────────────────────────────────────────────────


def _read_counting(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Records and the number of torn or non-object lines skipped."""
    out: List[Dict[str, Any]] = []
    bad = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
                else:
                    bad += 1
    except FileNotFoundError:
        pass
    return out, bad


def _epoch(ts: Any) -> Optional[float]:
    from .readers.common import epoch
    return epoch(ts)


def _iso(t: Optional[float]) -> Optional[str]:
    from .readers.common import iso
    return iso(t)


def parse_size(text: str) -> int:
    """``1MB``, ``500k``, ``2G``, ``1048576`` -> bytes (binary multiples)."""
    s = str(text).strip().lower().replace("ib", "").rstrip("b")
    mult = 1
    if s and s[-1] in "kmgt":
        mult = 1024 ** ("kmgt".index(s[-1]) + 1)
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        raise ValueError(f"not a size: {text!r} (try 1MB, 500k, 0)")


# ── the sandbox ────────────────────────────────────────────────────────


def _sha256(path: str) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def scan_sandbox(root: Path, *, hash_limit: Optional[int] = None,
                 known: Optional[Dict[Tuple[str, int, str], str]] = None,
                 progress: Optional[Callable[[str], None]] = None) -> Dict[str, Dict[str, Any]]:
    """``{relative path: {bytes, mtime (epoch), sha256}}`` for every regular
    file under ``root`` (symlinks are not followed). Hashes reuse ``known``
    (path, bytes, mtime ISO) -> sha256 from the call records where they match;
    files over ``hash_limit`` are listed without a hash."""
    files: Dict[str, Dict[str, Any]] = {}
    root_s = str(root)
    for dirpath, dirnames, filenames in os.walk(root_s, followlinks=False):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not statmod.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(full, root_s)
            files[rel] = {"bytes": st.st_size, "mtime": st.st_mtime, "sha256": None}
    todo = []
    for rel, e in files.items():
        if hash_limit is not None and e["bytes"] > hash_limit:
            continue
        hit = (known or {}).get((rel, e["bytes"], _iso(e["mtime"])))
        if hit:
            e["sha256"] = hit
        else:
            todo.append(rel)
    if progress and todo:
        total = sum(files[r]["bytes"] for r in todo)
        progress(f"hashing {len(todo)} sandbox files ({total / 1e6:.0f} MB)")
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 2))) as ex:
        for rel, digest in zip(todo, ex.map(lambda r: _sha256(os.path.join(root_s, r)), todo)):
            files[rel]["sha256"] = digest
    return files


# ── the graph ──────────────────────────────────────────────────────────


class Graph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self._edge_keys: Set[Tuple[str, str, str]] = set()

    def add(self, node: Dict[str, Any]) -> Dict[str, Any]:
        cur = self.nodes.get(node["id"])
        if cur is None:
            self.nodes[node["id"]] = node
            return node
        for k, v in node.items():
            if cur.get(k) is None and v is not None:
                cur[k] = v
        return cur

    def edge(self, kind: str, src: str, dst: str, **attrs: Any) -> None:
        key = (kind, src, dst)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        e = {"kind": kind, "from": src, "to": dst}
        e.update({k: v for k, v in attrs.items() if v is not None})
        self.edges.append(e)


def file_node_id(sha256: Optional[str], path: str, bytes_: Any = None, mtime: Any = None) -> str:
    """``f-`` and 12 hex digits of the content hash; a file without a hash
    gets ``f-p`` and 11 hex digits of its path, size and mtime."""
    if sha256:
        return "f-" + sha256[:12]
    key = f"{path}|{bytes_}|{mtime}"
    return "f-p" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:11]


def _ancestors(rel: str) -> List[str]:
    """``a/b/c.txt`` -> ``["a/b", "a"]`` (deepest first)."""
    parts = rel.split("/")[:-1]
    return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]


class _Resolver:
    """Map path-like strings from commands and arguments onto the sandbox."""

    def __init__(self, sandbox: Optional[Path], files: Dict[str, Dict[str, Any]]):
        self.root = os.path.realpath(sandbox) if sandbox else None
        self.raw_root = str(sandbox) if sandbox else None
        self.files = files
        dirs: Set[str] = set()
        for rel in files:
            dirs.update(_ancestors(rel))
        self.dirs = dirs

    def rel(self, raw: str, cwds: Iterable[Optional[str]] = ()) -> Optional[str]:
        """Sandbox-relative path of ``raw`` if it lands inside the sandbox."""
        if not self.root or not raw or len(raw) > 4096 or "\0" in raw:
            return None
        raw = raw.strip()
        if raw.startswith("file://"):
            raw = raw[7:]
        raw = os.path.expanduser(raw)
        cands = []
        if os.path.isabs(raw):
            cands.append(raw)
        else:
            for c in cwds:
                if c:
                    cands.append(os.path.join(c, raw))
            cands.append(os.path.join(self.root, raw))
        for c in cands:
            p = os.path.normpath(c)
            for root in {self.root, self.raw_root}:
                if root and (p == root or p.startswith(root.rstrip(os.sep) + os.sep)):
                    r = os.path.relpath(p, root)
                    if r == ".":
                        return "."
                    if r in self.files or r in self.dirs:
                        return r
        return None


def _command_mentions(cmd: Dict[str, Any], res: _Resolver
                      ) -> Tuple[List[str], Set[str], Optional[str], Set[str]]:
    """Files and directories a command names (sandbox-relative), its script
    (the first named file with a script extension), and the files it names
    as write targets (redirections, ``tee``, ``-o``, inline ``open(…, "w")``)."""
    text = cmd.get("command") or ""
    cwd = cmd.get("cwd")
    cwd_abs = None
    if cwd:
        cwd_abs = cwd if os.path.isabs(cwd) else os.path.join(res.root or "", cwd)
    cwds: List[Optional[str]] = [cwd_abs]
    for m in _CD_RE.finditer(text):
        target = m.group(1).strip("\"'")
        base = cwd_abs or res.root
        t = os.path.expanduser(target)
        cwds.insert(0, t if os.path.isabs(t) else os.path.join(base or "", t))
    files: List[str] = []
    dirs: Set[str] = set()
    script = None
    seen = set()
    for c in cwds:
        r = res.rel(c) if c else None
        if r and r != "." and r in res.dirs:
            dirs.add(r)
    for w in cmd.get("writes") or []:
        r = res.rel(w, cwds)
        if r and r in res.files and r not in seen:
            seen.add(r)
            files.append(r)
    for m in _TOKEN_RE.finditer(text[:200000]):
        tok = m.group(0).rstrip(".:,!?")
        if len(tok) < 2 or "*" in tok or "$" in tok:
            continue
        if not ("/" in tok or re.search(r"\w\.\w{1,10}$", tok)):
            continue
        r = res.rel(tok, cwds)
        if not r or r == ".":
            continue
        if r in res.files:
            if r not in seen:
                seen.add(r)
                files.append(r)
                if script is None and os.path.splitext(r)[1] in SCRIPT_EXTS:
                    script = r
        elif r in res.dirs:
            dirs.add(r)
        if len(files) >= MAX_NAMED_PER_COMMAND:
            break
    targets: Set[str] = set()
    for rx in _TARGET_RES:
        for m in rx.finditer(text[:200000]):
            r = res.rel(m.group(1).rstrip(".:,"), cwds)
            if r and r in res.files:
                targets.add(r)
                if r not in seen:
                    seen.add(r)
                    files.append(r)
    return files, dirs, script, targets


def _redactor(pairs: List[Tuple[str, str]]) -> Callable[[Any], Any]:
    pairs = [(a, b) for a, b in pairs if a and len(a) > 1]
    pairs.sort(key=lambda p: -len(p[0]))

    def red(v: Any) -> Any:
        if isinstance(v, str):
            for a, b in pairs:
                if a in v:
                    v = v.replace(a, b)
            return v
        if isinstance(v, dict):
            return {k: red(x) for k, x in v.items()}
        if isinstance(v, list):
            return [red(x) for x in v]
        return v
    return red


# ── export ─────────────────────────────────────────────────────────────


def export(
    trace_dir: Path,
    out: Path,
    *,
    sandbox: Optional[Path] = None,
    commands: Iterable[str] = (),
    copy_limit: int = DEFAULT_COPY_LIMIT,
    copy_total: int = DEFAULT_COPY_TOTAL,
    copy_mode: str = "graph",
    hash_limit: Optional[int] = None,
    redact: bool = True,
    force: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Write the bundle; return a summary (counts, sizes, warnings)."""
    t_begin = time.time()
    say = progress or (lambda _m: None)
    trace_dir = Path(trace_dir).expanduser().absolute()
    out = Path(out).expanduser().absolute()
    if copy_mode not in COPY_MODES:
        raise ExportError(f"copy mode must be one of {', '.join(COPY_MODES)}")
    if not ((trace_dir / CALLS_FILE).is_file() or (trace_dir / AGENTS_FILE).is_file()):
        raise ExportError(f"{trace_dir} is not a trace directory (no {CALLS_FILE} or {AGENTS_FILE})")
    if sandbox is not None:
        sandbox = Path(sandbox).expanduser().absolute()
        if not sandbox.is_dir():
            raise ExportError(f"--sandbox {sandbox} is not a directory")
    if out.exists() and any(out.iterdir()):
        if not force:
            raise ExportError(f"{out} exists and is not empty (use --force to replace the bundle there)")
        if not (out / "trace.json").is_file():
            raise ExportError(f"{out} is not a bundle (no trace.json); refusing to overwrite it")
        for name in ("index.html", "trace.json", "prov.json", "files.json"):
            (out / name).unlink(missing_ok=True)
        shutil.rmtree(out / "files", ignore_errors=True)
    warnings: List[str] = []

    # ── records ──
    agent_events, bad_a = _read_counting(trace_dir / AGENTS_FILE)
    calls, bad_c = _read_counting(trace_dir / CALLS_FILE)
    messages, bad_m = _read_counting(trace_dir / MESSAGES_FILE)
    for n, name in ((bad_a, AGENTS_FILE), (bad_c, CALLS_FILE), (bad_m, MESSAGES_FILE)):
        if n:
            warnings.append(f"{name}: {n} torn or unreadable line(s) skipped "
                            "(a writer killed mid-line)")
    calls = [c for c in calls if c.get("id")]

    g = Graph()
    agents: Dict[str, Dict[str, Any]] = {}

    def agent(aid: str, **fields: Any) -> Dict[str, Any]:
        a = agents.get(aid)
        if a is None:
            a = agents[aid] = g.add({"id": aid, "kind": "agent", "label": None, "parent": None,
                                     "model": None, "engine": None, "harness": None,
                                     "ts_start": None, "ts_end": None})
        for k, v in fields.items():
            if v is not None and a.get(k) is None:
                a[k] = v
        return a

    for e in agent_events:
        aid = e.get("id")
        if not aid:
            continue
        if e.get("event") == "agent_started":
            agent(aid, label=e.get("label"), parent=e.get("parent"), model=e.get("model"),
                  engine=e.get("engine"), harness=e.get("harness"), ts_start=e.get("ts"))
        elif e.get("event") == "agent_ended":
            agent(aid)["ts_end"] = e.get("ts")

    # ── messages: spawns (briefs), sends, terminations ──
    n_msg = 0
    for i, m in enumerate(messages):
        ev, src, dst, ts = m.get("event"), m.get("from"), m.get("to"), m.get("ts")
        if ev == "spawn" and dst:
            a = agent(str(dst), label=m.get("label"), parent=src, model=m.get("model"),
                      engine=m.get("engine"), ts_start=ts)
            mid = f"m-spawn-{dst}"
            g.add({"id": mid, "kind": "message", "event": "spawn", "from": src, "to": dst, "ts": ts,
                   "label": m.get("label"), "role_prompt": m.get("role_prompt"),
                   "initial_message": m.get("initial_message"), "model": m.get("model"),
                   "engine": m.get("engine"), "toolkits": m.get("toolkits")})
            a["spawn"] = mid
            if src:
                agent(str(src))
                g.edge("spawned", str(src), str(dst), ts=ts)
                g.edge("sent", str(src), mid, ts=ts)
            g.edge("received", mid, str(dst), ts=ts)
            n_msg += 1
        elif ev == "send":
            mid = "m-" + str(m.get("msg_id") or hashlib.sha256(
                json.dumps(m, sort_keys=True, default=str).encode()).hexdigest()[:10]).removeprefix("msg-")
            g.add({"id": mid, "kind": "message", "event": "send", "from": src, "to": dst, "ts": ts,
                   "body": m.get("body"), "msg_id": m.get("msg_id"), "thread_id": m.get("thread_id")})
            for who in (src, dst):
                if who:
                    agent(str(who))
            if src:
                g.edge("sent", str(src), mid, ts=ts)
            if dst:
                g.edge("received", mid, str(dst), ts=ts)
            n_msg += 1
        elif ev == "terminate" and dst:
            a = agent(str(dst))
            a["terminated"] = {"ts": ts, "by": src, "cascade": m.get("cascade")}
            if a.get("ts_end") is None:
                a["ts_end"] = ts

    # ── calls ──
    for c in calls:
        aid = c.get("agent")
        if aid:
            agent(str(aid))
    known_hashes: Dict[Tuple[str, int, str], str] = {}
    for c in calls:
        for f in c.get("files") or []:
            if f.get("sha256") and f.get("path") and f.get("bytes") is not None and f.get("mtime"):
                known_hashes[(f["path"], f["bytes"], f["mtime"])] = f["sha256"]

    # ── sandbox ──
    sb_files: Dict[str, Dict[str, Any]] = {}
    if sandbox is not None:
        say(f"scanning {sandbox}")
        sb_files = scan_sandbox(sandbox, hash_limit=hash_limit, known=known_hashes, progress=say)
    res = _Resolver(sandbox, sb_files)

    # ── commands from transcripts ──
    cmds: List[Dict[str, Any]] = []
    sessions: List[Any] = []
    if commands:
        from .readers import collect
        say("reading transcripts")
        cmds, notes, sessions = collect(list(commands), agents, calls)
        warnings += notes
        for cmd in cmds:
            a = agent(cmd["agent"])
            if a.get("label") is None and cmd["agent"].startswith(("ag-claude-", "ag-codex-", "ag-conjure-")):
                a.update({"label": f"{cmd['source']} session {cmd['session'][:8]}",
                          "harness": cmd["source"], "placeholder": True})

    for aid, a in agents.items():
        if aid.startswith("@"):
            a.setdefault("external", True)
            if a.get("label") is None:
                a["label"] = aid.lstrip("@")
        elif a.get("label") is None and not any(e.get("id") == aid for e in agent_events):
            a["unregistered"] = True

    # ── file versions ──
    files_by_id: Dict[str, Dict[str, Any]] = {}

    def file_node(path: str, sha: Optional[str], bytes_: Any, mtime_iso: Optional[str]) -> str:
        # A call's unhashed entry that matches the sandbox file takes its hash.
        if not sha and path in sb_files:
            e = sb_files[path]
            if e["sha256"] and e["bytes"] == bytes_ and _iso(e["mtime"]) == mtime_iso:
                sha = e["sha256"]
        fid = file_node_id(sha, path, bytes_, mtime_iso)
        n = files_by_id.get(fid)
        if n is None:
            n = files_by_id[fid] = g.add({"id": fid, "kind": "file", "sha256": sha,
                                          "bytes": bytes_, "paths": [], "mtime": mtime_iso})
        if path not in n["paths"]:
            n["paths"].append(path)
        return fid

    def sandbox_node(rel: str) -> str:
        e = sb_files[rel]
        return file_node(rel, e["sha256"], e["bytes"], _iso(e["mtime"]))

    for c in calls:
        cid = str(c["id"])
        t0, t1 = _epoch(c.get("ts_start")), _epoch(c.get("ts_end"))
        node = {"id": cid, "kind": "call", "agent": c.get("agent"), "toolkit": c.get("toolkit"),
                "version": c.get("version"), "tool": c.get("tool"), "ts_start": c.get("ts_start"),
                "ts_end": c.get("ts_end"), "ok": c.get("ok", True), "error": c.get("error"),
                "args": c.get("args"), "result": c.get("result"), "parent": c.get("parent")}
        g.add(node)
        if c.get("agent"):
            g.edge("called", str(c["agent"]), cid, ts=c.get("ts_start"))
        if c.get("parent"):
            g.edge("child_of", cid, str(c["parent"]))
        for f in c.get("files") or []:
            path = f.get("path")
            if not path:
                continue
            fid = file_node(path, f.get("sha256"), f.get("bytes"), f.get("mtime"))
            mt = _epoch(f.get("mtime"))
            gen = (mt is not None and t0 is not None and t1 is not None
                   and t0 - SLACK_BEFORE <= mt <= t1 + SLACK_AFTER)
            if gen:
                g.edge("generated", cid, fid, basis="recorded")
            else:
                g.edge("used", cid, fid, basis="recorded")
    for c in calls:
        if c.get("parent") and str(c["parent"]) not in g.nodes:
            warnings.append(f"call {c['id']}: parent {c['parent']} has no record")

    # ── command nodes, named files, scripts ──
    cmd_info: List[Tuple[Dict[str, Any], float, float, Set[str], Set[str]]] = []
    for cmd in cmds:
        node = {k: v for k, v in cmd.items() if k != "kind"}
        node["kind"] = "command"
        g.add(node)
        g.edge("called", cmd["agent"], cmd["id"], ts=cmd.get("ts_start"))
        t0, t1 = _epoch(cmd.get("ts_start")), _epoch(cmd.get("ts_end"))
        if sandbox is None or t0 is None:
            continue
        named, dirs, script, targets = _command_mentions(cmd, res)
        end = t1 if t1 is not None else t0
        writes = set()
        for w in cmd.get("writes") or []:
            r = res.rel(w, [cmd.get("cwd")])
            if r:
                writes.add(r)
        unmatched = []
        for rel in named:
            e = sb_files[rel]
            mt = e["mtime"]
            if rel in writes:
                if t0 - SLACK_BEFORE <= mt <= end + SLACK_AFTER + (30 if t1 is None else 0):
                    fid = sandbox_node(rel)
                    g.edge("generated", cmd["id"], fid, basis="named")
                else:
                    unmatched.append(rel)
                continue
            if mt < t0 - SLACK_BEFORE:
                g.edge("used", cmd["id"], sandbox_node(rel), basis="named", inferred=True)
            elif mt <= end + SLACK_AFTER:
                # Named and written in its window: a candidate generator. The
                # inference below picks among all candidates (two commands
                # issued together can both name it).
                sandbox_node(rel)
        if unmatched:
            node["writes_unmatched"] = unmatched
        if script:
            e = sb_files[script]
            node["script"] = {"path": script, "sha256": e["sha256"], "bytes": e["bytes"],
                              "changed_since": e["mtime"] > t0 + SLACK_AFTER}
        cmd_info.append((node, t0, t1 if t1 is not None else t0, dirs, set(named), targets))

    # ── inferred generation for sandbox files nothing generated directly ──
    # Candidates: commands whose window holds the file's mtime and that name
    # the file or one of its directories; calls whose window holds it and
    # whose arguments name one of its directories. The deepest named
    # directory wins, then the shortest window, then the latest start.
    acts: List[Tuple[float, float, str, Set[str], Set[str], Set[str]]] = []
    for node, t0, t1, dirs, named, targets in cmd_info:
        acts.append((t0, t1, node["id"], dirs, named, targets))
    if sandbox is not None:
        for c in calls:
            t0, t1 = _epoch(c.get("ts_start")), _epoch(c.get("ts_end"))
            if t0 is None or t1 is None:
                continue
            strs: List[str] = []
            _arg_strings(c.get("args") or {}, strs)
            dirs = set()
            for s in strs:
                r = res.rel(s)
                if r and r != "." and r in res.dirs:
                    dirs.add(r)
            if dirs:
                acts.append((t0, t1, str(c["id"]), dirs, set(), set()))
    acts.sort(key=lambda a: a[0])
    starts = [a[0] for a in acts]
    max_dur = max((a[1] - a[0] for a in acts), default=0.0)
    gen_by_path: Dict[str, Tuple[str, str]] = {}  # rel -> (generator id, basis)
    cand_by_path: Dict[str, List[str]] = {}
    def current_id(rel: str) -> str:
        e = sb_files[rel]
        return file_node_id(e["sha256"], rel, e["bytes"], _iso(e["mtime"]))

    # Sandbox paths whose current version some activity generated.
    direct_paths: Dict[str, str] = {}
    for e in g.edges:
        if e["kind"] == "generated":
            for p in files_by_id[e["to"]]["paths"]:
                if p in sb_files and current_id(p) == e["to"]:
                    direct_paths.setdefault(p, e["from"])
    if acts:
        say(f"matching {len(sb_files)} files to {len(acts)} activities")
        for rel, fe in sb_files.items():
            if rel in direct_paths:
                continue
            mt = fe["mtime"]
            hi = bisect.bisect_right(starts, mt + SLACK_BEFORE)
            best = None
            window_only = []
            anc = None
            for i in range(hi - 1, -1, -1):
                t0, t1, aid, dirs, named, targets = acts[i]
                if t0 < mt - max_dur - SLACK_AFTER:
                    break
                if mt > t1 + SLACK_AFTER:
                    continue
                if anc is None:
                    anc = _ancestors(rel)
                depth = 0
                if rel in targets:
                    depth = 2000
                elif rel in named:
                    depth = 1000
                else:
                    for j, a in enumerate(anc):
                        if a in dirs:
                            depth = len(anc) - j
                            break
                if depth == 0:
                    window_only.append(aid)
                    continue
                key = (depth, -(t1 - t0), t0)
                if best is None or key > best[0]:
                    best = (key, aid, depth)
            if best is not None:
                gen_by_path[rel] = (best[1], {2000: "target+mtime", 1000: "named+mtime"}.get(
                    best[2], "dir+mtime"))
            elif window_only:
                cand_by_path[rel] = window_only[:5]

    # Inferred edges for files already in the graph (named by some call or
    # command): only those versions are graph nodes; the rest stay in files.json.
    for rel, (aid, basis) in gen_by_path.items():
        e = sb_files[rel]
        fid = file_node_id(e["sha256"], rel, e["bytes"], _iso(e["mtime"]))
        if fid in files_by_id:
            g.edge("generated", aid, fid, basis=basis, inferred=True)

    # ── files.json ──
    edges_to: Dict[str, List[Dict[str, Any]]] = {}
    edges_from: Dict[str, List[Dict[str, Any]]] = {}
    for e in g.edges:
        edges_to.setdefault(e["to"], []).append(e)
        edges_from.setdefault(e["from"], []).append(e)

    def gens(fid: str) -> List[Dict[str, Any]]:
        return [e for e in edges_to.get(fid, []) if e["kind"] == "generated"]

    t_trace0 = min((t for t in (_epoch(c.get("ts_start")) for c in calls) if t), default=None)
    for a in agents.values():
        ta = _epoch(a.get("ts_start"))
        if ta is not None and (t_trace0 is None or ta < t_trace0):
            t_trace0 = ta

    rows: List[Dict[str, Any]] = []
    present_ids: Set[str] = set()
    for rel in sorted(sb_files):
        e = sb_files[rel]
        fid = current_id(rel)
        in_graph = fid in files_by_id
        if in_graph:
            present_ids.add(fid)
        row: Dict[str, Any] = {"path": rel, "bytes": e["bytes"], "mtime": _iso(e["mtime"]),
                               "sha256": e["sha256"], "node": fid if in_graph else None,
                               "present": True}
        gl = gens(fid) if in_graph else []
        if gl:
            row["generated_by"] = [x["from"] for x in gl]
            row["basis"] = gl[0].get("basis")
            row["origin"] = "inferred" if all(x.get("inferred") for x in gl) else "recorded"
        elif rel in gen_by_path:
            row["generated_by"] = [gen_by_path[rel][0]]
            row["basis"] = gen_by_path[rel][1]
            row["origin"] = "inferred"
        else:
            row["origin"] = "unknown"
            if rel in cand_by_path:
                row["candidates"] = cand_by_path[rel]
            if t_trace0 is not None and e["mtime"] < t_trace0:
                row["before_trace"] = True
        if in_graph:
            row["used_by"] = [x["from"] for x in edges_to.get(fid, []) if x["kind"] == "used"]
        rows.append(row)
    for fid, n in files_by_id.items():
        n["present"] = fid in present_ids
        if fid in present_ids:
            continue
        for p in n["paths"]:
            gl = gens(fid)
            row = {"path": p, "bytes": n["bytes"], "mtime": n["mtime"], "sha256": n["sha256"],
                   "node": fid, "present": False,
                   "origin": ("recorded" if gl else "unknown"),
                   "used_by": [x["from"] for x in edges_to.get(fid, []) if x["kind"] == "used"]}
            if gl:
                row["generated_by"] = [x["from"] for x in gl]
                row["basis"] = gl[0].get("basis")
            if sandbox is not None:
                row["note"] = ("not in the sandbox" if p not in sb_files
                               else "the sandbox holds a later version")
            rows.append(row)
    for fid, n in files_by_id.items():
        gl = gens(fid)
        n["origin"] = ("recorded" if any(not x.get("inferred") for x in gl)
                       else "inferred" if gl else "unknown")

    # ── copies ──
    out.mkdir(parents=True, exist_ok=True)
    copied = 0
    copied_bytes = 0
    skipped_total = 0
    if sandbox is not None and copy_mode != "none" and copy_limit > 0:
        for row in rows:
            if not row["present"] or row["bytes"] is None or row["bytes"] > copy_limit:
                continue
            if copy_mode == "graph":
                if not row.get("node"):
                    continue
            if copied_bytes + row["bytes"] > copy_total:
                skipped_total += 1
                continue
            src = sandbox / row["path"]
            dst = out / "files" / row["path"]
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            except OSError as exc:
                warnings.append(f"could not copy {row['path']}: {exc}")
                continue
            row["copied"] = "files/" + row["path"]
            copied += 1
            copied_bytes += row["bytes"]
            if row.get("node") and row["node"] in g.nodes:
                g.nodes[row["node"]].setdefault("copied", row["copied"])
        if skipped_total:
            warnings.append(f"{skipped_total} small file(s) not copied: the bundle reached "
                            f"its copy budget of {copy_total} bytes (--copy-total)")
    elif sandbox is None:
        warnings.append("no --sandbox: files are listed from the call records only; "
                        "none copied, none of unknown origin detected")

    # ── checks for a killed run ──
    open_agents = [a["id"] for a in agents.values()
                   if a.get("ts_start") and not a.get("ts_end") and not a.get("external")]
    no_result = [c["id"] for c in cmds if c.get("ts_end") is None]
    if open_agents:
        warnings.append(f"{len(open_agents)} agent(s) never ended "
                        f"({', '.join(open_agents[:5])}{'…' if len(open_agents) > 5 else ''}): "
                        "the run was still going, crashed or was killed")
    if no_result:
        warnings.append(f"{len(no_result)} command(s) have no result in their transcript "
                        "(still running when the transcript ends)")

    # ── assemble ──
    nodes = list(g.nodes.values())
    stamps = [t for n in nodes for t in (_epoch(n.get("ts_start")), _epoch(n.get("ts_end")),
                                          _epoch(n.get("ts"))) if t is not None]
    redact_pairs: List[Tuple[str, str]] = []
    if redact:
        if sandbox is not None:
            redact_pairs += [(str(sandbox), "<sandbox>"), (os.path.realpath(sandbox), "<sandbox>")]
        redact_pairs += [(str(trace_dir), "<trace>")]
        home = str(Path.home())
        if home and home != "/":
            redact_pairs.append((home, "~"))
    red = _redactor(redact_pairs) if redact else (lambda v: v)

    from .. import __version__ as tb_version  # type: ignore[attr-defined]
    meta = {
        "session": (trace_dir.parent.name if trace_dir.name in ("trace", "traces", ".")
                    else trace_dir.name),
        "exported_at": _iso(time.time()),
        "exporter": f"toolbase {tb_version}",
        "start": _iso(min(stamps)) if stamps else None,
        "end": _iso(max(stamps)) if stamps else None,
        "sandbox": "<sandbox>" if (sandbox is not None and redact) else (str(sandbox) if sandbox else None),
        "commands": [red(s) for s in commands],
        "counts": {
            "agents": sum(1 for n in nodes if n["kind"] == "agent"),
            "calls": sum(1 for n in nodes if n["kind"] == "call"),
            "failed_calls": sum(1 for n in nodes if n["kind"] == "call" and not n.get("ok", True)),
            "commands": sum(1 for n in nodes if n["kind"] == "command"),
            "messages": sum(1 for n in nodes if n["kind"] == "message"),
            "file_nodes": len(files_by_id),
            "files": len(rows),
            "unknown_origin": sum(1 for r in rows if r["origin"] == "unknown" and r["present"]),
            "copied": copied, "copied_bytes": copied_bytes,
        },
        "options": {"copy_mode": copy_mode, "copy_limit": copy_limit, "copy_total": copy_total,
                    "hash_limit": hash_limit, "redact": redact},
        "complete": not (open_agents or bad_a or bad_c or bad_m),
        "warnings": [red(w) for w in warnings],
        "inference": {"slack_before_s": SLACK_BEFORE, "slack_after_s": SLACK_AFTER},
    }
    nodes = red(nodes)
    rows = [{k: v for k, v in r.items() if v not in (None, [], False) or k in ("present", "sha256", "bytes")}
            for r in red(rows)]
    trace_doc = {"schema": BUNDLE_SCHEMA, "meta": meta, "nodes": nodes, "edges": g.edges}
    files_doc = {"schema": FILES_SCHEMA, "sandbox": meta["sandbox"], "files": rows}
    prov_doc = to_prov(nodes, g.edges)

    _dump(out / "trace.json", trace_doc)
    _dump(out / "files.json", files_doc)
    _dump(out / "prov.json", prov_doc)
    from .viewer import render
    html = render(trace_doc, rows)
    (out / "index.html").write_text(html, encoding="utf-8")

    sizes = {name: (out / name).stat().st_size
             for name in ("index.html", "trace.json", "prov.json", "files.json")}
    total = sum(sizes.values()) + copied_bytes
    return {"out": str(out), "meta": meta, "sizes": sizes, "bundle_bytes": total,
            "seconds": round(time.time() - t_begin, 1),
            "sessions": [(s.source, s.session, getattr(s, "attributed_by", None)) for s in sessions]}


def _dump(path: Path, doc: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"), default=str)


# ── W3C PROV-JSON ──────────────────────────────────────────────────────


def _pv(v: Any) -> Any:
    """A PROV-JSON attribute value: strings, numbers and booleans as they
    are; anything structured as a JSON string."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return json.dumps(v, ensure_ascii=False, default=str, sort_keys=True)


def _attrs(n: Dict[str, Any], skip: Iterable[str]) -> Dict[str, Any]:
    skip = set(skip) | {"id", "kind"}
    return {f"tb:{k}": _pv(v) for k, v in n.items() if k not in skip and v is not None}


def to_prov(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The graph as a W3C PROV-JSON document (https://www.w3.org/submissions/prov-json/).

    agent -> ``agent`` (``prov:SoftwareAgent``, or ``prov:Person`` for
    ``@user``); call and command -> ``activity`` with ``prov:startTime`` and
    ``prov:endTime``; file and message -> ``entity``. Edges: called ->
    ``wasAssociatedWith``, used -> ``used``, generated -> ``wasGeneratedBy``,
    spawned -> ``actedOnBehalfOf`` (child on behalf of parent), child_of ->
    ``wasInformedBy``, sent -> ``wasAttributedTo``, received ->
    ``wasInfluencedBy`` (recipient by message). Everything else is a ``tb:``
    attribute.
    """
    def q(i: str) -> str:
        return "tb:" + i

    doc: Dict[str, Any] = {"prefix": {"tb": PROV_NS}, "agent": {}, "activity": {}, "entity": {}}
    for n in nodes:
        k = n["kind"]
        if k == "agent":
            a = {"prov:type": {"$": "prov:Person" if n.get("external") else "prov:SoftwareAgent",
                               "type": "prov:QUALIFIED_NAME"}}
            if n.get("label"):
                a["prov:label"] = str(n["label"])
            a.update(_attrs(n, ("label",)))
            doc["agent"][q(n["id"])] = a
        elif k in ("call", "command"):
            a = {"prov:type": {"$": "tb:ToolCall" if k == "call" else "tb:ShellCommand",
                               "type": "prov:QUALIFIED_NAME"},
                 "prov:label": str(n.get("tool") if k == "call" else (n.get("command") or "")[:120])}
            if n.get("ts_start"):
                a["prov:startTime"] = n["ts_start"]
            if n.get("ts_end"):
                a["prov:endTime"] = n["ts_end"]
            a.update(_attrs(n, ("ts_start", "ts_end")))
            doc["activity"][q(n["id"])] = a
        elif k == "file":
            a = {"prov:type": {"$": "tb:File", "type": "prov:QUALIFIED_NAME"}}
            if n.get("paths"):
                a["prov:location"] = n["paths"][0]
                a["prov:label"] = n["paths"][0].rsplit("/", 1)[-1]
            a.update(_attrs(n, ()))
            doc["entity"][q(n["id"])] = a
        elif k == "message":
            a = {"prov:type": {"$": "tb:Spawn" if n.get("event") == "spawn" else "tb:Message",
                               "type": "prov:QUALIFIED_NAME"}}
            body = n.get("body") if n.get("event") != "spawn" else n.get("initial_message")
            if body:
                a["prov:value"] = str(body)
            a.update(_attrs(n, ()))
            doc["entity"][q(n["id"])] = a

    rel: Dict[str, Dict[str, Any]] = {}
    for i, e in enumerate(edges):
        k, s, d = e["kind"], q(e["from"]), q(e["to"])
        extra = {f"tb:{x}": _pv(v) for x, v in e.items() if x not in ("kind", "from", "to", "ts")}
        if k == "called":
            r, body = "wasAssociatedWith", {"prov:activity": d, "prov:agent": s}
        elif k == "used":
            r, body = "used", {"prov:activity": s, "prov:entity": d}
        elif k == "generated":
            r, body = "wasGeneratedBy", {"prov:entity": d, "prov:activity": s}
        elif k == "spawned":
            r, body = "actedOnBehalfOf", {"prov:delegate": d, "prov:responsible": s}
        elif k == "child_of":
            r, body = "wasInformedBy", {"prov:informed": s, "prov:informant": d}
        elif k == "sent":
            r, body = "wasAttributedTo", {"prov:entity": d, "prov:agent": s}
        elif k == "received":
            r, body = "wasInfluencedBy", {"prov:influencee": d, "prov:influencer": s}
        else:
            continue
        if e.get("ts") and r in ("used", "wasGeneratedBy"):
            body["prov:time"] = e["ts"]
        body.update(extra)
        rel.setdefault(r, {})[f"_:e{i}"] = body
    doc.update(rel)
    return {k: v for k, v in doc.items() if v}
