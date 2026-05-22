"""
Line-delimited JSON-RPC primitives for the Phase 3C-2 setup channel.

This is **not** MCP. It's a minimal private protocol between the
parent process (orchestrator / CLI) and a setup-host subprocess
running ``setup.py::setup(ctx)`` or ``validate(ctx)`` inside the
toolkit's own venv.

Why a dedicated wire (per the manager's 3C-2 sketch sign-off):

- The serve-time MCPClient channel is for tool calls, not setup.
  Setup needs prompts, terminal access, progress bars, $EDITOR fallback
  — none of which fit cleanly through MCP.
- Setup runs once per install (or on ``toolbase setup``); a separate
  short-lived channel is simpler than reusing serve infra.
- The serve channel is itself about to shift under the Orchestral 1.4
  stdio cleanup. Keeping setup independent means that cleanup can
  happen without disturbing 3C-2.

Wire format
-----------

One JSON object per line on stdin/stdout. UTF-8. The format is
deliberately a tiny subset of JSON-RPC 2.0:

- **Request** (subprocess → parent): ``{"id": <int>, "method": str,
  "params": dict}``. Every request must get a response.
- **Response** (parent → subprocess): ``{"id": <int>, "result": <any>}``
  or ``{"id": <int>, "error": {"code": str, "message": str}}``.
- **Notification** (parent → subprocess, no response expected):
  ``{"method": str, "params": dict}`` — used for streaming download
  progress while a ``download`` request is in flight. The notification
  carries the in-flight request's ``id`` inside ``params`` so the
  subprocess can route it.
- **Hello** (subprocess → parent, first message): ``{"method": "hello",
  "params": {"protocol": int, "python_version": str, "has_setup": bool,
  "has_validate": bool}}``. Exactly one hello per session, no ``id``.
- **Go** (parent → subprocess, response to hello): ``{"method": "go",
  "params": {"mode": "setup"|"validate", "prompt_mode": "ask"|"skip"|
  "yes"|"no", "config": dict, "toolkit_path": str, "data_dir": str,
  "cache_dir": str, "config_path": str}}``. No ``id``.
- **Done** (subprocess → parent, last message): ``{"method": "done",
  "params": {"result": bool, "traceback": str|null}}``. No ``id``.

Why this slightly bespoke shape (handshake messages without ``id``):
the hello/go pair is a one-time bootstrap, not a request/response.
Treating them like normal requests would mean reserving id=0 with a
special meaning, which is the same complexity in a different place.
The line-mode helpers below handle both shapes uniformly.

Framing
-------

One JSON object per line. ``\n`` is the only delimiter. Payloads must
not contain literal newlines, which ``json.dumps`` guarantees by
default (it escapes ``\n`` inside strings). On read, anything before
the next ``\n`` is one message; partial reads are tolerated by the
``read_message`` helper which buffers across calls.

This is the same line-mode discipline as ``_toolkit_host.py``'s
handshake (one JSON line on first stdout flush). The setup channel
extends it to a multi-message conversation but the framing is the
same.

Errors
------

A request that triggers an error on the parent side comes back as a
response with an ``error`` key instead of ``result``. The subprocess
side translates that into a Python exception (``SetupRPCError``) and
raises it inside the toolkit author's ``setup.py`` — they ``try``/
``except`` like with any normal exception.

If the parent process dies mid-conversation (broken pipe,
disconnected stdin), the subprocess detects EOF on its next
``read_message`` call and propagates it as a clean exit. No zombie
processes, no infinite waits.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, BinaryIO, Dict, IO, Optional


# Bumped when the wire format changes in a way that requires both ends
# to be in sync. The hello message carries this; the parent rejects
# protocols it doesn't understand with a clear error.
PROTOCOL_VERSION = 1


class SetupRPCError(RuntimeError):
    """An RPC call raised on the parent side and propagated to the subprocess.

    Carries the same ``code`` / ``message`` the parent emitted. Authors
    can catch this in ``setup.py`` to handle download failures, prompt
    cancellation, etc. gracefully:

    ```python
    try:
        ctx.download(url, dest, sha256="abc...")
    except RuntimeError as e:
        ctx.warn(f"Download failed: {e}; continuing without it")
    ```

    ``RuntimeError`` is the public exception type to keep author code
    independent of the RPC layer; this subclass exists so the runner
    can distinguish RPC-origin exceptions from arbitrary author code.
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code
        self.rpc_message = message


@dataclass
class Message:
    """Parsed RPC line.

    Exactly one of (request, response, notification, hello, go, done)
    is true at a time, but rather than enumerating that as an enum we
    store the raw fields and let the consumer dispatch on shape. This
    matches how the parent's pump and the subprocess's RPC client both
    work: dispatch by ``method`` first, then check for ``id`` /
    ``result`` / ``error`` to decide what to do.
    """

    raw: Dict[str, Any]

    @property
    def method(self) -> Optional[str]:
        return self.raw.get("method")

    @property
    def id(self) -> Optional[int]:
        return self.raw.get("id")

    @property
    def params(self) -> Dict[str, Any]:
        return self.raw.get("params") or {}

    @property
    def result(self) -> Any:
        return self.raw.get("result")

    @property
    def error(self) -> Optional[Dict[str, Any]]:
        return self.raw.get("error")

    @property
    def is_request(self) -> bool:
        return "method" in self.raw and "id" in self.raw

    @property
    def is_response(self) -> bool:
        return "id" in self.raw and "method" not in self.raw

    @property
    def is_notification(self) -> bool:
        return "method" in self.raw and "id" not in self.raw


