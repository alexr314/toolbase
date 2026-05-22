"""End-to-end integration test for the 0.5.0 environments / scoping work.

Wraps the full Phase 1-6 stack in one harness:

1. Fresh ``~/.toolbase/`` (tmpdir, isolated via ``CONFIG_DIR`` patch).
2. ``tb project init`` in a tmp project dir.
3. ``tb install <synthetic>@0.1.0`` (synthetic install — no real pip
   work; mirrors the pattern used by ``run_two_layer_config_e2e.py``).
4. ``tb install <synthetic>@0.2.0`` (second slot).
5. Verify both cache slots exist; each carries ``.install_meta.yaml`` +
   ``.disk_size``; ``.last_used`` is absent (never served).
6. Verify the project manifest pins the latest (0.2.0).
7. ``tb list`` (tree) and ``tb list --json`` outputs sane.
8. ``tb config set`` at user layer (from default-project context) and
   at project layer (from inside the project).
9. Resolve state-config from inside the project: the project-layer
   value MUST win.
10. Simulate a serve session by touching ``.last_used`` on the pinned
    slot (the production orchestrator does this in ``_launch_one``;
    we touch directly to avoid spawning a real subprocess in this
    integration harness — that path is covered by ``run_serve_e2e.py``).
11. Verify ``.last_used`` is set on the pinned slot, absent on the
    unpinned slot.
12. ``tb reset --dry-run --all --include-config`` lists every target
    correctly.

Network-free, no real install pipeline, no real serve subprocess.
Target runtime: <30s.

Run from the repo root:

    python tests/e2e/run_envs_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
TOOLKIT_SRC = THIS_DIR / "test-config-toolkit"
TOOLKIT_NAME = "tb-envs-test"

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-envs-e2e"
FAKE_HOME = WORK_ROOT / "fake-home"
FAKE_TOOLBASE = FAKE_HOME / ".toolbase"


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def _seed_cache_slot(name: str, version: str) -> Path:
    """Synthetic install: copy the toolkit source into a cache slot
    and write the metadata files the orchestrator + ``tb list`` need.

    Mirrors the production ``tb install`` end-state without paying
    the cost of a real pip install. The slot points at the *current*
    Python interpreter because the synthetic toolkit has no extra deps.
    """
    from toolbase.envs import write_install_meta as _wim

    slot = FAKE_TOOLBASE / "cache" / name / version
    if slot.exists():
        shutil.rmtree(slot)
    slot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TOOLKIT_SRC, slot)

    # Rewrite the toolkit.yaml so the in-cache name + version match
    # what the orchestrator / list expects.
    import yaml as _y
    yaml_path = slot / "toolkit.yaml"
    tk_data = _y.safe_load(yaml_path.read_text())
    tk_data["name"] = name
    tk_data["version"] = version
    yaml_path.write_text(_y.safe_dump(tk_data, sort_keys=False))

    # Legacy meta (orchestrator reads .stk_meta.json + .install_meta.yaml).
    meta = {
        "name": name,
        "version": version,
        "environment": "venv",
        "python_path": sys.executable,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    (slot / ".stk_meta.json").write_text(json.dumps(meta, indent=2))

    # 0.5.0 install_meta — same shape as a real install.
    _wim(
        slot, name=name, version=version,
        install_method="venv",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        extras={"python_path": sys.executable},
    )

    # Pre-compute and write .disk_size so list shows a real number.
    from toolbase.envs import compute_and_write_disk_size as _cdsz
    _cdsz(slot)

    return slot


def _add_pin(name: str, version: str, project_root: Path) -> None:
    """Add a manifest pin under ``<project_root>/.toolbase/manifest.yaml``."""
    from toolbase.envs import project_manifest_path, add_pin
    add_pin(project_manifest_path(project_root), name, version)


def main() -> int:
    if not TOOLKIT_SRC.exists():
        print(f"!!! synthetic toolkit missing at {TOOLKIT_SRC}")
        return 1

    # ── Fresh fake home + CONFIG_DIR patch ──────────────────────────
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    FAKE_TOOLBASE.mkdir(parents=True)

    # Patch HOME so any module-level path resolutions that fall through
    # to ``~`` land in our tmp tree. Reload toolbase modules so their
    # import-time computations (CONFIG_DIR, etc.) reflect the new HOME.
    os.environ["HOME"] = str(FAKE_HOME)
    os.environ.pop("TOOLBASE_SUPPRESS_LEGACY_WARNING", None)

    from importlib import reload
    from toolbase import config as toolbase_config
    reload(toolbase_config)
    # Override CONFIG_DIR directly too — belt-and-suspenders for any
    # already-imported attrs that captured a stale path.
    toolbase_config.CONFIG_DIR = FAKE_TOOLBASE

    from toolbase import setup as setup_mod
    reload(setup_mod)
    from toolbase.setup import storage as _storage
    reload(_storage)
    from toolbase.setup import declarative as _decl
    reload(_decl)
    from toolbase.serve import orchestrator as _orch
    reload(_orch)
    from toolbase import cli as _cli
    reload(_cli)

    from click.testing import CliRunner
    runner = CliRunner()

    # ── 1. tb project init in a tmp project dir ────────────────────
    step("Step 1: tb project init")
    project = WORK_ROOT / "myproj"
    project.mkdir()
    r = runner.invoke(
        _cli.main,
        ["project", "init", "--path", str(project)],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! project init failed")
        return 2
    manifest = project / ".toolbase" / "manifest.yaml"
    if not manifest.exists():
        print(f"!!! manifest not created at {manifest}")
        return 3

    # ── 2. Synthetic install of two versions, pinned to the project ─
    # The integration question is "does the cache-plus-manifest substrate
    # behave correctly end-to-end?" — not "does pip work?". We bypass
    # the install pipeline (it's covered by run_install_e2e.py) and seed
    # the cache directly, then add manifest pins.
    step("Step 2: seed cache slots for v0.1.0 and v0.2.0, pin latest")
    slot_v1 = _seed_cache_slot(TOOLKIT_NAME, "0.1.0")
    slot_v2 = _seed_cache_slot(TOOLKIT_NAME, "0.2.0")
    print(f"  v0.1.0 slot: {slot_v1}")
    print(f"  v0.2.0 slot: {slot_v2}")
    # Production: ``tb install foo`` pins the latest; here we model that
    # by pinning 0.2.0 (the latest of the two we just installed).
    _add_pin(TOOLKIT_NAME, "0.2.0", project)

    # ── 3. Verify cache layout + metadata files ─────────────────────
    step("Step 3: verify cache slots, .install_meta.yaml, .disk_size, .last_used")
    for slot, ver in [(slot_v1, "0.1.0"), (slot_v2, "0.2.0")]:
        if not (slot / ".install_meta.yaml").exists():
            print(f"!!! {slot}/.install_meta.yaml missing")
            return 4
        if not (slot / ".stk_meta.json").exists():
            print(f"!!! {slot}/.stk_meta.json missing")
            return 4
        if not (slot / ".disk_size").exists():
            print(f"!!! {slot}/.disk_size missing")
            return 4
        # Fresh install → never served → .last_used absent.
        if (slot / ".last_used").exists():
            print(f"!!! {slot}/.last_used should NOT exist (never served)")
            return 4
        size = int((slot / ".disk_size").read_text().strip())
        print(f"  v{ver}: install_meta + stk_meta + disk_size present "
              f"({size} bytes), .last_used absent (as expected)")

    # ── 4. Verify manifest pins latest (0.2.0) ──────────────────────
    step("Step 4: project manifest pins 0.2.0")
    from toolbase.envs import get_pin
    pin = get_pin(manifest, TOOLKIT_NAME)
    if pin is None or pin.version != "0.2.0":
        print(f"!!! manifest pin = {pin}, expected 0.2.0")
        return 5
    print(f"  pinned: {pin.name}@{pin.version} (pinned_at={pin.pinned_at})")

    # ── 5. tb list (tree) and tb list --json ──────────────────────
    # Run from inside the project so the pinned-version indicator picks
    # up the right manifest.
    os.chdir(project)
    step("Step 5a: tb list (tree)")
    r = runner.invoke(_cli.main, ["list"])
    print(r.stdout.rstrip())
    if r.exit_code != 0:
        print("!!! tb list failed")
        return 6
    if TOOLKIT_NAME not in r.stdout:
        print(f"!!! tb list output missing {TOOLKIT_NAME}")
        return 6
    if "0.1.0" not in r.stdout or "0.2.0" not in r.stdout:
        print("!!! tb list should show both versions")
        return 6
    # 0.2.0 is the pinned version; the indicator (*) must be on it.
    # Be lax about row formatting — just check both versions and the
    # asterisk are present.
    if "*" not in r.stdout:
        print("!!! tb list should include the pinned-version indicator (*)")
        return 6

    step("Step 5b: tb list --json (parseable)")
    r = runner.invoke(_cli.main, ["list", "--json"])
    if r.exit_code != 0:
        print("!!! tb list --json failed")
        return 7
    payload = json.loads(r.stdout)
    by_version = {rec["version"]: rec for rec in payload if rec["name"] == TOOLKIT_NAME}
    if "0.1.0" not in by_version or "0.2.0" not in by_version:
        print(f"!!! --json output missing versions; got {list(by_version)}")
        return 7
    if by_version["0.2.0"]["pinned_in_project"] is not True:
        print("!!! 0.2.0 should be pinned_in_project=True")
        return 7
    if by_version["0.1.0"]["pinned_in_project"] is not False:
        print("!!! 0.1.0 should be pinned_in_project=False")
        return 7
    if by_version["0.2.0"]["size_bytes"] <= 0:
        print("!!! size_bytes should be populated")
        return 7
    print(f"  JSON OK: 0.2.0 pinned, 0.1.0 unpinned, both with size_bytes")

    # ── 6. tb config set at user + project layers ─────────────────
    # User layer: cd out to a no-project dir so the default-project
    # context applies. (--user flag would also work; we exercise the
    # default-resolution path here.)
    step("Step 6a: tb config set api_key (user layer, from default-project)")
    nowhere = WORK_ROOT / "nowhere"
    nowhere.mkdir()
    os.chdir(nowhere)
    r = runner.invoke(
        _cli.main,
        ["config", "set", TOOLKIT_NAME, "api_key", "tb_user_USER_VALUE"],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! user-layer config set failed")
        return 8
    user_cfg = FAKE_TOOLBASE / "config" / f"{TOOLKIT_NAME}.yaml"
    if not user_cfg.exists():
        print(f"!!! user-layer file not created at {user_cfg}")
        return 8
    if "USER_VALUE" not in user_cfg.read_text():
        print("!!! user-layer file missing the value")
        return 8

    step("Step 6b: tb config set api_key (project layer, from inside project)")
    os.chdir(project)
    r = runner.invoke(
        _cli.main,
        ["config", "set", TOOLKIT_NAME, "api_key", "tb_user_PROJECT_VALUE"],
    )
    print(r.output)
    if r.exit_code != 0:
        print("!!! project-layer config set failed")
        return 9
    project_cfg = project / ".toolbase" / "config" / f"{TOOLKIT_NAME}.yaml"
    if not project_cfg.exists():
        print(f"!!! project-layer file not created at {project_cfg}")
        return 9
    if "PROJECT_VALUE" not in project_cfg.read_text():
        print("!!! project-layer file missing the value")
        return 9
    # User layer must not be mutated by a project write.
    if "USER_VALUE" not in user_cfg.read_text():
        print("!!! user-layer file was mutated by a project write")
        return 9

    # ── 7. Orchestrator state-config resolution sees project wins ──
    step("Step 7: state-config resolution: project value overrides user value")
    from toolbase.setup import parse_config_block
    import yaml as _y
    with open(slot_v2 / "toolkit.yaml") as f:
        tk_yaml = _y.safe_load(f)
    schema = parse_config_block(tk_yaml["config"])
    res = _decl.load_state_config(
        TOOLKIT_NAME, schema, project_root=project,
    )
    if not res.ok:
        print(f"!!! state-config resolution failed: {res.skip_reason()}")
        return 10
    if res.state_config.get("api_key") != "tb_user_PROJECT_VALUE":
        print(
            f"!!! merged api_key = {res.state_config.get('api_key')!r}, "
            "expected project-layer value"
        )
        return 10
    print(f"  ✓ api_key = {res.state_config['api_key']!r} (project wins)")

    # ── 8. Simulate serve: touch .last_used on the pinned slot ─────
    # Production: orchestrator._launch_one calls envs.touch_last_used
    # on every spawn. The full subprocess path is exercised by
    # run_serve_e2e.py — here we touch directly to keep the harness
    # under 30s without sacrificing the "last_used was touched on
    # the served slot only" check.
    step("Step 8: touch .last_used on pinned slot (simulating serve)")
    from toolbase.envs import touch_last_used as _tlu
    _tlu(slot_v2)
    # Brief sleep so list's "X seconds ago" rendering has a real delta.
    time.sleep(0.05)

    pinned_lu = slot_v2 / ".last_used"
    unpinned_lu = slot_v1 / ".last_used"
    if not pinned_lu.exists():
        print(f"!!! .last_used should exist on pinned slot")
        return 11
    if unpinned_lu.exists():
        print(f"!!! .last_used should NOT exist on unpinned slot")
        return 11
    print(f"  ✓ pinned slot .last_used set: {pinned_lu.read_text().strip()}")
    print(f"  ✓ unpinned slot .last_used absent")

    # ── 9. tb list now shows last_used for pinned, never for unpinned ─
    step("Step 9: tb list reflects the .last_used delta")
    r = runner.invoke(_cli.main, ["list", "--json"])
    if r.exit_code != 0:
        print("!!! tb list --json failed (post-serve)")
        return 12
    payload = json.loads(r.stdout)
    by_version = {rec["version"]: rec for rec in payload if rec["name"] == TOOLKIT_NAME}
    if by_version["0.2.0"]["last_used_iso"] is None:
        print("!!! pinned slot should have a last_used_iso")
        return 12
    if by_version["0.1.0"]["last_used_iso"] is not None:
        print("!!! unpinned slot should NOT have a last_used_iso")
        return 12
    print(f"  ✓ pinned last_used_iso = {by_version['0.2.0']['last_used_iso']}")
    print(f"  ✓ unpinned last_used_iso = None (never served)")

    # ── 10. tb reset --dry-run --all --include-config lists targets ─
    step("Step 10: tb reset --dry-run --all --include-config lists everything")
    r = runner.invoke(
        _cli.main, ["reset", "--dry-run", "--all", "--include-config"],
    )
    print(r.stdout.rstrip())
    if r.exit_code != 0:
        print("!!! reset --dry-run --all --include-config exited non-zero")
        return 13
    # cache/, config/, default-project/ should all be named in the dry-run.
    # (toolkits/ and downloads/ may or may not be present depending on
    # state; they're listed if they exist.)
    for needle in ("cache", "config", "Dry-run"):
        if needle not in r.stdout:
            print(f"!!! reset --dry-run --all --include-config missing {needle!r}")
            return 13
    # And nothing was deleted.
    if not (FAKE_TOOLBASE / "cache").exists():
        print("!!! reset --dry-run deleted cache/")
        return 13
    if not user_cfg.exists():
        print("!!! reset --dry-run deleted user config")
        return 13

    print("\n" + "=" * 60)
    print("✓ envs integration e2e passed (all 10 steps)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
