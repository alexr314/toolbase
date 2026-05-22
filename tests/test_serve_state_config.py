"""Tests for the orchestrator's Phase 3C-1 state-config resolution.

The orchestrator's ``_resolve_state_config(disc)`` walks a discovery,
parses the toolkit's published ``config:`` block, validates the
user's stored values, and returns ``(state_config_dict, skip_reason)``.

Covers the four cases:

1. No ``config:`` block → empty state_config, no skip.
2. Block present + all required filled → populated state_config.
3. Block present + missing required → skip_reason flags missing.
4. Malformed toolkit.yaml or missing file → skip_reason explains.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from toolbase import config as toolbase_config
from toolbase.serve import orchestrator
from toolbase.serve.orchestrator import ToolkitDiscovery
from toolbase.setup import save_config


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "toolbase"
    fake.mkdir()
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def _make_discovery(
    base: Path,
    name: str = "demo",
    config_block=None,
) -> ToolkitDiscovery:
    """Drop a synthetic toolkit dir with toolkit.yaml + .tb_meta.json."""
    tk = base / "toolkits" / name
    tk.mkdir(parents=True)
    yaml_data = {
        "name": name, "version": "0.1.0",
        "description": "x", "author": "test", "category": "other",
        "tools": [{"name": "t", "function": "tools.t", "description": "d"}],
    }
    if config_block is not None:
        yaml_data["config"] = config_block
    (tk / "toolkit.yaml").write_text(yaml.safe_dump(yaml_data))
    meta = {
        "name": name, "version": "0.1.0",
        "environment": "venv",
        "python_path": "/usr/bin/python", "python_version": "3.12",
    }
    (tk / ".tb_meta.json").write_text(json.dumps(meta))
    return ToolkitDiscovery(name=name, path=tk, meta=meta)


def test_no_config_block_returns_empty_dict(isolated: Path):
    disc = _make_discovery(isolated)
    state_config, err = orchestrator._resolve_state_config(disc)
    assert err is None
    assert state_config == {}


def test_all_required_filled_returns_populated_dict(isolated: Path):
    disc = _make_discovery(isolated, config_block=[
        {"name": "api_key", "type": "secret", "required": True},
        {"name": "max_workers", "type": "integer", "default": 4},
    ])
    save_config("demo", {"api_key": "tb_user_xx"})
    state_config, err = orchestrator._resolve_state_config(disc)
    assert err is None
    assert state_config["api_key"] == "tb_user_xx"
    assert state_config["max_workers"] == 4


def test_missing_required_returns_skip_reason(isolated: Path):
    disc = _make_discovery(isolated, config_block=[
        {"name": "api_key", "type": "secret", "required": True},
    ])
    state_config, err = orchestrator._resolve_state_config(disc)
    assert state_config is None
    assert err is not None
    assert "config incomplete" in err
    assert "api_key" in err


def test_invalid_stored_value_returns_skip_reason(isolated: Path):
    disc = _make_discovery(isolated, config_block=[
        {"name": "n", "type": "integer", "min": 1, "max": 10},
    ])
    save_config("demo", {"n": 9999})
    state_config, err = orchestrator._resolve_state_config(disc)
    assert state_config is None
    assert err is not None
    assert "invalid" in err.lower() or "above max" in err.lower()


def test_missing_toolkit_yaml_returns_skip_reason(isolated: Path):
    disc = _make_discovery(isolated)
    (disc.path / "toolkit.yaml").unlink()
    state_config, err = orchestrator._resolve_state_config(disc)
    assert state_config is None
    assert err is not None
    assert "toolkit.yaml" in err


def test_malformed_config_block_returns_skip_reason(isolated: Path):
    disc = _make_discovery(isolated, config_block=[
        {"name": "x", "type": "nonsense_type"},
    ])
    state_config, err = orchestrator._resolve_state_config(disc)
    assert state_config is None
    assert err is not None
    assert "schema" in err.lower() or "config" in err.lower()


def test_state_config_is_json_serializable(isolated: Path):
    """The orchestrator hands the state-config dict to the host
    subprocess via ``--state-config <json>``. Verify nothing in the
    pipeline produces unencodable values."""
    disc = _make_discovery(isolated, config_block=[
        {"name": "data_path", "type": "path"},
        {"name": "n", "type": "integer", "default": 4},
        {"name": "use_gpu", "type": "boolean", "default": False},
    ])
    save_config("demo", {"data_path": "/tmp/data"})
    state_config, err = orchestrator._resolve_state_config(disc)
    assert err is None
    # Round-trip through JSON to confirm.
    encoded = json.dumps(state_config)
    decoded = json.loads(encoded)
    assert decoded["data_path"] == "/tmp/data"
    assert decoded["n"] == 4
    assert decoded["use_gpu"] is False
