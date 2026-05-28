"""Tests for ``toolbase/serve/config.py`` — the defaults-only serve.yaml.

serve.yaml carries only ``default.profile`` (which profile is active) and
``default.disabled`` (absolute blocklists). Profile bodies live one-file-per
under ``profiles/`` and are covered in ``test_serve_profiles.py``.

Matrix:
- with/without serve.yaml present
- profile field present / absent / wrong type
- disabled blocklists round-trip
- the retired ``groups:`` block is rejected with a clear message
- malformed / non-mapping yaml -> clear error with path
- two-layer merge: profile project-wins, disabled lists union
- ``_split_tool`` shape validation
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from toolbase.serve.config import (
    DefaultBlock,
    ServeConfig,
    ServeConfigError,
    _split_tool,
    load_serve_config,
    merge_serve_configs,
    save_serve_config,
)


# ── load / save round trip ──────────────────────────────────────────────────


def test_load_missing_returns_empty(tmp_path: Path):
    cfg = load_serve_config(tmp_path / "serve.yaml")
    assert cfg.default.profile is None
    assert cfg.default.disabled_toolkits == []
    assert cfg.default.disabled_tools == []


def test_load_profile_and_blocklists(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text(yaml.safe_dump({
        "default": {
            "profile": "paper",
            "disabled": {
                "toolkits": ["heptapod"],
                "tools": ["aster__heavy"],
            },
        }
    }))
    cfg = load_serve_config(p)
    assert cfg.default.profile == "paper"
    assert cfg.default.disabled_toolkits == ["heptapod"]
    assert cfg.default.disabled_tools == ["aster__heavy"]


def test_load_profile_must_be_nonempty_string(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text(yaml.safe_dump({"default": {"profile": ["not", "a", "str"]}}))
    with pytest.raises(ServeConfigError):
        load_serve_config(p)


def test_load_rejects_retired_groups_block(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text(yaml.safe_dump({"groups": {"exo": {"toolkits": ["aster"]}}}))
    with pytest.raises(ServeConfigError) as ei:
        load_serve_config(p)
    assert "groups" in str(ei.value)
    assert "profile" in str(ei.value)


def test_load_malformed_yaml_clear_error(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text(":::not valid yaml:::\n  - [")
    with pytest.raises(ServeConfigError) as ei:
        load_serve_config(p)
    assert str(p) in str(ei.value) or "could not parse" in str(ei.value)


def test_load_top_level_must_be_mapping(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text("- item1\n- item2\n")
    with pytest.raises(ServeConfigError):
        load_serve_config(p)


def test_save_and_reload_roundtrip(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    cfg = ServeConfig(
        default=DefaultBlock(
            profile="paper",
            disabled_toolkits=["heptapod"],
            disabled_tools=["aster__heavy"],
        ),
    )
    save_serve_config(cfg, p)
    reloaded = load_serve_config(p)
    assert reloaded.default.profile == "paper"
    assert reloaded.default.disabled_toolkits == ["heptapod"]
    assert reloaded.default.disabled_tools == ["aster__heavy"]


def test_save_empty_config_drops_empty_keys(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    save_serve_config(ServeConfig(), p)
    reloaded = load_serve_config(p)
    assert reloaded.default.profile is None
    assert reloaded.default.disabled_toolkits == []
    # An empty config serializes to an empty mapping (no stray keys).
    assert (yaml.safe_load(p.read_text()) or {}) == {}


# ── two-layer merge ──────────────────────────────────────────────────────────


def test_merge_profile_project_wins():
    user = ServeConfig(default=DefaultBlock(profile="user-default"))
    project = ServeConfig(default=DefaultBlock(profile="proj-default"))
    merged = merge_serve_configs(user, project)
    assert merged.default.profile == "proj-default"


def test_merge_profile_falls_through_to_user():
    user = ServeConfig(default=DefaultBlock(profile="user-default"))
    project = ServeConfig(default=DefaultBlock())  # no profile
    merged = merge_serve_configs(user, project)
    assert merged.default.profile == "user-default"


def test_merge_disabled_lists_union():
    user = ServeConfig(default=DefaultBlock(
        disabled_toolkits=["a"], disabled_tools=["x__1"],
    ))
    project = ServeConfig(default=DefaultBlock(
        disabled_toolkits=["b", "a"], disabled_tools=["y__2"],
    ))
    merged = merge_serve_configs(user, project)
    # union, de-duped, order preserved (user first, then project extras)
    assert merged.default.disabled_toolkits == ["a", "b"]
    assert merged.default.disabled_tools == ["x__1", "y__2"]


# ── _split_tool ──────────────────────────────────────────────────────────────


def test_split_tool_valid():
    assert _split_tool("aster__transit") == ("aster", "transit")


@pytest.mark.parametrize("bad", ["no-delimiter", "__tool", "toolkit__", "__"])
def test_split_tool_malformed_errors(bad: str):
    with pytest.raises(ServeConfigError):
        _split_tool(bad)
