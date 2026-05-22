"""End-to-end test for ``toolbase install -e`` (editable installs).

Proves the editable dev loop the 0.6.0 feature exists for:

  1. Create a local toolkit source dir.
  2. ``toolbase install -e <dir>`` — symlinks the source into a cache
     slot keyed ``editable`` and builds a real venv in the slot.
  3. ``toolbase serve`` it over stdio MCP; call a tool; confirm result.
  4. Edit the tool's source in place.
  5. Reconnect (a fresh serve = fresh per-toolkit subprocess) and confirm
     the edited behavior is live — proving serve loads tools through the
     symlink from the live source, not a frozen copy.

Run from the repo root with the test venv active (so ``toolbase`` is
on PATH):

    python tests/e2e/run_editable_e2e.py

Side effects: creates a temp tree under ``$TMPDIR/tb-editable-e2e/``.
Cleaned up on rerun. No network access.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


WORK_ROOT = Path(tempfile.gettempdir()) / "tb-editable-e2e"
FAKE_HOME = WORK_ROOT / "fake-home"
SOURCE_DIR = WORK_ROOT / "src" / "editkit"
TOOLKIT_NAME = "editkit"


TOOLKIT_YAML = """\
name: editkit
version: 0.1.0
description: Editable e2e toolkit
author: Toolbase
category: other
tools:
  - name: greet
    function: tools.greet
    description: Returns a greeting
"""

TOOLS_INIT = "from .greet import greet\n\nTOOLS = [greet]\n"

# Version 1 of the tool: returns "hello".
GREET_V1 = '''\
from orchestral import define_tool


@define_tool
def greet(name: str = "world") -> str:
    """Returns a greeting."""
    import json as _json
    return _json.dumps({"message": f"hello, {name}"})
'''

# Version 2 of the tool: returns "HOWDY" — the live-edit we inject.
GREET_V2 = '''\
from orchestral import define_tool


@define_tool
def greet(name: str = "world") -> str:
    """Returns a greeting."""
    import json as _json
    return _json.dumps({"message": f"HOWDY, {name}"})
'''


def _build_source() -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    (SOURCE_DIR / "tools").mkdir(parents=True)
    (SOURCE_DIR / "toolkit.yaml").write_text(TOOLKIT_YAML)
    (SOURCE_DIR / "requirements.txt").write_text("")
    (SOURCE_DIR / "tools" / "__init__.py").write_text(TOOLS_INIT)
    (SOURCE_DIR / "tools" / "greet.py").write_text(GREET_V1)
    FAKE_HOME.mkdir(parents=True, exist_ok=True)


def _toolbase_bin() -> str:
    binp = shutil.which("toolbase")
    if binp is None:
        print(
            "!!! could not find `toolbase` on PATH. Activate the dev venv."
        )
        sys.exit(1)
    return binp


def _call_greet(toolbase_bin: str) -> str:
    """Connect a fresh MCP client/serve and call editkit__greet."""
    from orchestral.mcp import MCPClient

    sub_env = {
        "HOME": str(FAKE_HOME),
        "PATH": os.environ.get("PATH", ""),
    }
    client = MCPClient(
        server_command=[toolbase_bin, "serve", TOOLKIT_NAME],
        env=sub_env,
    )
    client.connect()
    try:
        return client.call_tool(f"{TOOLKIT_NAME}__greet", {"name": "tony"})
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def main() -> int:
    _build_source()
    toolbase_bin = _toolbase_bin()

    print(f"HOME redirected to {FAKE_HOME}")
    print(f"source dir: {SOURCE_DIR}")
    print(f"using toolbase at: {toolbase_bin}\n")

    # Step 1: editable install.
    print("--- toolbase install -e ---")
    install = subprocess.run(
        [toolbase_bin, "install", "-e", str(SOURCE_DIR), "--no-input"],
        env={"HOME": str(FAKE_HOME), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    print(install.stdout[-800:])
    if install.returncode != 0:
        print("!!! editable install failed")
        print(install.stderr[-800:])
        return 1

    # Verify the slot is a real dir with a symlinked tools/ and a real venv.
    slot = FAKE_HOME / ".toolbase" / "cache" / TOOLKIT_NAME / "editable"
    if not (slot / "tools").is_symlink():
        print(f"!!! expected {slot}/tools to be a symlink")
        return 2
    if not (slot / ".venv").is_dir() or (slot / ".venv").is_symlink():
        print(f"!!! expected {slot}/.venv to be a real dir")
        return 2
    if (SOURCE_DIR / ".venv").exists():
        print("!!! .venv leaked into the user's source dir")
        return 2
    print("✓ slot symlinks source, venv is real in slot, source dir clean\n")

    # Step 2: serve + call (expect v1 "hello").
    print("--- serve + call (expect hello) ---")
    r1 = _call_greet(toolbase_bin)
    print(f"  result: {r1}")
    if "hello, tony" not in r1:
        print("!!! v1 greet did not return the expected payload")
        return 3
    print("✓ v1 live\n")

    # Step 3: edit the tool source in place.
    print("--- editing tools/greet.py in place ---")
    (SOURCE_DIR / "tools" / "greet.py").write_text(GREET_V2)
    print("✓ source edited (hello -> HOWDY)\n")

    # Step 4: reconnect (fresh serve = fresh subprocess) and confirm live.
    print("--- serve + call again (expect HOWDY) ---")
    r2 = _call_greet(toolbase_bin)
    print(f"  result: {r2}")
    if "HOWDY, tony" not in r2:
        print("!!! edit was NOT picked up — symlink-follow may be broken")
        return 4
    print("✓ edit is live through the symlink\n")

    print("✓ editable e2e passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
