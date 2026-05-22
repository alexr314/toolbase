"""End-to-end test for the Phase 3C-1 setup system.

Verifies the full install → config-set → serve → call-tool loop:

1. Install a synthetic toolkit with a Tier-1 ``config:`` block
   declaring two state fields (``api_key`` secret + ``max_workers``
   integer with default).
2. Confirm the orchestrator refuses to serve the toolkit when the
   required field is unset (carries the NEEDS_VALUE_SENTINEL).
3. Run ``toolbase config set`` to fill in the required value.
4. Run ``toolbase config validate`` and confirm it now passes.
5. Spin up the orchestrator in-process, call the tool, verify the
   injected state values flow through to the tool body.

Like the restart e2e, this runs the orchestrator in-process (same
synthetic-install pattern) so we can drive the test in seconds without
spinning up the full ``toolbase serve`` stdio CLI.

Run from the repo root:

    python tests/e2e/run_setup_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
TOOLKIT_SRC = THIS_DIR / "test-config-toolkit"
TOOLKIT_NAME = "tb-config-test"

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-setup-e2e"
INSTALL_ROOT = WORK_ROOT / "toolbase"


def _setup_synthetic_install() -> Path:
    """Build a fake installed toolkit at INSTALL_ROOT/toolkits/<name>.

    Same pattern as run_restart_e2e.py: copy the toolkit source into
    place and write a .tb_meta.json that points at the dev venv's
    Python (the synthetic toolkit has no extra deps).
    """
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    INSTALL_ROOT.mkdir(parents=True)
    (INSTALL_ROOT / "toolkits").mkdir()
    (INSTALL_ROOT / "config").mkdir()

    dest = INSTALL_ROOT / "toolkits" / TOOLKIT_NAME
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


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess with HOME pointed at our isolated config root."""
    env = os.environ.copy()
    env["HOME"] = str(INSTALL_ROOT.parent)
    # The CLI reads CONFIG_DIR via Path.home() / ".toolbase". Layout:
    #   $HOME/.toolbase/  ←  our INSTALL_ROOT
    return subprocess.run(
        cmd, env=env, capture_output=True, text=True, check=False, **kwargs,
    )


