"""
Parent-side runner for Phase 3C-2 setup.

Spawns ``toolbase._setup_host`` inside the toolkit's own venv,
drives the JSON-RPC conversation, surfaces ``ctx.*`` calls to the
user (terminal output, prompts, config writes, downloads), and
collects the final ``setup(ctx)``/``validate(ctx)`` result.

The split:

- **In this module:** spawn / handshake / pump / cleanup. The pump is
  a switch on RPC method names — each method has a small handler.
- **In the toolkit subprocess** (``toolbase._setup_host``): import
  ``setup.py``, build a ``SetupContext``, invoke ``setup(ctx)`` or
  ``validate(ctx)``.

Day 1 ships:

- The protocol scaffolding (handshake, mode selection, done).
- Log-routing handlers (``info`` / ``warn`` / ``error`` / ``hint`` /
  ``success`` → Rich console).
- The minimal ``run_setup_script(...)`` and ``validate_setup_script(...)``
  entry points used by ``toolbase setup`` and the orchestrator's
  serve-startup validate (Day 5 wires the latter in for real).

Day 2 adds the prompt + ``set_config`` handlers. Day 3 adds the
download handler with progress-notification streaming. The pump
structure here doesn't need to change to absorb those — each new
method is an additional handler entry, plus the notification flow
for downloads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import _rpc
from .schema import ConfigSchema
from .storage import (
    config_path as _config_path_for,
    load_config,
    set_config_value,
)


# ── public result type ────────────────────────────────────────────────


class SetupResult:
    """Outcome of a ``setup(ctx)`` or ``validate(ctx)`` invocation.

    Three states:
    - ``ok=True``: function returned truthy.
    - ``ok=False, traceback=None``: function returned falsy
      (``False``, ``0``, ``""`` etc.) — clean refusal, not a crash.
      The author signalled "not ready"; user should fix and re-run.
    - ``ok=False, traceback=<str>``: function raised. The traceback is
      the full Python traceback; the runner has already written it to
      a log file at ``log_path``.

    A fourth case — the subprocess died / never sent ``done`` — is
    represented as ``ok=False, traceback=<message about subprocess
    exit>`` for caller simplicity.
    """

    def __init__(
        self,
        *,
        ok: bool,
        traceback: Optional[str] = None,
        log_path: Optional[Path] = None,
        message: Optional[str] = None,
    ):
        self.ok = ok
        self.traceback = traceback
        self.log_path = log_path
        self.message = message

    def __repr__(self) -> str:
        return (
            f"SetupResult(ok={self.ok}, "
            f"has_traceback={self.traceback is not None}, "
            f"log_path={self.log_path}, message={self.message!r})"
        )


# ── handler types ─────────────────────────────────────────────────────


# A handler takes the RPC params dict and returns the response payload.
# It can raise; the pump catches and converts to an error response.
RPCHandler = Callable[[Dict[str, Any]], Any]


# ── default handlers (Day 1: log routing only) ─────────────────────────


def _default_log_handler(console_print: Callable[[str], None]) -> RPCHandler:
    """Build a handler for ``log`` RPCs.

    The handler prints to a Rich console using level-appropriate
    styling. ``console_print`` is the actual print function (passed
    in so tests can swap it for a list-append).

    Levels: ``info``, ``warn``, ``error``, ``hint``, ``success``.
    Anything else is treated as ``info`` with the unknown level shown
    in brackets so authors can debug their typo.
    """
    style_map = {
        "info":    ("[cyan]", "[/cyan]"),
        "warn":    ("[yellow]", "[/yellow]"),
        "error":   ("[red]", "[/red]"),
        "hint":    ("[dim]", "[/dim]"),
        "success": ("[green]", "[/green]"),
    }

    def handle(params: Dict[str, Any]) -> Any:
        level = str(params.get("level") or "info")
        message = str(params.get("message") or "")
        if level in style_map:
            open_tag, close_tag = style_map[level]
            console_print(f"{open_tag}{message}{close_tag}")
        else:
            console_print(f"[{level}] {message}")
        return None  # logs return None; the subprocess just needs ack

    return handle


# ── prompt handler (Day 2) ─────────────────────────────────────────────


def _default_prompt_handler(prompt_mode: str) -> RPCHandler:
    """Build a handler for the ``prompt`` RPC.

    Honors ``prompt_mode`` from the four modes the CLI uses elsewhere:

    - ``ask``  — interactive ``click.prompt`` / ``click.confirm``.
    - ``yes``  — auto-accept (returns ``True`` for confirm, ``default`` or
      None for value prompts that have no default — the user is implicitly
      saying "use whatever you'd default to").
    - ``no``   — auto-decline (returns ``False`` for confirm, ``None`` for
      value prompts).
    - ``skip`` — non-interactive, use defaults; if a required value
      prompt has no default, return ``None`` (subprocess can decide
      whether that's OK; the spec says optional fields stay None and
      required fields ultimately get the NEEDS_VALUE_SENTINEL via the
      file).

    Author-side: the prompt method returns ``None`` to signal "user
    skipped this." Required fields handle that by falling back to the
    sentinel; optional fields just stay at their declared default.
    """
    import click

    def handle(params: Dict[str, Any]) -> Any:
        kind = str(params.get("kind") or "string")
        label = str(params.get("label") or "")
        default = params.get("default")

        # Confirm prompts have separate semantics — return a bool
        # always, never None.
        if kind == "bool":
            return _handle_confirm(prompt_mode, label, default)

        # Choice prompts have their own UX (menu).
        if kind == "choice":
            options = params.get("options") or []
            return _handle_choice(prompt_mode, label, options)

        # Value prompts: string / secret / path / int / float.
        return _handle_value_prompt(prompt_mode, kind, label, default, params)

    return handle


def _handle_confirm(mode: str, label: str, default: Any) -> bool:
    """Confirm dispatch."""
    import click
    default_bool = bool(default) if default is not None else False
    if mode == "yes":
        return True
    if mode == "no":
        return False
    if mode == "skip":
        return default_bool
    return click.confirm(label, default=default_bool)


def _handle_choice(mode: str, label: str, options: List[Any]) -> Optional[str]:
    """Choice dispatch.

    ``options`` is a list of ``[key, label]`` pairs (normalized
    subprocess-side). Renders a numbered menu; user picks by number or
    by typing the key.
    """
    import click
    if not options:
        # Shouldn't happen — the subprocess always provides ≥1 — but be
        # defensive against a bug elsewhere.
        return None

    if mode == "yes":
        # Default to first option in yes-mode.
        return options[0][0]
    if mode == "no":
        return None
    if mode == "skip":
        # No "default" concept for choice — pick first if author hasn't
        # given us another signal.
        return options[0][0]

    # Interactive: render menu, prompt for selection.
    click.echo(label)
    for i, (_key, opt_label) in enumerate(options, start=1):
        click.echo(f"  {i}) {opt_label}")
    while True:
        raw = click.prompt(
            "Choose by number or key",
            default="1",
            show_default=False,
        )
        # Number?
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
            click.echo(f"  not a valid number (1-{len(options)})")
            continue
        # Key match?
        for key, _ in options:
            if raw == key:
                return key
        click.echo("  not a valid choice; try again")


def _handle_value_prompt(
    mode: str, kind: str, label: str, default: Any, params: Dict[str, Any],
) -> Any:
    """String/secret/path/int/float dispatch.

    Returns the parsed value, or None if the user skipped or no
    default exists in non-interactive mode.

    Validation (min/max bounds, must_exist) is enforced for ``ask``
    mode with retry; in non-ask modes we trust the default (which the
    schema layer already validated).
    """
    import click

    # Non-ask paths: shortcut to default-or-None.
    if mode in ("yes", "skip", "no"):
        if default is None:
            return None
        return _coerce_for_kind(kind, default)

    # ask mode: real interactive prompt with validation retry.
    hide = (kind == "secret")
    return _prompt_with_retry(kind, label, default, params, hide_input=hide)


def _coerce_for_kind(kind: str, raw: Any) -> Any:
    """Best-effort coercion of a default value to the prompt kind.

    The subprocess will receive whatever shape we emit, so it has to
    be JSON-encodable and match the kind's expectation. Defaults
    declared in YAML come through as Python primitives already; this
    is mostly a safety net.
    """
    if kind == "string" or kind == "secret":
        return str(raw)
    if kind == "path":
        return str(raw)  # Path() is built subprocess-side
    if kind == "int":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if kind == "float":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return raw


def _prompt_with_retry(
    kind: str, label: str, default: Any, params: Dict[str, Any], *,
    hide_input: bool,
) -> Any:
    """Interactive prompt with up to 3 retries on validation failure.

    Same retry budget as Tier 1's ``run_install_setup`` (which uses
    the prompts.py module). After 3 failed attempts, return None —
    the user can fix things via ``toolbase config edit`` or by
    re-running setup.
    """
    import click

    retries = 3
    show_default = default is not None

    for _attempt in range(retries):
        try:
            raw = click.prompt(
                label,
                default=str(default) if default is not None else None,
                hide_input=hide_input,
                show_default=show_default,
            )
        except click.exceptions.Abort:
            # User pressed Ctrl-C / Esc. Treat as "skip this prompt."
            return None

        if raw is None or raw == "":
            if default is not None:
                return _coerce_for_kind(kind, default)
            click.echo("  this field requires a value; try again")
            continue

        # Type-specific validation.
        try:
            if kind in ("string", "secret"):
                return raw
            if kind == "path":
                # Tilde expansion + must_exist check.
                from pathlib import Path as _P
                p = _P(raw).expanduser()
                if params.get("must_exist") and not p.exists():
                    click.echo(f"  path does not exist: {p}; try again")
                    continue
                return str(p)
            if kind == "int":
                v = int(raw)
                lo, hi = params.get("min"), params.get("max")
                if lo is not None and v < lo:
                    click.echo(f"  must be >= {lo}; try again")
                    continue
                if hi is not None and v > hi:
                    click.echo(f"  must be <= {hi}; try again")
                    continue
                return v
            if kind == "float":
                v = float(raw)
                lo, hi = params.get("min"), params.get("max")
                if lo is not None and v < lo:
                    click.echo(f"  must be >= {lo}; try again")
                    continue
                if hi is not None and v > hi:
                    click.echo(f"  must be <= {hi}; try again")
                    continue
                return v
        except ValueError as e:
            click.echo(f"  not a valid {kind}: {e}; try again")
            continue

    # Used all retries.
    return None


# ── download handler (Day 3) ───────────────────────────────────────────


def _default_download_handler(
    cache_dir: Optional[Path] = None,
    *,
    use_rich_progress: bool = True,
) -> RPCHandler:
    """Build a handler for ``download`` RPCs.

    Owns the Rich progress bar (parent has the terminal). Streams
    download → SHA256 verify → optional extract; returns the final
    destination path back to the subprocess as the RPC result.

    Errors propagate as ``SetupRPCError`` so the toolkit author's
    ``ctx.download(...)`` raises ``RuntimeError`` they can catch.
    """
    from .downloads import download as _download, DownloadError

    def handle(params: Dict[str, Any]) -> Any:
        url = params.get("url")
        destination = params.get("destination")
        if not url or not destination:
            raise _rpc.SetupRPCError(
                "invalid_params",
                "download: 'url' and 'destination' are required",
            )

        sha256 = params.get("sha256")
        extract = bool(params.get("extract"))
        description = params.get("description") or f"Downloading {url}"
        size_hint = params.get("size_hint")

        # Build progress callback. Rich Progress is the default UX;
        # tests pass use_rich_progress=False to skip the live bar.
        if use_rich_progress:
            try:
                from rich.console import Console
                from rich.progress import (
                    Progress, BarColumn, DownloadColumn,
                    TextColumn, TimeRemainingColumn, TransferSpeedColumn,
                )
            except ImportError:  # pragma: no cover — Rich is a hard dep
                use_local_progress = False
                progress_callback: Optional[Callable] = None
            else:
                use_local_progress = True
                progress_callback = None
        else:
            use_local_progress = False
            progress_callback = None

        try:
            if use_local_progress:
                console = Console(stderr=False)
                with Progress(
                    TextColumn("[bold]{task.description}"),
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task_id = progress.add_task(description, total=None)

                    def update_progress(
                        *, bytes_so_far: int,
                        total_bytes: Optional[int],
                        stage: str,
                    ) -> None:
                        if stage == "download":
                            if total_bytes is not None:
                                progress.update(
                                    task_id, completed=bytes_so_far,
                                    total=total_bytes,
                                )
                            else:
                                progress.update(
                                    task_id, completed=bytes_so_far,
                                )
                        elif stage == "extract":
                            progress.update(
                                task_id,
                                description=f"Extracting {description}",
                            )
                        elif stage == "verify":
                            progress.update(
                                task_id,
                                description=f"Verifying {description}",
                            )

                    final = _download(
                        url, Path(destination),
                        sha256=sha256, extract=extract,
                        description=description, size_hint=size_hint,
                        on_progress=update_progress,
                        cache_dir=cache_dir,
                    )
            else:
                final = _download(
                    url, Path(destination),
                    sha256=sha256, extract=extract,
                    description=description, size_hint=size_hint,
                    on_progress=None,
                    cache_dir=cache_dir,
                )
        except DownloadError as e:
            raise _rpc.SetupRPCError(e.code, str(e))
        except Exception as e:
            raise _rpc.SetupRPCError("download_failed", str(e))

        return str(final)

    return handle


# ── set_config handler (Day 2) ─────────────────────────────────────────


def _default_set_config_handler(toolkit_name: str) -> RPCHandler:
    """Build a handler for ``set_config`` RPCs.

    Writes through to ``~/.toolbase/config/<toolkit>.yaml`` via the
    storage layer's ``set_config_value`` (atomic, comment-preserving,
    mode 0600). The subprocess holds a local snapshot it updates in
    parallel; both end up consistent at end-of-setup.
    """
    def handle(params: Dict[str, Any]) -> Any:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _rpc.SetupRPCError("invalid_params", "set_config: 'name' must be a non-empty string")
        value = params.get("value")
        # We don't validate against ConfigSchema here — Tier 2's
        # set_config is intentionally schema-less so authors can stash
        # values that aren't declared in the toolkit.yaml config:
        # block (e.g., setup.py-derived state like detected GPU
        # capabilities). The serve-time resolver only looks at fields
        # declared in the schema; extra fields in the file are
        # benign.
        set_config_value(toolkit_name, name, value)
        return None

    return handle


# ── runner ────────────────────────────────────────────────────────────


class _Runner:
    """One run of the setup conversation. Single use."""

    def __init__(
        self,
        *,
        toolkit_name: str,
        toolkit_dir: Path,
        python_exe: str,
        env: Dict[str, str],
        mode: str,                # "setup" | "validate"
        prompt_mode: str,
        config_snapshot: Dict[str, Any],
        config_path: Path,
        data_dir: Path,
        cache_dir: Path,
        log_dir: Path,
        console_print: Callable[[str], None],
        extra_handlers: Optional[Dict[str, RPCHandler]] = None,
    ):
        self.toolkit_name = toolkit_name
        self.toolkit_dir = toolkit_dir
        self.python_exe = python_exe
        self.env = env
        self.mode = mode
        self.prompt_mode = prompt_mode
        self.config_snapshot = config_snapshot
        self.config_path = config_path
        self.data_dir = data_dir
        self.cache_dir = cache_dir
        self.log_dir = log_dir
        self.console_print = console_print

        # Build the dispatch table.
        # Day 1: ``log``. Day 2: ``prompt``, ``set_config``.
        # Day 3: ``download``.
        # In validate mode we don't wire ``prompt`` / ``set_config`` /
        # ``download`` — they're forbidden client-side, but if the
        # subprocess somehow sends one anyway (bug in author code,
        # stale ctx?), the ``unknown_method`` error is clearer than a
        # silent skip.
        self.handlers: Dict[str, RPCHandler] = {
            "log": _default_log_handler(console_print),
        }
        if mode == "setup":
            self.handlers["prompt"] = _default_prompt_handler(prompt_mode)
            self.handlers["set_config"] = _default_set_config_handler(toolkit_name)
            self.handlers["download"] = _default_download_handler()
        if extra_handlers:
            self.handlers.update(extra_handlers)

    def run(self) -> SetupResult:
        """Spawn, drive, return the result.

        Errors during spawn or handshake produce an immediate
        ``ok=False`` with a descriptive ``message``. Errors during
        ``setup(ctx)`` / ``validate(ctx)`` are surfaced as a normal
        ``done`` message with ``traceback`` populated.
        """
        # ── spawn ──────────────────────────────────────────────────
        cmd = self._build_command()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                cwd=str(self.toolkit_dir),
                text=True,
                bufsize=1,  # line-buffered (matches _toolkit_host.py)
            )
        except FileNotFoundError as e:
            return SetupResult(
                ok=False,
                message=f"could not spawn setup-host: {e}",
            )

        try:
            return self._drive(proc)
        finally:
            self._cleanup(proc)

    # ── command + env wiring ───────────────────────────────────────

    def _build_command(self) -> List[str]:
        return [
            self.python_exe,
            "-m", "toolbase._setup_host",
            "--toolkit-dir", str(self.toolkit_dir),
            "--name", self.toolkit_name,
        ]

    # ── pump ───────────────────────────────────────────────────────

    def _drive(self, proc: subprocess.Popen) -> SetupResult:
        """Run the conversation. Returns the SetupResult."""
        # The subprocess streams are non-None because we set them as
        # PIPE in Popen above; the type checker doesn't know that.
        rx = proc.stdout
        tx = proc.stdin
        assert rx is not None and tx is not None

        # Step 1: read hello.
        try:
            hello = _rpc.read_message(rx)
        except ValueError as e:
            return self._fail_with_subprocess_diag(
                proc, f"malformed RPC line on hello: {e}",
            )
        if hello is None:
            return self._fail_with_subprocess_diag(
                proc, "subprocess exited before sending hello",
            )
        if hello.method != "hello":
            return self._fail_with_subprocess_diag(
                proc, f"expected 'hello' first, got method={hello.method!r}",
            )
        proto = hello.params.get("protocol")
        if proto != _rpc.PROTOCOL_VERSION:
            return self._fail_with_subprocess_diag(
                proc,
                f"protocol version mismatch: subprocess={proto!r}, "
                f"parent={_rpc.PROTOCOL_VERSION!r}. The toolkit's setup-host "
                "is from a different toolbase version than the parent. "
                "Reinstall the toolkit (`toolbase uninstall` + `install`).",
            )
        has_setup = bool(hello.params.get("has_setup"))
        has_validate = bool(hello.params.get("has_validate"))
        load_error = hello.params.get("load_error")

        # If setup.py failed to import, surface the root cause (syntax
        # error, ImportError, etc.) rather than the misleading "setup
        # not defined." Write the traceback to a log file and return
        # a populated SetupResult.
        if load_error:
            log_path = self._write_traceback_log(load_error)
            self._cleanup(proc)
            return SetupResult(
                ok=False,
                traceback=load_error,
                log_path=log_path,
                message=(
                    f"setup.py at this toolkit failed to load — see "
                    f"{log_path} for the full traceback."
                ),
            )

        # Sanity: if the user asked for setup/validate but setup.py
        # doesn't define it, fail loudly here rather than letting the
        # subprocess send a confusing ``done``.
        if self.mode == "setup" and not has_setup:
            return self._fail_with_subprocess_diag(
                proc,
                "setup.py at this toolkit does not define `setup(ctx)`. "
                "See https://tool-base.org/docs/configuration#setup-script.",
            )
        if self.mode == "validate" and not has_validate:
            # Missing validate(ctx) → trivially passes. (The
            # subprocess would also handle this by sending done(true);
            # short-circuit here saves a round-trip.)
            self._cleanup(proc)
            return SetupResult(ok=True)

        # Step 2: send go.
        go = _rpc.make_go(
            mode=self.mode,
            prompt_mode=self.prompt_mode,
            config=self.config_snapshot,
            toolkit_path=str(self.toolkit_dir),
            data_dir=str(self.data_dir),
            cache_dir=str(self.cache_dir),
            config_path=str(self.config_path),
        )
        try:
            _rpc.write_message(tx, go)
        except (BrokenPipeError, OSError) as e:
            return self._fail_with_subprocess_diag(
                proc, f"could not send 'go' to subprocess: {e}",
            )

        # Step 3: pump until done.
        while True:
            try:
                msg = _rpc.read_message(rx)
            except ValueError as e:
                return self._fail_with_subprocess_diag(
                    proc, f"malformed RPC line: {e}",
                )
            if msg is None:
                return self._fail_with_subprocess_diag(
                    proc, "subprocess exited before sending 'done'",
                )

            if msg.method == "done":
                # End of conversation.
                params = msg.params
                ok = bool(params.get("result"))
                tb = params.get("traceback")
                log_path = None
                if tb:
                    log_path = self._write_traceback_log(tb)
                return SetupResult(ok=ok, traceback=tb, log_path=log_path)

            if msg.is_request:
                # An RPC call from the subprocess; dispatch.
                handler = self.handlers.get(msg.method or "")
                req_id = msg.id
                if req_id is None:
                    # Should not happen for a request, but be defensive.
                    continue
                if handler is None:
                    response = _rpc.make_error_response(
                        req_id,
                        "unknown_method",
                        f"setup-host requested method {msg.method!r} "
                        "which the parent does not implement. "
                        "Likely an toolbase version skew.",
                    )
                else:
                    try:
                        result = handler(msg.params)
                        response = _rpc.make_response(req_id, result)
                    except _rpc.SetupRPCError as e:
                        response = _rpc.make_error_response(
                            req_id, e.code, e.rpc_message,
                        )
                    except Exception as e:
                        response = _rpc.make_error_response(
                            req_id, "handler_exception", str(e),
                        )
                try:
                    _rpc.write_message(tx, response)
                except (BrokenPipeError, OSError):
                    return self._fail_with_subprocess_diag(
                        proc, "subprocess pipe closed while sending response",
                    )
                continue

            # Notifications from subprocess to parent are not part of
            # the protocol today (the parent sends notifications, not
            # the subprocess). Ignore unknown shapes rather than
            # crashing on them.
            continue

    # ── cleanup + diagnostics ──────────────────────────────────────

    def _cleanup(self, proc: subprocess.Popen) -> None:
        """Best-effort tear-down.

        Closes stdin to signal EOF (in case the subprocess is reading);
        waits briefly; SIGTERMs if needed; SIGKILLs as last resort.
        """
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass

        # If already exited, nothing more to do.
        if proc.poll() is not None:
            return

        # Wait briefly for clean exit.
        try:
            proc.wait(timeout=2.0)
            return
        except subprocess.TimeoutExpired:
            pass

        proc.terminate()
        try:
            proc.wait(timeout=2.0)
            return
        except subprocess.TimeoutExpired:
            pass

        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass

    def _fail_with_subprocess_diag(
        self,
        proc: subprocess.Popen,
        message: str,
    ) -> SetupResult:
        """Build a SetupResult capturing subprocess stderr.

        When the conversation goes sideways we want to give the user
        whatever the subprocess printed to stderr (Python traceback
        from import error, missing-dependency message, etc.). Wait
        briefly for the subprocess to exit, drain stderr, package it
        up.
        """
        try:
            # Drain remaining streams so wait() doesn't hang.
            self._cleanup(proc)
            stderr = ""
            if proc.stderr:
                try:
                    stderr = proc.stderr.read() or ""
                except Exception:
                    stderr = ""
        except Exception:
            stderr = ""

        full = message
        if stderr:
            full += "\n--- subprocess stderr ---\n" + stderr.rstrip()

        log_path = self._write_traceback_log(full) if stderr else None
        return SetupResult(
            ok=False,
            message=message,
            traceback=stderr or None,
            log_path=log_path,
        )

    def _write_traceback_log(self, traceback_str: str) -> Path:
        """Write the full traceback to a per-run log file.

        Per the spec error-handling section: show the user a one-line
        summary; full log goes to disk. Format:
        ``~/.toolbase/logs/setup-<toolkit>-<YYYYMMDD-HHMMSS>.log``.
        """
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.log_dir / f"setup-{self.toolkit_name}-{stamp}.log"
        try:
            path.write_text(traceback_str, encoding="utf-8")
        except Exception:
            # If we can't even write the log, don't crash the runner;
            # the message in SetupResult already carries the gist.
            pass
        return path


# ── public entry points ───────────────────────────────────────────────


def _read_toolkit_meta(toolkit_dir: Path) -> Dict[str, Any]:
    """Read ``.tb_meta.json`` from an installed toolkit.

    Same file the serve orchestrator reads. Carries ``python_path``
    (for venv toolkits) and ``env_name`` (for conda toolkits).
    """
    from ..envs.cache import LEGACY_META_FILE
    meta_file = toolkit_dir / LEGACY_META_FILE
    if not meta_file.exists():
        raise RuntimeError(
            f"installed toolkit at {toolkit_dir} has no {LEGACY_META_FILE} — "
            "reinstall to fix"
        )
    return json.loads(meta_file.read_text())


def _build_subprocess_env() -> Dict[str, str]:
    """Build the env for the setup-host subprocess.

    Same PYTHONPATH discipline as the serve orchestrator: the toolkit
    venv has ``orchestral-ai`` and ``mcp`` but **not** toolbase, so
    we point PYTHONPATH at the orchestrator's ``toolbase/`` parent
    so ``python -m toolbase._setup_host`` works.
    """
    env = os.environ.copy()
    import toolbase as _sk
    pkg_parent = str(Path(_sk.__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        pkg_parent + (os.pathsep + existing if existing else "")
    )
    env["PYTHONUNBUFFERED"] = "1"
    # Sentinel — gives the subprocess a way to assert it's running
    # under the setup-host context if anything ever needs to.
    env["TOOLBASE_SETUP_HOST"] = "1"
    return env


def _resolve_python_exe(meta: Dict[str, Any], toolkit_name: str) -> str:
    env_type = meta.get("environment", "venv")
    if env_type == "venv":
        py = meta.get("python_path")
        if not py:
            raise RuntimeError(
                f"venv toolkit {toolkit_name!r}: .tb_meta.json missing "
                "python_path"
            )
        return py
    # Conda support deferred — same constraints as the serve flow.
    raise RuntimeError(
        f"toolkit {toolkit_name!r}: setup runner does not yet support "
        f"environment={env_type!r} (only venv for now). "
        "File-canonical config can still be edited directly: "
        f"~/.toolbase/config/{toolkit_name}.yaml"
    )


def _resolve_toolkit_dir(
    toolkit_name: str,
    toolkits_dir: Optional[Path],
) -> Path:
    """Resolve where a toolkit's installed binaries live.

    Three resolution modes:

    - ``toolkits_dir`` explicitly passed → use that (test-injection
      path; matches the 0.4.x flat layout used in test fixtures).
    - ``toolkits_dir`` is None → resolve via the 0.5.0 cache layout.
      The active version is picked from the default-project manifest
      pin if there is one; otherwise the only-installed-version (if
      there's exactly one) or the highest version when multiple.

    Raises ``RuntimeError`` if the toolkit isn't installed anywhere.
    """
    if toolkits_dir is not None:
        legacy = toolkits_dir / toolkit_name
        if not legacy.exists():
            raise RuntimeError(
                f"toolkit {toolkit_name!r} is not installed at {legacy}"
            )
        return legacy

    # 0.5.0 cache walk.
    from ..envs import (
        list_versions, find_slot,
        project_manifest_path, get_pin,
    )
    from ..versioning import parse_version

    versions = list_versions(toolkit_name)
    if not versions:
        raise RuntimeError(
            f"toolkit {toolkit_name!r} is not installed (no slot in "
            "~/.toolbase/cache/)"
        )

    # Prefer the pin from the active project's manifest (Phase 3:
    # walk-upward discovery via cli._resolve_active_project_root).
    try:
        from ..cli import _resolve_active_project_root
        project_root, _source = _resolve_active_project_root()
        manifest_path = project_manifest_path(project_root)
        pin = get_pin(manifest_path, toolkit_name)
    except Exception:
        pin = None

    if pin is not None and pin.version in versions:
        chosen_version = pin.version
    elif len(versions) == 1:
        chosen_version = versions[0]
    else:
        # No pin, multiple — pick highest.
        chosen_version = sorted(
            versions,
            key=lambda v: parse_version(v) or (0, 0, 0),
            reverse=True,
        )[0]

    slot = find_slot(toolkit_name, chosen_version)
    if slot is None:
        raise RuntimeError(
            f"toolkit {toolkit_name!r} v{chosen_version} resolution "
            "failed (cache walk inconsistency)"
        )
    return slot.path


def _toolbase_dirs(toolkit_name: str) -> Tuple[Path, Path, Path]:
    """Return (data_dir, cache_dir, log_dir) for a toolkit.

    Resolves at call time from ``~/.toolbase/``.

    NB (0.5.0): the per-toolkit downloads cache used to live at
    ``~/.toolbase/cache/<toolkit>/`` but that path now belongs to
    the toolkit-binaries cache (``cache/<name>/<version>/``). The
    downloads cache moved to ``~/.toolbase/downloads/<toolkit>/``
    to avoid the namespacing collision. The setup ``_setup_validate.json``
    file is fine where it is (top-of-cache underscore-prefixed names
    are filtered out by the cache walker).
    """
    home = Path.home()
    base = home / ".toolbase"
    data_dir = base / "data" / toolkit_name
    cache_dir = base / "downloads" / toolkit_name
    log_dir = base / "logs"
    return data_dir, cache_dir, log_dir


def run_setup_script(
    toolkit_name: str,
    *,
    toolkits_dir: Optional[Path] = None,
    prompt_mode: str = "ask",
    console_print: Optional[Callable[[str], None]] = None,
    extra_handlers: Optional[Dict[str, RPCHandler]] = None,
) -> SetupResult:
    """Run a toolkit's ``setup.py::setup(ctx)`` in its own venv.

    Args:
        toolkit_name: name of the installed toolkit.
        toolkits_dir: override for ``~/.toolbase/toolkits/`` (tests).
        prompt_mode: ``"ask"``, ``"skip"``, ``"yes"``, ``"no"`` —
            propagated to the subprocess via the ``go`` message and
            consulted by Day 2's prompt handlers when they land.
        console_print: how to render ``ctx.info(...)`` etc. on the
            parent's terminal. Defaults to ``rich.console.Console().print``.
        extra_handlers: additional RPC method handlers (Day 2/3 wires
            in prompts, set_config, download).
    """
    toolkit_dir = _resolve_toolkit_dir(toolkit_name, toolkits_dir)

    meta = _read_toolkit_meta(toolkit_dir)
    python_exe = _resolve_python_exe(meta, toolkit_name)
    env = _build_subprocess_env()

    cfg_path = _config_path_for(toolkit_name)
    config_snapshot: Dict[str, Any] = {}
    if cfg_path.exists():
        config_snapshot = dict(load_config(toolkit_name))

    data_dir, cache_dir, log_dir = _toolbase_dirs(toolkit_name)

    if console_print is None:
        from rich.console import Console
        _c = Console()
        console_print = _c.print

    runner = _Runner(
        toolkit_name=toolkit_name,
        toolkit_dir=toolkit_dir,
        python_exe=python_exe,
        env=env,
        mode="setup",
        prompt_mode=prompt_mode,
        config_snapshot=config_snapshot,
        config_path=cfg_path,
        data_dir=data_dir,
        cache_dir=cache_dir,
        log_dir=log_dir,
        console_print=console_print,
        extra_handlers=extra_handlers,
    )
    return runner.run()


def validate_setup_script_cached(
    toolkit_name: str,
    *,
    toolkits_dir: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    console_print: Optional[Callable[[str], None]] = None,
) -> SetupResult:
    """Cached wrapper around ``validate_setup_script``.

    Skips the subprocess spawn when the toolkit's config file and
    ``setup.py`` haven't changed since the last validate. Hit /
    miss is keyed on mtimes; the cache stores both successful and
    failed results so a repeatedly-failing validate doesn't spawn
    a subprocess on every serve startup.

    Used by the orchestrator at serve startup. The plain
    ``validate_setup_script`` path bypasses the cache and is what
    ``toolbase setup --check`` uses.
    """
    from .validate_cache import (
        ValidateCache, default_cache_path, _mtime_or_none,
    )

    toolkit_dir = _resolve_toolkit_dir(toolkit_name, toolkits_dir)

    setup_py = toolkit_dir / "setup.py"
    cfg_path = _config_path_for(toolkit_name)
    config_mtime = _mtime_or_none(cfg_path)
    setup_py_mtime = _mtime_or_none(setup_py)

    cache = ValidateCache(cache_path or default_cache_path())
    hit = cache.get(
        toolkit_name,
        config_mtime=config_mtime,
        setup_py_mtime=setup_py_mtime,
    )
    if hit is not None:
        return SetupResult(
            ok=bool(hit["result"]),
            message=hit.get("message"),
        )

    # Miss: run the subprocess and cache the outcome.
    result = validate_setup_script(
        toolkit_name,
        toolkits_dir=toolkits_dir,
        console_print=console_print,
    )
    cache.put(
        toolkit_name,
        result=result.ok,
        message=result.message or (
            None if result.ok else "validate(ctx) returned False"
        ),
        config_mtime=config_mtime,
        setup_py_mtime=setup_py_mtime,
    )
    return result


def validate_setup_script(
    toolkit_name: str,
    *,
    toolkits_dir: Optional[Path] = None,
    console_print: Optional[Callable[[str], None]] = None,
    extra_handlers: Optional[Dict[str, RPCHandler]] = None,
) -> SetupResult:
    """Run a toolkit's ``setup.py::validate(ctx)`` in its own venv.

    Same machinery as ``run_setup_script`` but in ``mode="validate"``.
    The subprocess invokes ``validate(ctx)`` instead of ``setup(ctx)``,
    and Day 2's prompt handlers raise loudly if the author tries to
    call ``ctx.prompt`` here. The ``--check`` flag for ``toolbase
    setup`` calls this entry point.

    Latency-sensitive (called at every serve startup, must run fast);
    Day 5 adds an mtime-keyed cache around this entry point so a
    serve doesn't pay the subprocess-spawn cost when nothing has
    changed since the last validate.
    """
    toolkit_dir = _resolve_toolkit_dir(toolkit_name, toolkits_dir)

    meta = _read_toolkit_meta(toolkit_dir)
    python_exe = _resolve_python_exe(meta, toolkit_name)
    env = _build_subprocess_env()

    cfg_path = _config_path_for(toolkit_name)
    config_snapshot: Dict[str, Any] = {}
    if cfg_path.exists():
        config_snapshot = dict(load_config(toolkit_name))

    data_dir, cache_dir, log_dir = _toolbase_dirs(toolkit_name)

    if console_print is None:
        # Validate runs at serve startup; serve owns stdout for MCP.
        # Default to a stderr-only console there.
        from rich.console import Console
        _c = Console(stderr=True)
        console_print = _c.print

    runner = _Runner(
        toolkit_name=toolkit_name,
        toolkit_dir=toolkit_dir,
        python_exe=python_exe,
        env=env,
        mode="validate",
        prompt_mode="skip",  # validate never prompts
        config_snapshot=config_snapshot,
        config_path=cfg_path,
        data_dir=data_dir,
        cache_dir=cache_dir,
        log_dir=log_dir,
        console_print=console_print,
        extra_handlers=extra_handlers,
    )
    return runner.run()
