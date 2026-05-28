"""End-to-end test for the 0.5.1 ``bundles.requires:`` micro-feature.

Drives the full conditional-availability surface against a synthetic
HEPTAPOD-shaped fixture (synthetic config keys; no real MG5 needed):

1. Toolkit declares ``bundles:`` with three bundles —
   ``pdg`` (no requires), ``mg5`` (requires ``mg5_path``),
   ``feynrules`` (requires ``wolframscript_path`` + ``feynrules_path``).
2. Each tool carries a ``bundle:`` field.
3. With **no** user config: ``pdg`` is available; ``mg5`` and
   ``feynrules`` are dropped; tools belonging to those bundles don't
   reach the serve set.
4. With ``mg5_path`` set at the user layer: ``mg5`` unlocks, ``feynrules``
   stays dropped.
5. With ``mg5_path`` blank at user but set at project layer: ``mg5``
   unlocks (project wins).
6. With ``<NEEDS VALUE>`` sentinel: still counts as unset.
7. ``toolbase validate`` rejects a toolkit.yaml whose ``requires:``
   references a config key not in the ``config:`` block.
8. A 0.5.0-shaped toolkit (no ``bundles:`` block) loads on 0.5.1
   unchanged — backwards-compat sanity.

Run from the repo root:

    python tests/e2e/run_bundles_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml


THIS_DIR = Path(__file__).resolve().parent
WORK_ROOT = Path(tempfile.gettempdir()) / "tb-tool-bundles-e2e"
FAKE_HOME = WORK_ROOT / "fake-home"
INSTALL_ROOT = FAKE_HOME / ".toolbase"

TOOLKIT_NAME = "heptapod-synth"
VERSION = "0.1.0"


# ──────────────────────────────────────────────────────────────────
# Fixture builders
# ──────────────────────────────────────────────────────────────────


def _write_toolkit_yaml(dest: Path, *, with_bundles: bool) -> None:
    """Drop a synthetic HEPTAPOD-shaped toolkit.yaml at ``dest``.

    ``with_bundles=False`` is the 0.5.0-shape used for the
    backwards-compat assertion.
    """
    data = {
        "name": TOOLKIT_NAME,
        "version": VERSION,
        "description": "Synthetic HEPTAPOD-shaped fixture for bundles e2e",
        "author": "Toolbase test",
        "category": "hep",
        "config": [
            {"name": "mg5_path", "type": "path", "required": False},
            {"name": "wolframscript_path", "type": "path", "required": False},
            {"name": "feynrules_path", "type": "path", "required": False},
        ],
        "tools": [
            {
                "name": "pdg_lookup",
                "function": "tools.pdg.pdg_lookup",
                "description": "PDG particle lookup — always available",
            },
            {
                "name": "mg5_run",
                "function": "tools.mg5.mg5_run",
                "description": "Run MadGraph",
            },
            {
                "name": "fr_export",
                "function": "tools.fr.fr_export",
                "description": "Export FeynRules model",
            },
            {
                "name": "loose_tool",
                "function": "tools.loose.loose_tool",
                "description": "Tool without a bundle — always served",
            },
        ],
    }
    if with_bundles:
        data["bundles"] = {
            "pdg": {},
            "mg5": {"requires": ["mg5_path"]},
            "feynrules": {"requires": ["wolframscript_path", "feynrules_path"]},
        }
        # Assign tools to bundles.
        data["tools"][0]["bundle"] = "pdg"
        data["tools"][1]["bundle"] = "mg5"
        data["tools"][2]["bundle"] = "feynrules"
        # loose_tool: no bundle — always served regardless.
    (dest / "toolkit.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def _scaffold_toolkit(dest: Path, *, with_bundles: bool) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    _write_toolkit_yaml(dest, with_bundles=with_bundles)
    (dest / "tools").mkdir(exist_ok=True)
    (dest / "tools" / "__init__.py").write_text("# synthetic\n")
    (dest / "requirements.txt").write_text("orchestral-ai>=1.0.0\n")
    (dest / "mcp").mkdir(exist_ok=True)
    (dest / "mcp" / "__init__.py").write_text("")
    (dest / "mcp" / "server_stdio.py").write_text("")
    (dest / "README.md").write_text("# test\n")


def _write_user_config(name: str, values: dict) -> None:
    """Write a Phase 3C user-layer config under FAKE_HOME."""
    cfg_dir = INSTALL_ROOT / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1}
    payload.update(values)
    (cfg_dir / f"{name}.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))


def _write_project_config(project: Path, name: str, values: dict) -> None:
    cfg_dir = project / ".toolbase" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1}
    payload.update(values)
    (cfg_dir / f"{name}.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))


def _reset_user_config(name: str) -> None:
    """Remove user-layer config for a fresh layer scenario."""
    p = INSTALL_ROOT / "config" / f"{name}.yaml"
    if p.exists():
        p.unlink()


# ──────────────────────────────────────────────────────────────────
# Steps
# ──────────────────────────────────────────────────────────────────


def main() -> int:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    INSTALL_ROOT.mkdir(parents=True)

    os.environ["HOME"] = str(FAKE_HOME)
    # Reload config + envs so CONFIG_DIR resolves under our fake home.
    from importlib import reload
    from toolbase import config as toolbase_config
    reload(toolbase_config)
    from toolbase.envs import paths as _envs_paths
    reload(_envs_paths)
    from toolbase.envs import config as _envs_config
    reload(_envs_config)
    from toolbase.serve import orchestrator
    reload(orchestrator)
    from toolbase.serve import bundles as _tg
    reload(_tg)

    # Drop a synthetic toolkit cache slot.
    cache_dir = INSTALL_ROOT / "cache" / TOOLKIT_NAME / VERSION
    _scaffold_toolkit(cache_dir, with_bundles=True)

    from toolbase.serve.orchestrator import (
        ToolkitDiscovery,
        _resolve_bundle_availability,
        _read_bundles_and_membership,
    )

    disc = ToolkitDiscovery(
        name=TOOLKIT_NAME, path=cache_dir, meta={"environment": "venv"},
    )

    # ── Step 1: no config at all → mg5 and feynrules dropped, pdg available.
    print("=" * 60)
    print("Step 1: no config → bundles requiring keys are dropped")
    print("=" * 60)
    availability, mapping = _resolve_bundle_availability(disc)
    print(f"  available: {availability.available_bundles}")
    print(f"  dropped:   {dict(availability.dropped_bundles)}")
    if "pdg" not in availability.available_bundles:
        print("!!! pdg should be available with no requires")
        return 10
    if "mg5" not in availability.dropped_bundles:
        print("!!! mg5 should be dropped — missing mg5_path")
        return 11
    if availability.dropped_bundles["mg5"] != ["mg5_path"]:
        print(f"!!! mg5 dropped_keys mismatch: {availability.dropped_bundles['mg5']}")
        return 12
    if "feynrules" not in availability.dropped_bundles:
        print("!!! feynrules should be dropped")
        return 13
    # Check the membership map: loose_tool has no bundle.
    if mapping.get("loose_tool") is not None:
        print(f"!!! loose_tool should have bundle=None, got {mapping['loose_tool']}")
        return 14
    if mapping.get("mg5_run") != "mg5":
        print(f"!!! mg5_run should belong to bundle 'mg5', got {mapping.get('mg5_run')}")
        return 15
    # is_bundle_available semantics:
    if not availability.is_bundle_available(None):
        print("!!! bundle=None should always be available")
        return 16
    if availability.is_bundle_available("mg5"):
        print("!!! mg5 should be reported unavailable")
        return 17
    print("  ✓ pdg available, mg5 + feynrules dropped, loose_tool has no bundle")

    # ── Step 2: set mg5_path at user layer → mg5 unlocks.
    print()
    print("=" * 60)
    print("Step 2: user layer sets mg5_path → mg5 unlocks")
    print("=" * 60)
    _write_user_config(TOOLKIT_NAME, {"mg5_path": "/opt/mg5"})
    availability, _ = _resolve_bundle_availability(disc)
    print(f"  available: {availability.available_bundles}")
    print(f"  dropped:   {dict(availability.dropped_bundles)}")
    if "mg5" not in availability.available_bundles:
        print("!!! mg5 should be available after setting mg5_path")
        return 20
    if "feynrules" not in availability.dropped_bundles:
        print("!!! feynrules still needs more keys; should be dropped")
        return 21
    print("  ✓ mg5 unlocked, feynrules still dropped")

    # ── Step 3: user blank, project layer fills in.
    print()
    print("=" * 60)
    print("Step 3: user mg5_path blank, project sets it → mg5 unlocks (project wins)")
    print("=" * 60)
    _reset_user_config(TOOLKIT_NAME)
    project = WORK_ROOT / "proj-mg5"
    project.mkdir()
    _write_project_config(project, TOOLKIT_NAME, {"mg5_path": "/opt/from-project"})

    # Patch the project-root resolver so orchestrator picks our project.
    from toolbase import cli as _cli
    orig = _cli._resolve_active_project_root
    _cli._resolve_active_project_root = lambda: (project, "test-override")
    try:
        availability, _ = _resolve_bundle_availability(disc)
        print(f"  available: {availability.available_bundles}")
        if "mg5" not in availability.available_bundles:
            print("!!! mg5 should be available with project-level mg5_path")
            return 30
    finally:
        _cli._resolve_active_project_root = orig

    # ── Step 4: <NEEDS VALUE> sentinel still counts as unset.
    print()
    print("=" * 60)
    print("Step 4: <NEEDS VALUE> sentinel counts as unset")
    print("=" * 60)
    _write_user_config(TOOLKIT_NAME, {"mg5_path": "<NEEDS VALUE>"})
    availability, _ = _resolve_bundle_availability(disc)
    print(f"  available: {availability.available_bundles}")
    print(f"  dropped:   {dict(availability.dropped_bundles)}")
    if "mg5" not in availability.dropped_bundles:
        print("!!! sentinel should keep mg5 dropped")
        return 40
    print("  ✓ <NEEDS VALUE> treated as unset")

    # ── Step 5: toolbase validate rejects unknown require-key.
    print()
    print("=" * 60)
    print("Step 5: toolbase validate rejects unknown require-key")
    print("=" * 60)
    bad = WORK_ROOT / "bad-toolkit"
    bad.mkdir()
    # config: declares mg5_path; bundles.requires references foo_unknown.
    bad_yaml = {
        "name": "bad-toolkit",
        "version": "0.1.0",
        "description": "bad",
        "author": "tester",
        "category": "other",
        "config": [
            {"name": "mg5_path", "type": "path", "required": False},
        ],
        "bundles": {
            "broken": {"requires": ["foo_unknown"]},
        },
        "tools": [
            {"name": "t1", "function": "tools.t1.t1", "description": "x"},
        ],
    }
    (bad / "toolkit.yaml").write_text(yaml.safe_dump(bad_yaml, sort_keys=False))
    (bad / "tools").mkdir()
    (bad / "tools" / "__init__.py").write_text("")
    (bad / "mcp").mkdir()
    (bad / "mcp" / "__init__.py").write_text("")
    (bad / "mcp" / "server_stdio.py").write_text("")
    (bad / "requirements.txt").write_text("orchestral-ai>=1.0.0\n")
    (bad / "README.md").write_text("# bad\n")

    from toolbase.validation import validate_toolkit
    result = validate_toolkit(bad)
    if result.is_valid:
        print("!!! validate should fail on unknown require-key")
        return 50
    joined = " ".join(result.errors)
    if "foo_unknown" not in joined:
        print(f"!!! validate error should mention 'foo_unknown', got: {joined}")
        return 51
    print(f"  ✓ validate rejected; error: {joined.strip()}")

    # ── Step 6: 0.5.0-shape toolkit (no bundles:) still loads on 0.5.1.
    print()
    print("=" * 60)
    print("Step 6: backwards-compat — 0.5.0-shaped toolkit (no bundles:)")
    print("=" * 60)
    legacy = WORK_ROOT / "cache-legacy" / "legacy-tk" / "0.1.0"
    _scaffold_toolkit(legacy, with_bundles=False)
    legacy_disc = ToolkitDiscovery(
        name="legacy-tk", path=legacy, meta={"environment": "venv"},
    )
    availability, mapping = _resolve_bundle_availability(legacy_disc)
    if availability.has_bundles_block:
        print("!!! 0.5.0 toolkit should have no bundles block")
        return 60
    # No gating: every tool is available regardless of bundle field.
    for tool_name in ("pdg_lookup", "mg5_run", "fr_export", "loose_tool"):
        if not availability.is_bundle_available(mapping.get(tool_name)):
            print(f"!!! tool {tool_name} should be available in legacy mode")
            return 61
    # ToolkitMetadata parses (full validate succeeds).
    result = validate_toolkit(legacy)
    if not result.is_valid:
        print(f"!!! 0.5.0 toolkit failed to validate on 0.5.1: {result.errors}")
        return 62
    print("  ✓ 0.5.0-shaped toolkit loads cleanly on 0.5.1")

    # ── Step 7: log-line format sanity (matches the brief's pattern).
    print()
    print("=" * 60)
    print("Step 7: log line format")
    print("=" * 60)
    from toolbase.serve.bundles import format_skip_log_line
    line = format_skip_log_line(TOOLKIT_NAME, "mg5", ["mg5_path"])
    print(f"  -> {line}")
    expected_bits = [
        "[toolbase.serve] bundle_skipped",
        f"toolkit={TOOLKIT_NAME}",
        "name=mg5",
        "reason=missing_config",
        "keys=mg5_path",
    ]
    for bit in expected_bits:
        if bit not in line:
            print(f"!!! log line missing '{bit}': {line}")
            return 70

    print()
    print("=" * 60)
    print("✓ bundles.requires e2e passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