def main() -> int:
    if not TOOLKIT_SRC.exists():
        print(f"!!! synthetic toolkit missing at {TOOLKIT_SRC}")
        return 1

    # 0.5.0 cache layout: $HOME/.toolbase/cache/<name>/<version>/.
    fake_home = WORK_ROOT
    if fake_home.exists():
        shutil.rmtree(fake_home)
    fake_home.mkdir(parents=True)
    (fake_home / ".toolbase").symlink_to(INSTALL_ROOT)
    INSTALL_ROOT.mkdir(parents=True)
    version = "0.1.0"
    cache_root = INSTALL_ROOT / "cache"
    dest = cache_root / TOOLKIT_NAME / version
    shutil.copytree(TOOLKIT_SRC, dest)
    meta = {
        "name": TOOLKIT_NAME, "version": version,
        "environment": "venv",
        "python_path": sys.executable,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    (dest / ".tb_meta.json").write_text(json.dumps(meta, indent=2))
    # Also drop the canonical .install_meta.yaml so the cache walker
    # picks the slot up; the .tb_meta.json carry-along stays for the
    # serve / setup runner.
    from toolbase.envs import write_install_meta as _wim
    _wim(dest, name=TOOLKIT_NAME, version=version,
         install_method="venv",
         python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
         extras={"python_path": sys.executable})

    # Patch HOME so the CLI's CONFIG_DIR resolves to our isolated dir.
    os.environ["HOME"] = str(fake_home)

    # ── Step 1: validate refuses with no config ────────────────────
    print("=" * 60)
    print("Step 1: toolbase config validate (should fail — required missing)")
    print("=" * 60)

    # Force CLI imports to re-resolve CONFIG_DIR to the new HOME.
    from importlib import reload
    from toolbase import config as toolbase_config
    reload(toolbase_config)
    from toolbase import setup as setup_mod
    reload(setup_mod)
    from toolbase.setup import declarative as _decl
    reload(_decl)
    from toolbase.serve import orchestrator
    reload(orchestrator)

    from click.testing import CliRunner
    from toolbase import cli as _cli
    reload(_cli)
    runner = CliRunner()

    r = runner.invoke(_cli.main, ["config", "validate", TOOLKIT_NAME])
    print(r.output)
    if r.exit_code == 0:
        print("!!! validate should have failed — required field is unset")
        return 2
    if "missing required" not in r.output.lower() and "no config" in r.output.lower():
        # If no file exists yet, validate may say "no config." The setup
        # was supposed to drop a NEEDS_VALUE marker on install — but
        # we bypassed the install pipeline. Drop the file manually.
        from toolbase.setup import (
            parse_config_block, run_install_setup,
        )
        import yaml as _y
        with open(dest / "toolkit.yaml") as f:
            data = _y.safe_load(f)
        schema = parse_config_block(data["config"])
        run_install_setup(TOOLKIT_NAME, schema, mode="skip")
        # Re-run validate.
        r = runner.invoke(_cli.main, ["config", "validate", TOOLKIT_NAME])
        print(r.output)

    if "api_key" not in r.output:
        print("!!! expected api_key to be flagged as missing required")
        return 3

    # ── Step 2: orchestrator skips the toolkit ─────────────────────
    print()
    print("=" * 60)
    print("Step 2: orchestrator should skip toolkit on missing required")
    print("=" * 60)
    orch = orchestrator.Orchestrator()  # cache-walker discovery
    try:
        orch.start()
    except RuntimeError as e:
        if "no toolkits could be started" not in str(e):
            print(f"!!! unexpected RuntimeError: {e}")
            return 4
        print(f"  ✓ orchestrator refused to serve: {e}")

    # ── Step 3: config set fills the required value ────────────────
    print()
    print("=" * 60)
    print("Step 3: toolbase config set api_key tb_user_xxx")
    print("=" * 60)
    r = runner.invoke(
        _cli.main,
        ["config", "set", TOOLKIT_NAME, "api_key", "tb_user_xxx"],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! config set failed")
        return 5

    r = runner.invoke(_cli.main, ["config", "validate", TOOLKIT_NAME])
    print(r.output)
    if r.exit_code != 0:
        print("!!! validate should have passed after fill")
        return 6

    # ── Step 4: serve in-process and verify state injection ────────
    print()
    print("=" * 60)
    print("Step 4: orchestrator serves; tool sees injected state")
    print("=" * 60)
    orch = orchestrator.Orchestrator()  # cache-walker discovery
    orch.start()

    rt = orch._runtimes.get(TOOLKIT_NAME)
    if rt is None:
        print(f"!!! toolkit {TOOLKIT_NAME!r} did not load")
        return 7
    print(f"  state: {rt.state.name}")

    proxies = {p.get_name(): p for p in orch._proxy_tools}
    qualified = f"{TOOLKIT_NAME}__get_config"
    if qualified not in proxies:
        print(f"!!! proxy tool {qualified} missing")
        return 8
    result = proxies[qualified].execute()
    print(f"  tool returned: {result}")

    try:
        payload = json.loads(result)
    except Exception:
        print(f"!!! tool returned non-JSON: {result!r}")
        orch.shutdown()
        return 9

    if payload.get("api_key") != "tb_user_xxx":
        print(f"!!! api_key not injected correctly: {payload}")
        orch.shutdown()
        return 10
    if payload.get("max_workers") != 4:
        print(f"!!! max_workers default not injected: {payload}")
        orch.shutdown()
        return 11

    print("  ✓ state values flowed through to tool")

    # ── Step 5: confirm config show masks the secret ───────────────
    print()
    print("=" * 60)
    print("Step 5: config show masks secret value")
    print("=" * 60)
    r = runner.invoke(_cli.main, ["config", "show", TOOLKIT_NAME])
    print(r.output)
    if "tb_user_xxx" in r.output:
        print("!!! secret leaked in config show output")
        orch.shutdown()
        return 12
    if "<set>" not in r.output:
        print("!!! expected <set> placeholder for secret")
        orch.shutdown()
        return 13

    orch.shutdown()
    print()
    print("✓ setup e2e passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
