"""Orchestrator-side tests for ``bundles.requires:`` evaluation.

These exercise the surface of ``_resolve_bundle_availability`` and
``_read_bundles_and_membership`` against synthetic toolkit dirs
without launching a real subprocess. End-to-end coverage (full serve
spawn + MCP listing) lives in ``tests/e2e/run_bundles_e2e.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from toolbase import config as toolbase_config
from toolbase.serve.orchestrator import (
    ToolkitDiscovery,
    _read_bundles_and_membership,
    _resolve_bundle_availability,
)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated user-level config dir + a stub project root."""
    fake = tmp_path / "toolbase"
    fake.mkdir()
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return tmp_path


def _make_toolkit(
    base: Path,
    name: str = "heptapod",
    config_block=None,
    bundles=None,
    tools=None,
) -> ToolkitDiscovery:
    """Drop a synthetic toolkit dir under ``base/cache/<name>/0.1.0/``."""
    tk = base / "cache" / name / "0.1.0"
    tk.mkdir(parents=True, exist_ok=True)
    yaml_data = {
        "name": name,
        "version": "0.1.0",
        "description": "x",
        "author": "test",
        "category": "other",
        "tools": tools or [
            {"name": "t1", "function": "tools.t1.t1", "description": "x"},
        ],
    }
    if config_block is not None:
        yaml_data["config"] = config_block
    if bundles is not None:
        yaml_data["bundles"] = bundles
    (tk / "toolkit.yaml").write_text(yaml.safe_dump(yaml_data, sort_keys=False))
    return ToolkitDiscovery(
        name=name, path=tk, meta={"environment": "venv"},
    )


def _set_user_config(config_dir: Path, name: str, values: dict) -> None:
    """Write a Phase 3C user-layer config file."""
    config_subdir = config_dir / "config"
    config_subdir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1}
    payload.update(values)
    (config_subdir / f"{name}.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False)
    )


# ────────────────────────────────────────────────────────────────────
# _read_bundles_and_membership
# ────────────────────────────────────────────────────────────────────


class TestReadToolBundlesAndMembership:

    def test_no_bundles_block(self, tmp_path):
        disc = _make_toolkit(tmp_path)
        block, mapping = _read_bundles_and_membership(disc.path)
        assert block is None
        # Tools without ``bundle:`` field map to an empty list.
        assert mapping == {"t1": []}

    def test_bundles_block_with_membership(self, tmp_path):
        disc = _make_toolkit(
            tmp_path,
            config_block=[
                {"name": "mg5_path", "type": "path", "required": False},
            ],
            bundles={"pdg": {}, "mg5": {"requires": ["mg5_path"]}},
            tools=[
                {
                    "name": "pdg_lookup", "module": "x.pdg",
                    "bundle": "pdg",
                },
                {
                    "name": "mg5_run", "module": "x.mg5",
                    "bundle": "mg5",
                },
                {
                    "name": "loose_tool", "module": "x.loose",
                },
            ],
        )
        block, mapping = _read_bundles_and_membership(disc.path)
        assert block == {"pdg": {}, "mg5": {"requires": ["mg5_path"]}}
        # Each tool's membership is a list of bundle names; absent = [].
        assert mapping == {
            "pdg_lookup": ["pdg"],
            "mg5_run": ["mg5"],
            "loose_tool": [],
        }

    def test_missing_yaml_returns_safe_default(self, tmp_path):
        disc = ToolkitDiscovery(
            name="nope", path=tmp_path / "missing", meta={},
        )
        block, mapping = _read_bundles_and_membership(disc.path)
        assert block is None
        assert mapping == {}

    def test_yaml_block_with_none_entries_treated_as_empty(self, tmp_path):
        """YAML ``foo:`` with no value parses as None; treat as empty bundle entry."""
        disc = _make_toolkit(
            tmp_path,
            config_block=[
                {"name": "mg5_path", "type": "path", "required": False},
            ],
            bundles={"pdg": None, "mg5": {"requires": ["mg5_path"]}},
        )
        block, _ = _read_bundles_and_membership(disc.path)
        assert block["pdg"] == {}
        assert "requires" in block["mg5"]


# ────────────────────────────────────────────────────────────────────
# _resolve_bundle_availability (full integration with two-layer config)
# ────────────────────────────────────────────────────────────────────


class TestResolveBundleAvailability:

    def test_no_block_no_gating(self, isolated):
        disc = _make_toolkit(isolated)
        availability, mapping = _resolve_bundle_availability(disc)
        assert availability.has_bundles_block is False
        assert availability.is_bundle_available(None) is True

    def test_user_layer_unlocks_bundle(self, isolated):
        disc = _make_toolkit(
            isolated,
            name="heptapod",
            config_block=[
                {"name": "mg5_path", "type": "path", "required": False},
            ],
            bundles={"mg5": {"requires": ["mg5_path"]}},
        )
        _set_user_config(
            toolbase_config.CONFIG_DIR, "heptapod",
            {"mg5_path": "/opt/mg5"},
        )
        availability, _ = _resolve_bundle_availability(disc)
        assert "mg5" in availability.available_bundles
        assert availability.dropped_bundles == {}

    def test_user_layer_missing_drops_bundle(self, isolated):
        disc = _make_toolkit(
            isolated,
            name="heptapod",
            config_block=[
                {"name": "mg5_path", "type": "path", "required": False},
            ],
            bundles={"mg5": {"requires": ["mg5_path"]}},
        )
        # No user-level config written.
        availability, _ = _resolve_bundle_availability(disc)
        assert "mg5" in availability.dropped_bundles
        assert availability.dropped_bundles["mg5"] == ["mg5_path"]

    def test_needs_value_sentinel_drops_bundle(self, isolated):
        disc = _make_toolkit(
            isolated,
            name="heptapod",
            config_block=[
                {"name": "mg5_path", "type": "path", "required": True},
            ],
            bundles={"mg5": {"requires": ["mg5_path"]}},
        )
        _set_user_config(
            toolbase_config.CONFIG_DIR, "heptapod",
            {"mg5_path": "<NEEDS VALUE>"},
        )
        availability, _ = _resolve_bundle_availability(disc)
        assert "mg5" in availability.dropped_bundles

    def test_multi_require_partial_satisfied_drops(self, isolated):
        disc = _make_toolkit(
            isolated,
            name="heptapod",
            config_block=[
                {"name": "wolframscript_path", "type": "path", "required": False},
                {"name": "feynrules_path", "type": "path", "required": False},
            ],
            bundles={
                "feynrules": {
                    "requires": ["wolframscript_path", "feynrules_path"],
                },
            },
        )
        _set_user_config(
            toolbase_config.CONFIG_DIR, "heptapod",
            {"wolframscript_path": "/usr/bin/wolframscript"},
        )
        availability, _ = _resolve_bundle_availability(disc)
        assert "feynrules" in availability.dropped_bundles
        assert availability.dropped_bundles["feynrules"] == ["feynrules_path"]
