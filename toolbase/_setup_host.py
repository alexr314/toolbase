"""
Per-toolkit subprocess host for Phase 3C-2 setup.

Mirrors ``_toolkit_host.py``'s shape but for the setup lifecycle
instead of the serve lifecycle:

- Runs *inside* a toolkit's Python interpreter (its own venv/conda env)
  so ``setup.py`` can import the toolkit's declared dependencies.
- Talks to the parent process over line-mode JSON-RPC on stdin/stdout
  (see ``toolbase/setup/_rpc.py``). This is **not** MCP and is not
  the same protocol the serve-time host speaks.
- Imports only stdlib + a minimal slice of ``toolbase.setup`` (the
  RPC primitives and ``SetupContext``). The parent ships those into
  the toolkit env via ``PYTHONPATH``, the same way ``_toolkit_host.py``
  is reachable from the toolkit env today.

The lifecycle:

1. Parse ``--toolkit-dir``, ``--name`` from argv.
2. Open the line-mode RPC channel over stdin/stdout.
3. Send a ``hello`` message announcing protocol version + which
   functions ``setup.py`` exports (``setup`` and/or ``validate``).
4. Wait for ``go`` from parent. ``go`` carries the mode (``setup``
   or ``validate``), prompt mode, current config snapshot, and the
   four standard paths.
5. Construct ``SetupContext`` from the ``go`` payload.
6. Invoke ``setup.py::setup(ctx)`` or ``setup.py::validate(ctx)``.
7. Send ``done`` with the function's return value (cast to bool) and
   any traceback if it raised.
8. Exit.

Errors during setup.py loading or invocation are captured as a
``done`` message with ``result=false`` + a traceback. The runner on
the parent side renders the one-line summary and writes the full
traceback to ``~/.toolbase/logs/setup-<toolkit>-<date>.log`` per
the spec's error-handling rules.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Optional


def _load_setup_module(toolkit_dir: Path):
    """Import ``<toolkit_dir>/setup.py`` without polluting sys.path.

    Same discipline as ``_toolkit_host.py::_import_tools_package``:
    use an explicit ``spec_from_file_location`` so the toolkit's
    sibling directories don't shadow installed packages. ``setup.py``
    sits at toolkit root; tools live in ``tools/``. The two are
    independent imports.

    Returns the loaded module, or None if no setup.py exists.
    """
    setup_file = toolkit_dir / "setup.py"
    if not setup_file.exists():
        return None
    # Use a unique module name so we don't collide with anything else
    # the toolkit might import that happens to be called 'setup'.
    spec = importlib.util.spec_from_file_location(
        "_toolbase_toolkit_setup",
        str(setup_file),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build module spec for {setup_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_toolbase_toolkit_setup"] = module
    spec.loader.exec_module(module)
    return module


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m toolbase._setup_host",
        description="Per-toolkit subprocess host for toolbase setup.",
    )
    parser.add_argument(
        "--toolkit-dir", required=True, type=Path,
        help="Path to the installed toolkit directory.",
    )
    parser.add_argument(
        "--name", required=True,
        help="Toolkit name (used in error messages).",
    )
    args = parser.parse_args(argv)

    # Import the RPC primitives and SetupContext from the parent's
    # PYTHONPATH-injected toolbase slice.
    try:
        from toolbase.setup import _rpc
        from toolbase.setup.context import SetupContext, _SetupContextRPC
    except ImportError as e:  # pragma: no cover — defensive
        sys.stderr.write(
            f"setup-host: failed to import toolbase.setup: {e}\n"
            "(parent should have set PYTHONPATH; this is a bug in the "
            "orchestrator's spawn wiring)\n"
        )
        return 2

    # The line-mode helpers expect text streams. stdout/stdin should
    # already be text-mode under Python 3, but force UTF-8 to match the
    # parent's expectations exactly (the parent uses ensure_ascii=False
    # when emitting messages with potential Unicode, so the channel
    # must be UTF-8 capable end-to-end).
    rx = sys.stdin
    tx = sys.stdout
    # Reconfigure for UTF-8 if possible. ``reconfigure`` exists on
    # Python 3.7+; safe to call multiple times.
    try:
        rx.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        tx.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, io.UnsupportedOperation):
        pass

    # Step 1: try to load setup.py. We hold any load-time exception
    # to be reported as the eventual ``done``, not as a pre-handshake
    # error — the protocol is "always send hello first," and the
    # parent's pump treats a pre-hello ``done`` as a protocol violation.
    setup_module = None
    load_traceback: Optional[str] = None
    try:
        setup_module = _load_setup_module(args.toolkit_dir)
    except Exception:
        load_traceback = traceback.format_exc()

    has_setup = bool(setup_module and callable(getattr(setup_module, "setup", None)))
    has_validate = bool(setup_module and callable(getattr(setup_module, "validate", None)))

    # Step 2: send hello. If the setup.py couldn't even load, we
    # advertise has_setup=has_validate=False AND include the load
    # traceback in ``load_error``. The parent uses load_error to
    # surface the actual root cause (syntax error, ImportError) to
    # the user rather than the misleading "setup() not defined."
    try:
        _rpc.write_message(tx, _rpc.make_hello(
            has_setup=has_setup,
            has_validate=has_validate,
            load_error=load_traceback,
        ))
    except Exception:
        return 4

    # Step 3: wait for go. The parent may decline to send 'go' if it
    # decided based on the hello alone that there's nothing to do
    # (e.g., validate-mode + has_validate=False); EOF here is fine.
    msg = _rpc.read_message(rx)
    if msg is None:
        # Parent disconnected without sending go — clean exit.
        return 0
    if msg.method != "go":
        try:
            _rpc.write_message(tx, _rpc.make_done(
                result=False,
                traceback_str=f"expected 'go' message, got {msg.method!r}",
            ))
        except Exception:
            pass
        return 6
    go_params = msg.params

    mode = go_params.get("mode", "setup")
    prompt_mode = go_params.get("prompt_mode", "ask")
    config_snapshot = go_params.get("config") or {}
    toolkit_path = Path(go_params.get("toolkit_path") or args.toolkit_dir)
    data_dir = Path(go_params.get("data_dir") or "")
    cache_dir = Path(go_params.get("cache_dir") or "")
    config_path = Path(go_params.get("config_path") or "")

    # Step 4: build the RPC client and ctx.
    rpc_client = _SetupContextRPC(rx=rx, tx=tx)
    ctx = SetupContext(
        rpc=rpc_client,
        mode=mode,
        prompt_mode=prompt_mode,
        config_snapshot=config_snapshot,
        toolkit_path=toolkit_path,
        data_dir=data_dir,
        cache_dir=cache_dir,
        config_path=config_path,
    )

    # Step 5: invoke the requested function.
    if mode == "validate":
        if not has_validate:
            # No validate(ctx) defined → trivially passes. The
            # canonical "no setup_script means no validate" path goes
            # through the orchestrator's _resolve_state_config, not
            # through here, but defending against the edge is cheap.
            _rpc.write_message(tx, _rpc.make_done(result=True))
            return 0
        target_fn = setup_module.validate
        target_label = "validate"
    else:
        if not has_setup:
            _rpc.write_message(tx, _rpc.make_done(
                result=False,
                traceback_str=(
                    f"setup.py at {args.toolkit_dir / 'setup.py'} does not "
                    "define `setup(ctx)`. See "
                    "https://toolbase-ai.com/docs/configuration#setup-script "
                    "(or tb-package/docs/SETUP_SYSTEM_SPEC.md §'Tier 2 — "
                    "script' until that page lands)."
                ),
            ))
            return 0
        target_fn = setup_module.setup
        target_label = "setup"

    try:
        result = target_fn(ctx)
    except Exception:
        tb = traceback.format_exc()
        try:
            _rpc.write_message(tx, _rpc.make_done(
                result=False,
                traceback_str=tb,
            ))
        except Exception:
            pass
        return 0  # not 7 — we communicated cleanly; the *result* is failure.

    # Allow setup/validate to return None (treat as success) for
    # author-friendly ergonomics. Authors who explicitly return False
    # signal failure; everyone else just returns.
    success = True if result is None else bool(result)

    try:
        _rpc.write_message(tx, _rpc.make_done(result=success))
    except Exception:
        return 8

    return 0


if __name__ == "__main__":
    sys.exit(main())
