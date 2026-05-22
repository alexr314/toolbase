"""Tools for the synthetic restart-test toolkit.

Two tools:

- ``still_alive`` returns "ok" — used to confirm the host subprocess is
  healthy after a restart cycle.
- ``crash_now`` calls ``os._exit(1)`` — kills the host subprocess
  unconditionally, simulating an OOM or hard fault that bypasses normal
  exception handling.

Used by ``tests/e2e/run_restart_e2e.py``.
"""

from __future__ import annotations

import json as _json
import os
import sys

from orchestral import define_tool


@define_tool
def still_alive() -> str:
    """Return "ok" if the host process is healthy."""
    return _json.dumps({"status": "ok", "pid": os.getpid()})


@define_tool
def crash_now() -> str:
    """Crash the host subprocess by calling os._exit(1).

    The orchestrator's MCPClient will see a connection-class error (the
    httpx connection drops mid-stream); the forwarder should classify
    this as a crash and trigger the restart cycle.
    """
    # Flush stderr so the death is visible in the per-toolkit log.
    sys.stderr.write(f"[crash_now] crashing pid={os.getpid()}\n")
    sys.stderr.flush()
    os._exit(1)


TOOLS = [still_alive, crash_now]
