"""Trace records, the append-only writer, the agent registry and the
toolkit-side API. Stdlib only: this module is imported inside toolkit host
processes as well as by the orchestrator."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Union

from .._sanitize import sanitize_args

try:  # POSIX advisory lock across processes; absent on Windows.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]

CALL_SCHEMA = "toolbase-call/1"
CALLS_FILE = "calls.jsonl"
AGENTS_FILE = "agents.jsonl"

# The request ``_meta`` key the orchestrator sets on every traced call.
META_KEY = "toolbase/trace"
# Environment hand-off for a worker *process* a toolkit starts (see
# ``child_env``). The thread/async path uses a context variable instead.
ENV_CALL = "TOOLBASE_TRACE_CALL"
ENV_DIR = "TOOLBASE_TRACE_DIR"
ENV_AGENT = "TOOLBASE_TRACE_AGENT"

DEFAULT_HASH_LIMIT = 100 * 1024 * 1024  # bytes; larger files: size + mtime only
RESULT_HEAD_CHARS = 2048
ARG_STR_MAX = 16 * 1024
MAX_FILES_PER_CALL = 200
_MAX_PATH_LEN = 4096
_RESULT_SCAN_CHARS = 1024 * 1024
_MAX_RESULT_TOKENS = 1000

PathLike = Union[str, os.PathLike]


# ── small helpers ──────────────────────────────────────────────────────


def now_iso() -> str:
    """UTC timestamp, millisecond precision, ``Z`` suffix."""
    return _iso(time.time())


def _iso(epoch: float) -> str:
    return (datetime.fromtimestamp(epoch, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def new_call_id() -> str:
    return "c-" + secrets.token_hex(5)


def new_agent_id() -> str:
    return "ag-" + secrets.token_hex(6)


def hash_limit_from_env(default: int = DEFAULT_HASH_LIMIT) -> int:
    raw = os.environ.get("TOOLBASE_TRACE_HASH_LIMIT")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return default


# ── writer ─────────────────────────────────────────────────────────────

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(path)
        if lock is None:
            lock = _locks[path] = threading.Lock()
        return lock


def append_jsonl(path: PathLike, record: Dict[str, Any]) -> None:
    """Append one JSON line to ``path`` and hand it to the OS before returning.

    Safe across threads (a per-path lock) and processes (``O_APPEND`` plus an
    exclusive ``flock`` around a single write). The parent directory is
    created on demand. Raises on failure; callers on the hot path swallow.
    """
    line = (json.dumps(record, ensure_ascii=False, default=str,
                       separators=(",", ":")) + "\n").encode("utf-8")
    p = os.fspath(path)
    with _lock_for(p):
        try:
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        except FileNotFoundError:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_EX)
            view = memoryview(line)
            while view:
                n = os.write(fd, view)
                view = view[n:]
        finally:
            os.close(fd)  # also releases the flock


def read_jsonl(path: PathLike) -> List[Dict[str, Any]]:
    """Read a JSONL file, skipping blank and torn lines (a killed writer can
    leave a partial last line)."""
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


# ── agent registry ─────────────────────────────────────────────────────


def register_agent(
    dir: PathLike,
    id: str,
    label: Optional[str] = None,
    parent: Optional[str] = None,
    model: Optional[str] = None,
    engine: Optional[str] = None,
    harness: Optional[str] = None,
) -> str:
    """Append an ``agent_started`` event to ``<dir>/agents.jsonl``."""
    append_jsonl(Path(dir) / AGENTS_FILE, {
        "event": "agent_started", "id": id, "label": label,
        "parent": parent, "model": model, "engine": engine,
        "harness": harness, "ts": now_iso(),
    })
    return id


def end_agent(dir: PathLike, id: str) -> None:
    """Append an ``agent_ended`` event to ``<dir>/agents.jsonl``."""
    append_jsonl(Path(dir) / AGENTS_FILE,
                 {"event": "agent_ended", "id": id, "ts": now_iso()})


# ── the Trace handle ───────────────────────────────────────────────────


class Trace:
    """Where one agent's calls go: ``Trace(dir, agent=...)``.

    ``dir`` is the trace (session) directory holding ``calls.jsonl`` and
    ``agents.jsonl``; several ``Trace`` objects, threads and processes may
    share it. ``agent`` is the id stamped on every call record made through
    this handle (generated when omitted). ``base`` is the directory paths are
    stored relative to when no toolkit ``base_directory`` applies.
    ``hash_limit`` (bytes) caps which files are hashed during the run.

    Constructing a ``Trace`` writes nothing; the directory appears with the
    first record. Registering the agent is a separate, explicit step
    (``register_agent``), so a harness that builds several orchestrators for
    one agent registers it once.
    """

    def __init__(
        self,
        dir: PathLike,
        agent: Optional[str] = None,
        *,
        base: Optional[PathLike] = None,
        hash_limit: Optional[int] = None,
    ):
        self.dir = Path(dir).expanduser().absolute()
        self.agent = agent or new_agent_id()
        self.base = Path(base).expanduser().absolute() if base else None
        self.hash_limit = hash_limit_from_env() if hash_limit is None else int(hash_limit)

    def __repr__(self) -> str:
        return f"Trace({str(self.dir)!r}, agent={self.agent!r})"

    @property
    def calls_path(self) -> Path:
        return self.dir / CALLS_FILE

    @property
    def agents_path(self) -> Path:
        return self.dir / AGENTS_FILE

    def for_agent(self, agent: str) -> "Trace":
        """Same directory and settings, another agent."""
        return Trace(self.dir, agent, base=self.base, hash_limit=self.hash_limit)

    def register_agent(
        self,
        id: Optional[str] = None,
        label: Optional[str] = None,
        parent: Optional[str] = None,
        model: Optional[str] = None,
        engine: Optional[str] = None,
        harness: Optional[str] = None,
    ) -> str:
        """Record ``agent_started`` (``id`` defaults to this handle's agent)."""
        return register_agent(self.dir, id or self.agent, label, parent,
                              model, engine, harness)

    def end_agent(self, id: Optional[str] = None) -> None:
        end_agent(self.dir, id or self.agent)

    def write_call(self, record: Dict[str, Any]) -> None:
        append_jsonl(self.calls_path, record)


# ── record building ────────────────────────────────────────────────────


def result_digest(text: Any) -> Dict[str, Any]:
    s = "" if text is None else (text if isinstance(text, str) else str(text))
    return {"sha256": hashlib.sha256(s.encode("utf-8", "replace")).hexdigest(),
            "head": s[:RESULT_HEAD_CHARS]}


def trace_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Arguments as recorded: secrets redacted, whole lists, long strings cut."""
    if not args:
        return {}
    try:
        return sanitize_args(dict(args), max_items=None, max_str=ARG_STR_MAX)
    except Exception:
        return {"<unrecordable>": type(args).__name__}


def build_call_record(
    *,
    id: str,
    agent: Optional[str],
    toolkit: Optional[str],
    version: Optional[str],
    tool: str,
    args: Optional[Dict[str, Any]],
    ok: bool,
    error: Optional[str],
    result: Any,
    ts_start: str,
    ts_end: str,
    files: List[Dict[str, Any]],
    parent: Optional[str] = None,
) -> Dict[str, Any]:
    """One ``toolbase-call/1`` record, keys in contract order."""
    return {
        "schema": CALL_SCHEMA,
        "id": id, "agent": agent, "ts_start": ts_start, "ts_end": ts_end,
        "toolkit": toolkit, "version": version, "tool": tool,
        "args": trace_args(args), "ok": bool(ok), "error": error,
        "result": result_digest(result), "files": files, "parent": parent,
    }


# ── files named in args and results ────────────────────────────────────

# Path-ish tokens in free text: no whitespace, quotes or brackets.
_TOKEN_RE = re.compile(r"[^\s\"'`<>()\[\]{},;|]+")
_TRAILING = ".:;,!?"
_hash_cache: Dict[tuple, str] = {}
_hash_cache_guard = threading.Lock()


def _arg_strings(value: Any, out: List[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _arg_strings(v, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _arg_strings(v, out, depth + 1)


def _result_tokens(text: str) -> List[str]:
    out: List[str] = []
    for m in _TOKEN_RE.finditer(text[:_RESULT_SCAN_CHARS]):
        tok = m.group(0).rstrip(_TRAILING)
        if "=" in tok and "/" in tok.split("=", 1)[1]:
            tok = tok.split("=", 1)[1]  # key=path
        if len(tok) < 3:
            continue
        # A path has a separator or a dotted extension.
        if "/" in tok or re.search(r"\w\.\w{1,10}$", tok):
            out.append(tok)
            if len(out) >= _MAX_RESULT_TOKENS:
                break
    return out


def sha256_file(path: str, st: os.stat_result) -> str:
    key = (path, st.st_size, st.st_mtime_ns)
    with _hash_cache_guard:
        hit = _hash_cache.get(key)
    if hit:
        return hit
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    with _hash_cache_guard:
        if len(_hash_cache) > 10000:
            _hash_cache.clear()
        _hash_cache[key] = digest
    return digest


def collect_files(
    args: Optional[Dict[str, Any]],
    result: Any,
    *,
    base: Optional[PathLike] = None,
    declared: Optional[Iterable[PathLike]] = None,
    hash_limit: int = DEFAULT_HASH_LIMIT,
) -> List[Dict[str, Any]]:
    """Regular files named in ``args``, ``result`` and ``declared`` that exist
    now. Relative names resolve against ``base``; a file under ``base`` is
    recorded relative to it, any other by its absolute path."""
    base_p = Path(base).expanduser() if base else Path.cwd()
    try:
        base_real = os.path.realpath(base_p)
    except OSError:
        base_real = str(base_p)
    candidates: List[str] = []
    if declared:
        candidates.extend(os.fspath(p) for p in declared)
    _arg_strings(args or {}, candidates)
    if isinstance(result, str) and result:
        candidates.extend(_result_tokens(result))

    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in candidates:
        if len(out) >= MAX_FILES_PER_CALL:
            break
        if not raw or len(raw) > _MAX_PATH_LEN or "\n" in raw or "\0" in raw:
            continue
        if raw.startswith("file://"):
            raw = raw[len("file://"):]
        p = os.path.expanduser(raw.strip())
        full = p if os.path.isabs(p) else os.path.join(base_p, p)
        try:
            st = os.stat(full)
        except (OSError, ValueError):
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        real = os.path.realpath(full)
        if real in seen:
            continue
        seen.add(real)
        if real == base_real or real.startswith(base_real.rstrip(os.sep) + os.sep):
            shown = os.path.relpath(real, base_real)
        else:
            shown = os.path.abspath(full)
        entry: Dict[str, Any] = {"path": shown, "bytes": st.st_size,
                                 "mtime": _iso(st.st_mtime), "sha256": None}
        if st.st_size <= hash_limit:
            try:
                entry["sha256"] = sha256_file(real, st)
            except OSError:
                pass
        out.append(entry)
    return out


# ── the current call, inside a toolkit ─────────────────────────────────

_CURRENT: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = \
    contextvars.ContextVar("toolbase_trace_current", default=None)


def current() -> Optional[Dict[str, Any]]:
    """The traced call this code is running under, or ``None``.

    Inside a toolkit tool's ``execute`` (in the toolkit host process) this is
    the call toolbase is recording: ``{"call_id", "dir", "agent", "toolkit",
    "version", "base"}``. ``None`` when tracing is off. Thread-local in
    effect (a context variable): a worker thread or process sees it only if
    handed over with ``bind(ctx)`` or ``child_env(ctx)``.
    """
    ctx = _CURRENT.get()
    if ctx:
        return dict(ctx)
    call_id, d = os.environ.get(ENV_CALL), os.environ.get(ENV_DIR)
    if call_id and d:
        return {"call_id": call_id, "dir": d,
                "agent": os.environ.get(ENV_AGENT), "toolkit": None,
                "version": None, "base": None}
    return None


@contextlib.contextmanager
def bind(ctx: Optional[Dict[str, Any]]) -> Iterator[None]:
    """Make ``ctx`` the ``current()`` call for the enclosed block (for a
    worker thread that received a context captured earlier). ``None`` is a
    no-op."""
    if not ctx:
        yield
        return
    token = _CURRENT.set(dict(ctx))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def child_env(ctx: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Environment variables that let a worker *process* see ``ctx`` (default
    ``current()``) through its own ``current()``. Empty when not traced."""
    ctx = ctx if ctx is not None else current()
    if not ctx or not ctx.get("call_id") or not ctx.get("dir"):
        return {}
    env = {ENV_CALL: str(ctx["call_id"]), ENV_DIR: str(ctx["dir"])}
    if ctx.get("agent"):
        env[ENV_AGENT] = str(ctx["agent"])
    return env


def record_call(
    ctx: Optional[Dict[str, Any]],
    *,
    tool: str,
    args: Optional[Dict[str, Any]] = None,
    ok: bool = True,
    error: Optional[str] = None,
    result: Any = None,
    ts_start: Optional[str] = None,
    ts_end: Optional[str] = None,
    toolkit: Optional[str] = None,
    version: Optional[str] = None,
    files: Optional[Iterable[PathLike]] = None,
    parent: Optional[str] = None,
    id: Optional[str] = None,
) -> Optional[str]:
    """Append a call record made by a toolkit, linked to ``ctx``.

    ``ctx`` is a ``current()`` value captured when the outer call ran (for
    heptapod, inside ``SubmitJob``). The record goes to ``ctx["dir"]``, takes
    the agent, toolkit and version from ``ctx`` unless given, and has
    ``parent = ctx["call_id"]`` unless ``parent`` is given. ``files`` are
    declared paths, recorded with those named in ``args`` and ``result``.

    Returns the new record's id, or ``None`` when ``ctx`` is ``None`` or the
    write failed. Never raises.
    """
    if not ctx or not ctx.get("dir"):
        return None
    try:
        cid = id or new_call_id()
        stamp = now_iso()
        rec = build_call_record(
            id=cid, agent=ctx.get("agent"),
            toolkit=toolkit or ctx.get("toolkit"),
            version=version or ctx.get("version"),
            tool=tool, args=args, ok=ok, error=error, result=result,
            ts_start=ts_start or stamp, ts_end=ts_end or stamp,
            files=collect_files(args, result, base=ctx.get("base"),
                                declared=files,
                                hash_limit=hash_limit_from_env()),
            parent=parent if parent is not None else ctx.get("call_id"),
        )
        append_jsonl(Path(ctx["dir"]) / CALLS_FILE, rec)
        return cid
    except Exception as e:  # tracing never breaks the toolkit
        _warn(f"record_call failed: {e}")
        return None


class _ChildCall:
    def __init__(self, ctx, tool, args, toolkit, version, files):
        self.ctx, self.tool, self.args = ctx, tool, args
        self.toolkit, self.version = toolkit, version
        self.files: List[PathLike] = list(files or [])
        self.result: Any = None
        self.ok = True
        self.error: Optional[str] = None
        self.id: Optional[str] = new_call_id() if ctx else None


@contextlib.contextmanager
def child_call(
    ctx: Optional[Dict[str, Any]],
    tool: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    toolkit: Optional[str] = None,
    version: Optional[str] = None,
    files: Optional[Iterable[PathLike]] = None,
) -> Iterator[_ChildCall]:
    """Time a block and record it as a call whose parent is ``ctx``.

    Set ``.result`` (and optionally ``.ok``/``.error``, extend ``.files``)
    inside the block; an exception marks it failed and propagates. ``.id`` is
    the record's id, known up front (``None`` when ``ctx`` is ``None``, in
    which case nothing is written). While the block runs, ``current()`` is
    this inner call, so anything it launches links to it in turn.
    """
    rec = _ChildCall(ctx, tool, args, toolkit, version, files)
    ts_start = now_iso()
    inner = None
    if ctx:
        inner = dict(ctx, call_id=rec.id)
        if toolkit:
            inner["toolkit"] = toolkit
    try:
        with bind(inner):
            yield rec
    except BaseException as e:
        rec.ok, rec.error = False, (str(e) or type(e).__name__)
        raise
    finally:
        if ctx:
            record_call(ctx, tool=tool, args=args, ok=rec.ok, error=rec.error,
                        result=rec.result, ts_start=ts_start,
                        toolkit=toolkit, version=version, files=rec.files,
                        id=rec.id)


def _warn(msg: str) -> None:
    try:
        sys.stderr.write(f"[toolbase-trace] {msg}\n")
    except Exception:
        pass
