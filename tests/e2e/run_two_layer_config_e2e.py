"""End-to-end test for the Phase 4 two-layer config system.

Drives the user→project layer merge across the real CLI surface:

1. Install a synthetic toolkit with a Tier-1 ``config:`` block.
2. Set ``api_key`` at the user layer (``tb config set`` from outside
   any project, i.e. default-project context).
3. Initialize a project at a tmp dir.
4. From inside the project, set ``api_key`` to a different value via
   ``tb config set`` (no flags) and verify it writes to the project
   layer (not the user layer).
5. Verify ``tb config show`` (no flags, in the project) renders the
   merged view with per-key layer annotations.
6. Verify ``tb config show --layer user`` shows only the user-layer
   value.
7. Verify the orchestrator's state-config resolution sees the merged
   view: the project value overrides the user value, and the tool
   receives the project value.

Run from the repo root:

    python tests/e2e/run_two_layer_config_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
TOOLKIT_SRC = THIS_DIR / "test-config-toolkit"
TOOLKIT_NAME = "tb-config-test"

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-two-layer-e2e"
FAKE_HOME = WORK_ROOT / "fake-home"
INSTALL_ROOT = FAKE_HOME / ".toolbase"


def main() -> int:
    if not TOOLKIT_SRC.exists():
        print(f"!!! synthetic toolkit missing at {TOOLKIT_SRC}")
        return 1

    # Fresh fake home.
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    INSTALL_ROOT.mkdir(parents=True)

    # Cache slot for the synthetic toolkit.
    version = "0.1.0"
    dest = INSTALL_ROOT / "cache" / TOOLKIT_NAME / version
    shutil.copytree(TOOLKIT_SRC, dest)

    meta = {
        "name": TOOLKIT_NAME,
        "version": version,
        "environment": "venv",
        "python_path": sys.executable,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    (dest / ".tb_meta.json").write_text(json.dumps(meta, indent=2))

    from toolbase.envs import write_install_meta as _wim
    _wim(
        dest, name=TOOLKIT_NAME, version=version,
        install_method="venv",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        extras={"python_path": sys.executable},
    )

    # Patch HOME so the CLI's CONFIG_DIR resolves to our isolated dir.
    os.environ["HOME"] = str(FAKE_HOME)
    from importlib import reload
    from toolbase import config as toolbase_config
    reload(toolbase_config)
    from toolbase import setup as setup_mod
    reload(setup_mod)
    from toolbase.setup import storage as _storage
    reload(_storage)
    from toolbase.setup import declarative as _decl
    reload(_decl)
    from toolbase.serve import orchestrator
    reload(orchestrator)
    from toolbase import cli as _cli
    reload(_cli)
    from click.testing import CliRunner
    runner = CliRunner()

    # ── Step 1: Set api_key at user layer from default-project context.
    # Make cwd somewhere with no project upward.
    nowhere = WORK_ROOT / "nowhere"
    nowhere.mkdir()
    os.chdir(nowhere)
    print("=" * 60)
    print("Step 1: set api_key at USER layer (default-project context)")
    print("=" * 60)
    r = runner.invoke(
        _cli.main,
        ["config", "set", TOOLKIT_NAME, "api_key", "tb_user_USER_LAYER"],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! config set should have succeeded")
        return 2
    user_file = INSTALL_ROOT / "config" / f"{TOOLKIT_NAME}.yaml"
    if not user_file.exists():
        print(f"!!! user-layer file not created: {user_file}")
        return 3
    if "USER_LAYER" not in user_file.read_text():
        print("!!! user layer doesn't contain the value")
        return 4
    print(f"  ✓ user-layer file: {user_file}")

    # ── Step 2: create a project, cd into it.
    project = WORK_ROOT / "myproj"
    project.mkdir()
    print()
    print("=" * 60)
    print("Step 2: tb project init")
    print("=" * 60)
    r = runner.invoke(
        _cli.main,
        ["project", "init", "--path", str(project)],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! project init failed")
        return 5
    manifest = project / ".toolbase" / "manifest.yaml"
    if not manifest.exists():
        print("!!! manifest not created")
        return 6

    # ── Step 3: set api_key from inside the project → project layer.
    os.chdir(project)
    print()
    print("=" * 60)
    print("Step 3: set api_key INSIDE project (defaults to project layer)")
    print("=" * 60)
    r = runner.invoke(
        _cli.main,
        ["config", "set", TOOLKIT_NAME, "api_key", "tb_user_PROJECT_LAYER"],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! config set in project failed")
        return 7
    project_file = project / ".toolbase" / "config" / f"{TOOLKIT_NAME}.yaml"
    if not project_file.exists():
        print(f"!!! project-layer file not created: {project_file}")
        return 8
    if "PROJECT_LAYER" not in project_file.read_text():
        print("!!! project layer doesn't contain the value")
        return 9
    # User layer should still have the OLD value (project file is sparse).
    if "USER_LAYER" not in user_file.read_text():
        print("!!! user layer was mutated by a project-layer write — bug")
        return 10
    print(f"  ✓ project-layer file: {project_file}")
    print(f"  ✓ user-layer file preserved")

    # ── Step 4: tb config show in project → merged with annotations.
    print()
    print("=" * 60)
    print("Step 4: tb config show (merged view in project context)")
    print("=" * 60)
    r = runner.invoke(_cli.main, ["config", "show", TOOLKIT_NAME])
    print(r.output)
    if r.exit_code != 0:
        print("!!! config show failed")
        return 11
    # secrets are masked: <set>; layer annotation: # from project
    if "from project" not in r.output:
        print("!!! merged view should annotate api_key as `# from project`")
        return 12

    # ── Step 5: tb config show --layer user → only user-layer value.
    print()
    print("=" * 60)
    print("Step 5: tb config show --layer user (single-layer view)")
    print("=" * 60)
    r = runner.invoke(
        _cli.main, ["config", "show", TOOLKIT_NAME, "--layer", "user"],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! config show --layer user failed")
        return 13
    # Single-layer view doesn't annotate with `# from <layer>`.
    if "from project" in r.output or "from user" in r.output:
        print("!!! single-layer view should not annotate")
        return 14

    # ── Step 6: orchestrator state-config resolution sees project wins.
    print()
    print("=" * 60)
    print("Step 6: orchestrator resolves merged view; tool sees project value")
    print("=" * 60)
    from toolbase.setup import parse_config_block
    import yaml as _y
    with open(dest / "toolkit.yaml") as f:
        tk_yaml = _y.safe_load(f)
    schema = parse_config_block(tk_yaml["config"])
    res = _decl.load_state_config(
        TOOLKIT_NAME, schema, project_root=project,
    )
    if not res.ok:
        print(f"!!! merged state-config not ok: {res.skip_reason()}")
        return 15
    if res.state_config.get("api_key") != "tb_user_PROJECT_LAYER":
        print(
            f"!!! merged api_key = {res.state_config.get('api_key')!r}, "
            "expected project-layer value"
        )
        return 16
    print(
        f"  ✓ orchestrator sees api_key = "
        f"{res.state_config['api_key']!r} (project wins)"
    )

    # ── Step 7: tb config unset on project layer.
    print()
    print("=" * 60)
    print("Step 7: tb config unset api_key (removes from project layer)")
    print("=" * 60)
    r = runner.invoke(
        _cli.main, ["config", "unset", TOOLKIT_NAME, "api_key"],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! config unset failed")
        return 17
    # User layer still has its value.
    if "USER_LAYER" not in user_file.read_text():
        print("!!! user layer was destroyed by project-layer unset")
        return 18
    # Merged view should now fall back to user value.
    res = _decl.load_state_config(
        TOOLKIT_NAME, schema, project_root=project,
    )
    if res.state_config.get("api_key") != "tb_user_USER_LAYER":
        print(
            f"!!! after unset, merged api_key = "
            f"{res.state_config.get('api_key')!r}, expected user-layer fallback"
        )
        return 19
    print("  ✓ after project unset, merged view falls back to user value")

    print()
    print("=" * 60)
    print("✓ two-layer config e2e passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
