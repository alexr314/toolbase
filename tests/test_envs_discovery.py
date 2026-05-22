"""Tests for ``toolbase.envs.discovery`` — project-root walk.

The walk is upward from ``cwd``, looking for ``.toolbase/manifest.yaml``.
Fallback is the default-project. Override (``--project-dir``) shortcircuits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase import config as toolbase_config
from toolbase.envs import discovery, paths


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def _drop_manifest(project_dir: Path) -> Path:
    """Create a minimal ``.toolbase/manifest.yaml`` and return its path."""
    sc = project_dir / ".toolbase"
    sc.mkdir(parents=True, exist_ok=True)
    manifest = sc / "manifest.yaml"
    manifest.write_text("schema_version: 1\ntoolkits: []\n")
    return manifest


def test_find_project_root_finds_in_cwd(tmp_path, fake_home):
    project = tmp_path / "myproj"
    project.mkdir()
    _drop_manifest(project)
    found = discovery.find_project_root(cwd=project)
    assert found == project.resolve()


def test_find_project_root_walks_upward_three_levels(tmp_path, fake_home):
    project = tmp_path / "myproj"
    sub = project / "src" / "deep" / "deeper"
    sub.mkdir(parents=True)
    _drop_manifest(project)
    found = discovery.find_project_root(cwd=sub)
    assert found == project.resolve()


def test_find_project_root_returns_none_when_no_manifest(tmp_path, fake_home):
    # tmp_path itself has no .toolbase/manifest.yaml anywhere upward,
    # but the walk terminates at filesystem root and returns None.
    cwd = tmp_path / "no-project-here"
    cwd.mkdir()
    found = discovery.find_project_root(cwd=cwd)
    assert found is None


def test_find_project_root_terminates_at_filesystem_root(fake_home):
    # Asking discovery to walk up from ``/`` should not loop forever.
    found = discovery.find_project_root(cwd=Path("/"))
    # Either None (typical case — no /.toolbase/manifest.yaml in CI)
    # or the discovered path if the developer's host has one. Either
    # way it terminates quickly.
    assert found is None or isinstance(found, Path)


def test_override_short_circuits(tmp_path, fake_home):
    """Explicit override path is returned without walking — even if it
    doesn't have a manifest yet."""
    forced = tmp_path / "force-this"
    found = discovery.find_project_root(cwd=tmp_path, override=forced)
    assert found == forced.resolve()


def test_project_root_or_default_falls_back(tmp_path, fake_home):
    """No manifest anywhere → fall through to default-project path."""
    cwd = tmp_path / "no-project-here"
    cwd.mkdir()
    root = discovery.project_root_or_default(cwd=cwd)
    assert root == paths.default_project_root()


def test_project_root_or_default_prefers_walk_over_fallback(
    tmp_path, fake_home,
):
    """If a manifest exists up the tree, prefer it over default-project."""
    project = tmp_path / "myproj"
    project.mkdir()
    _drop_manifest(project)
    sub = project / "src" / "deep"
    sub.mkdir(parents=True)
    root = discovery.project_root_or_default(cwd=sub)
    assert root == project.resolve()


def test_project_root_or_default_override_wins(tmp_path, fake_home):
    """Override wins over both walk and fallback."""
    project = tmp_path / "myproj"
    project.mkdir()
    _drop_manifest(project)
    forced = tmp_path / "force-this"
    root = discovery.project_root_or_default(cwd=project, override=forced)
    assert root == forced.resolve()


def test_walk_treats_directory_manifest_as_no_match(tmp_path, fake_home):
    """A ``.toolbase/manifest.yaml`` *directory* (not file) isn't a hit.

    Edge case: protects against weird layouts. We require a file.
    """
    project = tmp_path / "weird"
    sc = project / ".toolbase"
    sc.mkdir(parents=True)
    # manifest.yaml is a directory, not a file.
    (sc / "manifest.yaml").mkdir()
    assert discovery.find_project_root(cwd=project) is None


def test_walk_skips_to_actual_match_not_just_dottoolbase(tmp_path, fake_home):
    """A bare ``.toolbase/`` dir without ``manifest.yaml`` shouldn't match.

    Project-root only counts if the manifest file is present.
    """
    project_no_manifest = tmp_path / "incomplete"
    (project_no_manifest / ".toolbase").mkdir(parents=True)
    # No manifest.yaml dropped.
    assert discovery.find_project_root(cwd=project_no_manifest) is None
