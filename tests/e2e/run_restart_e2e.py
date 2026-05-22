"""End-to-end test for the orchestrator's auto-restart machinery.

Drives the ``Orchestrator`` class in-process against a real per-toolkit
host subprocess that deliberately crashes itself with ``os._exit(1)``
inside one of its tools. Verifies:

1. **Crash + recovery.** A call to ``crash_now`` kills the host
   subprocess. The forwarder detects the connection-class error,
   schedules a restart, and returns the "restart scheduled" message.
   After the backoff window, the runtime is back in READY state and a
   call to ``still_alive`` returns "ok" from a fresh subprocess.

2. **Permanent failure.** Crashing the host four times exhausts the
   3-attempt restart budget. The 4th call returns the "marked failed
   for this serve session" message.

We monkeypatch ``RESTART_BACKOFF_S`` to short delays for the
permanent-failure scenario so the harness completes in seconds, not the
21 s the production schedule would take.

Why in-process: this exercises the orchestrator's restart logic end-to-end
against a real subprocess that really dies, without the cost (or
patching difficulty) of spawning ``toolbase serve`` as an outer
subprocess. The serve-stdio layer is already covered by ``run_serve_e2e.py``.

Run from the repo root with the test venv:

    python tests/e2e/run_restart_e2e.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
TOOLKIT_SRC = THIS_DIR / "test-restart-toolkit"
TOOLKIT_NAME = "tb-restart-test"

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-restart-e2e"
INSTALL_ROOT = WORK_ROOT / "toolkits"


def _setup_synthetic_install() -> Path:
    """Build a fake installed toolkit at INSTALL_ROOT/<name> without
    going through the install pipeline.

    We just copy the toolkit source into place and write a .tb_meta.json
    that points at the *current* Python interpreter — the synthetic
    toolkit has no extra deps, so the dev venv (which already has
    orchestral and mcp installed) works as the toolkit env too.
    """
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    INSTALL_ROOT.mkdir(parents=True)
    dest = INSTALL_ROOT / TOOLKIT_NAME
    shutil.copytree(TOOLKIT_SRC, dest)

    meta = {
        "name": TOOLKIT_NAME,
        "version": "0.1.0",
        "environment": "venv",
        "python_path": sys.executable,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    (dest / ".tb_meta.json").write_text(json.dumps(meta, indent=2))
    return dest


def _wait_for_state(rt, target, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if rt.state.name == target:
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    from toolbase.serve import orchestrator
    from toolbase.serve.orchestrator import (
        Orchestrator,
        ToolkitState,
    )

    _setup_synthetic_install()

    # Speed up the permanent-failure scenario: 0.05s/0.05s/0.05s instead
    # of 1s/4s/16s. Restored before exit.
    original_backoff = orchestrator.RESTART_BACKOFF_S
    orchestrator.RESTART_BACKOFF_S = (0.05, 0.05, 0.05)

    overall_rc = 0

    try:
        # ── Scenario 1: crash + recovery ────────────────────────────────
        print("=" * 60)
        print("Scenario 1: crash + recovery")
        print("=" * 60)
        # Short call timeout so the harness doesn't wait the production
        # 60 s for a crashed subprocess's connection to actually drop.
        # The unit tests already verify the crash-classification path
        # against mocked exceptions; this just keeps the e2e snappy.
        orch = Orchestrator(toolkits_dir=INSTALL_ROOT, call_timeout_s=3.0)
        orch.start()

        rt = orch._runtimes.get(TOOLKIT_NAME)
        if rt is None:
            print(f"!!! toolkit {TOOLKIT_NAME!r} did not load")
            return 1
        # 0.4.1+ no longer holds a Popen handle directly — the MCPClient
        # owns the subprocess. We use ``id(rt.mcp_client)`` as a proxy for
        # "did the subprocess get rebuilt?" — restart swaps in a brand-new
        # MCPClient instance (see orchestrator.py ~line 1089).
        print(f"  initial state: {rt.state.name} (client_id={id(rt.mcp_client)})")
        original_client_id = id(rt.mcp_client)

        # Find the forwarder via the proxy tools list. We invoke the proxy
        # directly (not through the upstream MCP server) so we don't have
        # to spin up another stdio process.
        proxies_by_name = {
            p.get_name(): p for p in orch._proxy_tools
        }
        crash_proxy = proxies_by_name[f"{TOOLKIT_NAME}__crash_now"]
        alive_proxy = proxies_by_name[f"{TOOLKIT_NAME}__still_alive"]

        # Sanity: still_alive works pre-crash.
        result = alive_proxy.execute()
        if "ok" not in result:
            print(f"!!! pre-crash still_alive returned unexpected: {result!r}")
            overall_rc = 2
        else:
            print(f"  pre-crash still_alive: {result}")

        # Trigger the crash. Returns the "restart scheduled" message.
        crash_msg = crash_proxy.execute()
        print(f"  crash_now message: {crash_msg!r}")
        if "restart scheduled" not in crash_msg.lower() \
                and "restart in progress" not in crash_msg.lower():
            print(f"!!! expected 'restart scheduled' / 'restart in progress' message")
            overall_rc = 2

        # Wait for the restart thread to complete.
        if not _wait_for_state(rt, "READY", timeout_s=10.0):
            print(f"!!! runtime did not return to READY after restart "
                  f"(state={rt.state.name})")
            overall_rc = 3
        else:
            print(f"  recovered: state={rt.state.name} "
                  f"new client_id={id(rt.mcp_client)}")
            if id(rt.mcp_client) == original_client_id:
                print("!!! mcp_client unchanged — was the subprocess actually restarted?")
                overall_rc = 4

        # Confirm fresh subprocess responds correctly.
        result = alive_proxy.execute()
        print(f"  post-crash still_alive: {result}")
        if "ok" not in result:
            print(f"!!! post-crash still_alive returned unexpected: {result!r}")
            overall_rc = 5

        orch.shutdown()

        if overall_rc != 0:
            return overall_rc

        # ── Scenario 2: permanent failure after 3 retries ───────────────
        print()
        print("=" * 60)
        print("Scenario 2: permanent failure (4 crashes in a row)")
        print("=" * 60)
        orch = Orchestrator(toolkits_dir=INSTALL_ROOT, call_timeout_s=3.0)
        orch.start()

        rt = orch._runtimes.get(TOOLKIT_NAME)
        if rt is None:
            print(f"!!! toolkit {TOOLKIT_NAME!r} did not load (scenario 2)")
            orch.shutdown()
            return 6

        proxies_by_name = {p.get_name(): p for p in orch._proxy_tools}
        crash_proxy = proxies_by_name[f"{TOOLKIT_NAME}__crash_now"]

        # Crash four times. After each of the first three crashes, the
        # restart thread should bring the subprocess back up; the fourth
        # crash exhausts the budget and the runtime ends up FAILED.
        last_msg = ""
        for i in range(1, 5):
            # Wait for any in-flight restart from the previous iteration.
            for _ in range(200):  # up to 10s
                if rt.state == ToolkitState.READY:
                    break
                if rt.state == ToolkitState.FAILED:
                    break
                time.sleep(0.05)

            msg = crash_proxy.execute()
            print(f"  crash #{i}: state_before≈READY → message: {msg!r}")
            last_msg = msg

            # Give the restart thread a moment to either restart-and-ready
            # the toolkit, or (on the 4th crash) mark it FAILED.
            time.sleep(0.5)

        if rt.state != ToolkitState.FAILED:
            print(f"!!! expected FAILED after 4 crashes; got {rt.state.name}")
            orch.shutdown()
            return 7
        print(f"  runtime state: {rt.state.name} (attempts={rt.restart_attempts})")

        # A fifth call should now get the permanent-failure message.
        permanent_msg = crash_proxy.execute()
        print(f"  post-failure message: {permanent_msg!r}")
        if "marked failed" not in permanent_msg.lower():
            print(f"!!! expected 'marked failed' message; got {permanent_msg!r}")
            orch.shutdown()
            return 8

        orch.shutdown()

        # ── Tail of serve.log for context ──────────────────────────────
        print()
        from toolbase.config import LOGS_DIR
        serve_log = LOGS_DIR / "serve.log"
        if serve_log.exists():
            print("--- last 40 lines of serve.log ---")
            for line in serve_log.read_text().splitlines()[-40:]:
                print(line)

        print()
        print("✓ restart e2e passed")
        return 0

    finally:
        orchestrator.RESTART_BACKOFF_S = original_backoff


if __name__ == "__main__":
    sys.exit(main())
