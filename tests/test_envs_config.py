"""Tests for ``toolbase.envs.config`` — two-layer config resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase import config as toolbase_config
from toolbase.envs import config as envs_config_mod
from toolbase.envs import paths


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def _write_user_config(fake_home: Path, toolkit: str, body: str) -> Path:
    """Drop a user-level config file at ``~/.toolbase/config/<toolkit>.yaml``."""
    p = fake_home / "config" / f"{toolkit}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _write_project_config(
    project: Path, toolkit: str, body: str,
) -> Path:
    """Drop a project-level config file at ``<project>/.toolbase/config/<toolkit>.yaml``."""
    p = project / ".toolbase" / "config" / f"{toolkit}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_no_files_returns_empty(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out == {}


def test_user_only_surfaces(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(fake_home, "aster", "api_key: abc\n")
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out == {"api_key": "abc"}


def test_project_only_surfaces(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_config(project, "aster", "opacity_path: /data/ops\n")
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out == {"opacity_path": "/data/ops"}


def test_project_overrides_user_key_by_key(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(
        fake_home, "aster",
        "api_key: USER-KEY\nopacity_path: /home/user/opacities\n",
    )
    _write_project_config(
        project, "aster",
        "opacity_path: /scratch/proj-opacities\n",
    )
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out["api_key"] == "USER-KEY"  # only in user
    assert out["opacity_path"] == "/scratch/proj-opacities"  # project wins


def test_project_only_keys_are_merged_in(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(fake_home, "aster", "api_key: abc\n")
    _write_project_config(project, "aster", "extra_flag: true\n")
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out["api_key"] == "abc"
    assert out["extra_flag"] is True


def test_schema_version_is_stripped(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(
        fake_home, "aster",
        "schema_version: 1\napi_key: abc\n",
    )
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert "schema_version" not in out
    assert out["api_key"] == "abc"


def test_legacy_user_config_without_schema_version_works(tmp_path, fake_home):
    """Phase 3C files have no schema_version; treat as v0 (legacy), pass through."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(fake_home, "aster", "api_key: abc\n")
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out == {"api_key": "abc"}


def test_needs_value_sentinel_overridden_by_project(tmp_path, fake_home):
    """The Phase 3C <NEEDS VALUE> sentinel is just a string. Project wins."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(fake_home, "aster", "api_key: '<NEEDS VALUE>'\n")
    _write_project_config(project, "aster", "api_key: real-key\n")
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out["api_key"] == "real-key"


def test_needs_value_propagates_when_no_project_override(tmp_path, fake_home):
    """Sentinel stays if project doesn't override — serve's 3C gate fires."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(fake_home, "aster", "api_key: '<NEEDS VALUE>'\n")
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out["api_key"] == "<NEEDS VALUE>"


def test_load_user_layer_alone(tmp_path, fake_home):
    """The lower-level layer accessor returns user data only."""
    _write_user_config(fake_home, "aster", "api_key: abc\n")
    out = envs_config_mod.load_user_config_layer("aster")
    assert out == {"api_key": "abc"}


def test_load_project_layer_alone(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_config(project, "aster", "opacity: /data\n")
    out = envs_config_mod.load_project_config_layer("aster", project)
    assert out == {"opacity": "/data"}


def test_nested_value_is_replaced_wholesale_not_deep_merged(tmp_path, fake_home):
    """Per-key shallow merge: nested mappings get replaced, not merged."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_user_config(
        fake_home, "aster",
        "options:\n  a: 1\n  b: 2\n",
    )
    _write_project_config(
        project, "aster",
        "options:\n  a: 99\n",  # 'b' is NOT preserved
    )
    out = envs_config_mod.resolve_toolkit_config("aster", project)
    assert out["options"] == {"a": 99}
