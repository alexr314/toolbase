"""
``SetupContext`` — the toolbox passed to ``setup.py::setup(ctx)`` and
``validate(ctx)``.

Lives inside the toolkit's own subprocess. Routes calls that need
parent-process services (prompts, config writes, downloads, terminal
output styling) over the line-mode RPC protocol in ``_rpc.py``;
everything else runs locally with no round-trip.

Local-vs-RPC split (per the manager's 3C-2 sketch sign-off):

| Method                | Where  |
|-----------------------|--------|
| ``info/warn/error/hint/success(msg)`` | RPC (parent renders Rich) |
| ``prompt`` and the other prompt_*     | RPC (parent owns terminal) |
| ``set_config``                        | RPC (parent owns the file) |
| ``download``                          | RPC (parent owns Rich progress) |
| ``get_config`` / ``config``           | local (snapshot at startup) |
| ``toolkit_path`` / ``data_dir`` / ``cache_dir`` / ``config_path`` | local |

The local snapshot of config is updated by ``set_config`` so within
one ``setup(ctx)`` call, ``get_config`` reflects writes that have
happened. The parent's file is the canonical source; if the
subprocess crashes mid-setup, the file holds whatever was written up
to the crash. Same UX as Tier 1's "user pressed Esc halfway through."

This module is **shipped self-contained into the toolkit env** the
same way ``_toolkit_host.py`` is (PYTHONPATH points at the
orchestrator's ``toolbase/`` parent). It must therefore not import
anything outside the stdlib + ``toolbase.setup._rpc``. In particular,
no Rich, no Click, no ``toolbase.config`` — those live on the parent
side and reach us via RPC.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, IO, Optional

from . import _rpc


class _SetupContextRPC:
    """Internal RPC client — talks to the parent over stdin/stdout.

    Holds a request-id counter, the open streams, and a small registry
    of in-flight progress notifications so a future ``download`` call
    can look them up. (The notification handler is wired in 3C-2 Day 3
    when downloads land; for Day 1 the registry is empty.)
    """

    def __init__(
        self,
        *,
        rx: IO[str],   # stdin: messages from parent
        tx: IO[str],   # stdout: messages to parent
    ):
        self._rx = rx
        self._tx = tx
        self._next_id = 1

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send a request, wait for its response, return the result.

        Synchronous and blocking — the setup conversation is intrinsically
        sequential (one prompt at a time, one download at a time). The
        only async-ish thing is download progress notifications, which
        arrive while a ``download`` request is in flight; we handle
        those inline in this loop without breaking the request/response
        contract.
        """
        req_id = self._next_id
        self._next_id += 1
        _rpc.write_message(self._tx, _rpc.make_request(req_id, method, params or {}))

        while True:
            msg = _rpc.read_message(self._rx)
            if msg is None:
                # Parent closed our stdin. We can't continue; raise so
                # the runner sees a clean exit with a clear cause.
                raise _rpc.SetupRPCError(
                    "parent_disconnected",
                    "parent process closed the RPC channel mid-call",
                )
            if msg.is_notification:
                self._handle_notification(msg)
                continue
            if not msg.is_response:
                # Protocol violation — drop and continue. The runner
                # would have caught this on its side too; logging is
                # cleaner than crashing the author's setup.py.
                continue
            if msg.id != req_id:
                # Out-of-order response. Should not happen with the
                # current synchronous protocol, but tolerate gracefully.
                continue
            err = msg.error
            if err is not None:
                raise _rpc.SetupRPCError(
                    err.get("code", "rpc_error"),
                    err.get("message", "unspecified RPC error"),
                )
            return msg.result

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a one-way notification. No response awaited.

        Currently only used internally; author-facing methods are all
        request/response. Reserved for future use (e.g., heartbeat).
        """
        _rpc.write_message(self._tx, _rpc.make_notification(method, params or {}))

    def _handle_notification(self, msg: _rpc.Message) -> None:
        """Dispatch a parent-pushed notification.

        For Day 1 there are no notifications. Day 3's downloads helper
        adds ``progress`` and ``progress.done``; this is the dispatch
        seam.
        """
        # No-op for Day 1.
        return


class SetupContext:
    """The ``ctx`` object passed to ``setup.py::setup(ctx)`` and ``validate(ctx)``.

    See ``tb-package/docs/SETUP_SYSTEM_SPEC.md`` §"SetupContext API"
    for the public surface. This class wraps the RPC client and
    exposes a stable, author-facing API with no RPC ceremony visible.

    Authors construct nothing; the runner builds this and hands it in.
    """

    def __init__(
        self,
        *,
        rpc: _SetupContextRPC,
        mode: str,                # "setup" | "validate"
        prompt_mode: str,         # "ask" | "skip" | "yes" | "no"
        config_snapshot: Dict[str, Any],
        toolkit_path: Path,
        data_dir: Path,
        cache_dir: Path,
        config_path: Path,
    ):
        self._rpc = rpc
        self._mode = mode
        self._prompt_mode = prompt_mode
        self._config: Dict[str, Any] = dict(config_snapshot)
        self._toolkit_path = toolkit_path
        self._data_dir = data_dir
        self._cache_dir = cache_dir
        self._config_path = config_path

    # ── local properties (no RPC) ──────────────────────────────────

    @property
    def toolkit_path(self) -> Path:
        return self._toolkit_path

    @property
    def data_dir(self) -> Path:
        # Auto-create on first access. data_dir is "persistent, OK to
        # write" per the spec, so the toolkit can drop files into it
        # without checking existence.
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    @property
    def cache_dir(self) -> Path:
        # Same auto-create discipline as data_dir; cache is "OK to
        # delete" but should still exist when accessed.
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def config(self) -> Dict[str, Any]:
        """Read-only-ish view of the current config snapshot.

        Returns a copy to discourage authors from mutating the snapshot
        directly; ``set_config`` is the supported path. The copy is
        cheap (shallow dict) and the alternative — handing back the
        live dict — invites a hard-to-debug bug class where local
        mutations don't reach the file.
        """
        return dict(self._config)

    def get_config(self, name: str, default: Any = None) -> Any:
        """Read a value from the snapshot.

        Reflects writes performed via ``set_config`` earlier in this
        same ``setup(ctx)`` call (write-through to local snapshot).
        """
        return self._config.get(name, default)

    # ── output (RPC: parent renders) ───────────────────────────────

    def info(self, message: str) -> None:
        self._rpc.call("log", {"level": "info", "message": str(message)})

    def warn(self, message: str) -> None:
        self._rpc.call("log", {"level": "warn", "message": str(message)})

    def error(self, message: str) -> None:
        self._rpc.call("log", {"level": "error", "message": str(message)})

    def hint(self, message: str) -> None:
        self._rpc.call("log", {"level": "hint", "message": str(message)})

    def success(self, message: str) -> None:
        self._rpc.call("log", {"level": "success", "message": str(message)})

    # ── prompts ────────────────────────────────────────────────────
    #
    # All prompts route through one ``prompt`` RPC method on the
    # parent. Each ``prompt_*`` here just builds a params dict with
    # ``kind`` set to its type and forwards. The parent's handler
    # dispatches on ``kind``.
    #
    # The result type matches the kind:
    #   - prompt → str (or None if user skipped)
    #   - prompt_path → Path (or None)
    #   - prompt_int → int (or None)
    #   - prompt_float → float (or None)
    #   - prompt_secret → str (or None)
    #   - confirm → bool (always non-None; ``default`` decides skip-mode)
    #   - choice → str (the chosen option's key, or None)
    #
    # In validate mode, all prompt methods raise loudly per the spec
    # ("validate(ctx) must be fast — no prompting"). The check happens
    # client-side here so we don't pay an RPC round-trip just to learn
    # we shouldn't have called.

    def _require_setup_mode(self, method_name: str) -> None:
        if self._mode == "validate":
            raise RuntimeError(
                f"ctx.{method_name}() is not allowed in validate(ctx). "
                "Move the call into setup(ctx) — validate must run "
                "without prompting (it's called at every serve startup)."
            )

    def prompt(self, label: str, default: Optional[str] = None) -> Optional[str]:
        self._require_setup_mode("prompt")
        return self._rpc.call("prompt", {
            "kind": "string",
            "label": label,
            "default": default,
        })

    def prompt_path(self, label: str, *, must_exist: bool = False,
                    default: Optional[str] = None) -> Optional[Path]:
        self._require_setup_mode("prompt_path")
        result = self._rpc.call("prompt", {
            "kind": "path",
            "label": label,
            "default": default,
            "must_exist": must_exist,
        })
        return Path(result) if result is not None else None

    def prompt_int(self, label: str, *, default: Optional[int] = None,
                   min: Optional[int] = None,
                   max: Optional[int] = None) -> Optional[int]:
        self._require_setup_mode("prompt_int")
        return self._rpc.call("prompt", {
            "kind": "int",
            "label": label,
            "default": default,
            "min": min,
            "max": max,
        })

    def prompt_float(self, label: str, *, default: Optional[float] = None,
                     min: Optional[float] = None,
                     max: Optional[float] = None) -> Optional[float]:
        self._require_setup_mode("prompt_float")
        return self._rpc.call("prompt", {
            "kind": "float",
            "label": label,
            "default": default,
            "min": min,
            "max": max,
        })

    def prompt_secret(self, label: str) -> Optional[str]:
        self._require_setup_mode("prompt_secret")
        return self._rpc.call("prompt", {
            "kind": "secret",
            "label": label,
        })

    def confirm(self, label: str, default: bool = False) -> bool:
        self._require_setup_mode("confirm")
        result = self._rpc.call("prompt", {
            "kind": "bool",
            "label": label,
            "default": bool(default),
        })
        # Confirm always returns a bool — the parent enforces this even
        # in skip mode (returns the default). Defensive cast.
        return bool(result) if result is not None else bool(default)

    def choice(self, label: str, options: list) -> Optional[str]:
        """Menu prompt. ``options`` is a list of ``(key, label)`` tuples
        OR plain strings. Returns the chosen key (first tuple element),
        or the string itself if plain strings were passed.

        Per the manager's sketch sign-off: returning the key (not the
        label) matches how authors mentally model "which option did
        they pick" — labels are display-only.
        """
        self._require_setup_mode("choice")
        # Normalize to (key, label) tuples on the wire so the parent's
        # handler doesn't need to repeat this logic.
        normalized: list = []
        for opt in options:
            if isinstance(opt, (list, tuple)) and len(opt) == 2:
                normalized.append([str(opt[0]), str(opt[1])])
            else:
                normalized.append([str(opt), str(opt)])
        return self._rpc.call("prompt", {
            "kind": "choice",
            "label": label,
            "options": normalized,
        })

    # ── config writes ──────────────────────────────────────────────

    def set_config(self, name: str, value: Any) -> None:
        """Persist a config value. Updates the local snapshot too, so
        a subsequent ``get_config(name)`` in the same setup() call sees
        the new value (write-through).

        Per the manager's sign-off: the parent's file is canonical;
        the subprocess sees a consistent view that includes its own
        writes. A mid-setup crash leaves the file with whatever was
        written up to the crash — same UX as Tier 1's "Esc halfway."

        Forbidden in validate mode (validate is read-only).
        """
        self._require_setup_mode("set_config")
        self._rpc.call("set_config", {"name": name, "value": value})
        # Local-snapshot write-through. Done after the RPC succeeds so
        # we don't end up with a snapshot that's ahead of the file.
        self._config[name] = value

    # ── downloads ──────────────────────────────────────────────────

    def download(self, url: str, destination: Any, *,
                 description: Optional[str] = None,
                 size_hint: Optional[str] = None,
                 extract: bool = False,
                 sha256: Optional[str] = None) -> Path:
        """Download a URL to ``destination``.

        Routes through the ``download`` RPC; the parent owns the
        progress UI, the cache, and the network I/O. The subprocess
        just blocks until the parent reports completion.

        Returns the destination path. Raises ``RuntimeError`` (a
        ``SetupRPCError``) on download / verification / extraction
        failure — author code can ``try/except RuntimeError`` to
        handle it gracefully.
        """
        self._require_setup_mode("download")
        result = self._rpc.call("download", {
            "url": url,
            "destination": str(destination),
            "description": description,
            "size_hint": size_hint,
            "extract": bool(extract),
            "sha256": sha256,
        })
        return Path(result) if result else Path(str(destination))
