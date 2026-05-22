"""End-to-end test for ``toolbase serve``.

Drives ``toolbase serve`` as a subprocess and connects an MCP client to
it over stdio. Exercises the full serve pipeline (toolkit discovery →
per-toolkit subprocess spawn → handshake → MCPClient HTTP loopback →
proxy tools → upstream MCPServer stdio) end-to-end against the synthetic
toolkit installed by ``run_install_e2e.py``.

Prerequisite: run ``run_install_e2e.py`` first to populate the synthetic
toolkit at ``$TMPDIR/tb-e2e/install-root/tb-e2e-test/``.

Run from the repo root with the test venv:

    python tests/e2e/run_serve_e2e.py

Discovers the ``toolbase`` binary via ``shutil.which``. Make sure the
venv where ``toolbase`` is installed is on PATH.

NOTE on Orchestral's stdio MCPClient: this client respawns the server
subprocess on every ``call_tool`` (it's stateless-per-call by design;
see orchestral/mcp/client.py). For this test that's fine — each call is
independent and the orchestrator is cheap-ish to spin up. In production,
Claude Code maintains a persistent stdio session, so it pays the
orchestrator startup cost only once per conversation.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


WORK_ROOT = Path(tempfile.gettempdir()) / "tb-e2e"
# Hardened to the 0.5.0 cache layout: cache/<name>/<version>/.
FAKE_HOME_FROM_INSTALL = WORK_ROOT / "fake-home"
INSTALL_CACHE = FAKE_HOME_FROM_INSTALL / ".toolbase" / "cache"
TOOLKIT_NAME = "tb-e2e-test"


def main() -> int:
    # Find any installed version under the cache.
    name_dir = INSTALL_CACHE / TOOLKIT_NAME
    if not name_dir.exists():
        print(f"!!! synthetic toolkit cache dir missing at {name_dir}")
        print("    Run run_install_e2e.py first.")
        return 1
    version_dirs = [p for p in name_dir.iterdir() if p.is_dir()]
    if not version_dirs:
        print(f"!!! no version slot under {name_dir}")
        print("    Run run_install_e2e.py first.")
        return 1

    toolbase_bin = shutil.which("toolbase")
    if toolbase_bin is None:
        print(
            "!!! could not find `toolbase` on PATH. Activate the dev venv "
            "(or set PATH) so this script can launch the orchestrator.",
        )
        return 1

    # Serve will discover the toolkit in the cache, but it also needs the
    # default-project manifest's pin to resolve which version. The install
    # harness wrote both — we just point HOME at the same fake-home.
    fake_home = FAKE_HOME_FROM_INSTALL

    print(f"HOME redirected to {fake_home}")
    print(f"toolkits visible: {[p.name for p in INSTALL_CACHE.iterdir()]}")
    print(f"using toolbase at: {toolbase_bin}")
    print()

    # Build the env the MCP client will pass to its subprocess.
    sub_env = {
        "HOME": str(fake_home),
        # PATH is required because conda mode (if exercised) shells out
        # to `conda run`. Doesn't hurt for the venv-mode synthetic toolkit.
        "PATH": os.environ.get("PATH", ""),
    }

    from orchestral.mcp import MCPClient

    client = MCPClient(
        server_command=[toolbase_bin, "serve"],
        env=sub_env,
    )

    print("Connecting via stdio MCPClient...")
    t0 = time.monotonic()
    client.connect()
    print(f"Connected in {time.monotonic() - t0:.2f}s")

    print("\nTool list:")
    for d in client.get_tool_definitions():
        print(f"  - {d['name']}: {d.get('description', '')[:60]}")

    print(f"\nCalling {TOOLKIT_NAME}__hello(name='alex')...")
    r = client.call_tool(f"{TOOLKIT_NAME}__hello", {"name": "alex"})
    print(f"  result: {r}")
    if "hello, alex" not in r:
        print("!!! hello tool returned unexpected payload")
        return 2

    print(f"\nCalling {TOOLKIT_NAME}__add(a=2, b=3)...")
    r = client.call_tool(f"{TOOLKIT_NAME}__add", {"a": 2, "b": 3})
    print(f"  result: {r}")
    if json.loads(r).get("sum") != 5.0:
        print("!!! add tool returned unexpected payload")
        return 3

    print("\n--- last 30 lines of serve.log ---")
    serve_log = fake_home / ".toolbase" / "logs" / "serve.log"
    if serve_log.exists():
        lines = serve_log.read_text().splitlines()
        for line in lines[-30:]:
            print(line)
    else:
        print("(no serve.log written — serve_log flag wiring may have regressed)")

    print("\n✓ serve e2e passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