def encode(obj: Dict[str, Any]) -> str:
    """Serialize a dict to a single JSON line (newline-terminated).

    ``json.dumps`` with default settings escapes embedded newlines, so
    the result is a single line. The trailing ``\\n`` is the message
    delimiter.

    ``ensure_ascii=False`` keeps non-ASCII characters readable in the
    log files we write a copy of these messages to (helpful when an
    author has Unicode in a prompt label).
    """
    return json.dumps(obj, ensure_ascii=False) + "\n"


def write_message(stream: IO[str], obj: Dict[str, Any]) -> None:
    """Write one message and flush.

    Always flush. The parent and subprocess sit on opposite sides of a
    pipe and the OS may buffer; if we don't flush, the peer never sees
    the message. ``bufsize=1`` on Popen makes this less critical but
    not zero — explicit flushes are cheap insurance.
    """
    stream.write(encode(obj))
    stream.flush()


def parse_line(line: str) -> Message:
    """Parse one line into a Message.

    Raises ``ValueError`` on malformed JSON or non-object payloads. The
    caller (parent pump or subprocess RPC loop) decides how to handle:
    in practice both treat it as a fatal protocol error.
    """
    if not line:
        raise ValueError("empty RPC line")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise ValueError(f"malformed RPC line: {e}; raw={line!r}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"RPC line must be a JSON object, got {type(obj).__name__}")
    return Message(raw=obj)


def read_message(stream: IO[str]) -> Optional[Message]:
    """Read one message. Returns None on EOF.

    Uses ``readline`` which handles partial reads transparently. EOF is
    indicated by an empty string from ``readline`` (per Python docs);
    we return None so the caller can distinguish "stream closed" from
    "got a message."

    No timeout. The setup conversation is interactive and either side
    may legitimately wait for the user. The parent enforces process-
    level timeouts via SIGTERM if needed (see runner.py).
    """
    line = stream.readline()
    if not line:
        return None
    # Strip trailing newline only; preserve any internal whitespace
    # that some authors might rely on (multi-line prompt labels, e.g.).
    if line.endswith("\n"):
        line = line[:-1]
    if not line:
        # Blank line — protocol violation, but easier to ignore than to
        # error on. The peer might have flushed an empty buffer between
        # messages. Skip and try again.
        return read_message(stream)
    return parse_line(line)


def make_response(req_id: int, result: Any) -> Dict[str, Any]:
    """Build a success-response payload."""
    return {"id": req_id, "result": result}


def make_error_response(
    req_id: int,
    code: str,
    message: str,
) -> Dict[str, Any]:
    """Build an error-response payload."""
    return {"id": req_id, "error": {"code": code, "message": message}}


def make_request(req_id: int, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Build a request payload."""
    return {"id": req_id, "method": method, "params": params}


def make_notification(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Build a notification payload (no id)."""
    return {"method": method, "params": params}


def make_hello(
    *,
    has_setup: bool,
    has_validate: bool,
    python_version: Optional[str] = None,
    load_error: Optional[str] = None,
) -> Dict[str, Any]:
    """First message from subprocess to parent.

    ``load_error`` is set to a Python traceback string when ``setup.py``
    failed to import (syntax error, ImportError, etc.). The parent
    surfaces it as the failure message instead of "setup() not defined,"
    because "your code has a syntax error" is the more useful diagnostic.
    Always sent — even on load failure — so the protocol's "hello first"
    invariant holds.
    """
    if python_version is None:
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    params: Dict[str, Any] = {
        "protocol": PROTOCOL_VERSION,
        "python_version": python_version,
        "has_setup": has_setup,
        "has_validate": has_validate,
    }
    if load_error is not None:
        params["load_error"] = load_error
    return {
        "method": "hello",
        "params": params,
    }


def make_go(
    *,
    mode: str,
    prompt_mode: str,
    config: Dict[str, Any],
    toolkit_path: str,
    data_dir: str,
    cache_dir: str,
    config_path: str,
) -> Dict[str, Any]:
    """Second message from parent to subprocess (in response to hello)."""
    return {
        "method": "go",
        "params": {
            "mode": mode,
            "prompt_mode": prompt_mode,
            "config": config,
            "toolkit_path": toolkit_path,
            "data_dir": data_dir,
            "cache_dir": cache_dir,
            "config_path": config_path,
        },
    }


def make_done(*, result: bool, traceback_str: Optional[str] = None) -> Dict[str, Any]:
    """Last message from subprocess to parent."""
    return {
        "method": "done",
        "params": {
            "result": bool(result),
            "traceback": traceback_str,
        },
    }
