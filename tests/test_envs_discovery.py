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


def test_bare_dot_toolbase_is_a_project(tmp_path, fake_home):
    """The directory is the marker.

    It used to be ``manifest.yaml`` inside it, which meant a project
    holding only config or a loadout wasn't discoverable, and every
    command that wanted a project fabricated an empty manifest just to
    be found — a versioning file written by commands with no opinion
    about versions.
    """
    project = tmp_path / "repo"
    (project / ".toolbase").mkdir(parents=True)
    assert discovery.find_project_root(cwd=project) == project


def test_project_with_only_a_loadout_is_found(tmp_path, fake_home):
    project = tmp_path / "repo"
    loadouts = project / ".toolbase" / "loadouts"
    loadouts.mkdir(parents=True)
    (loadouts / "default.yaml").write_text("toolkits: {}\n")
    assert discovery.find_project_root(cwd=project) == project


def test_legacy_manifest_still_marks_a_project(tmp_path, fake_home):
    """Pre-0.12 layouts keep resolving."""
    project = tmp_path / "old"
    sc = project / ".toolbase"
    sc.mkdir(parents=True)
    (sc / "manifest.yaml").write_text("toolkits: []\n")
    assert discovery.find_project_root(cwd=project) == project


def test_a_file_named_dot_toolbase_is_not_a_project(tmp_path, fake_home):
    """Defends the walk against odd layouts: the marker must be a dir."""
    project = tmp_path / "weird"
    project.mkdir()
    (project / ".toolbase").write_text("not a directory\n")
    assert discovery.find_project_root(cwd=project) is None


def test_walk_stops_at_the_nearest_project(tmp_path, fake_home):
    """Nested projects: the closest one wins."""
    outer = tmp_path / "outer"
    inner = outer / "sub" / "inner"
    (outer / ".toolbase").mkdir(parents=True)
    (inner / ".toolbase").mkdir(parents=True)
    assert discovery.find_project_root(cwd=inner) == inner


def test_user_config_dir_is_never_a_project(tmp_path, monkeypatch):
    """``~/.toolbase/`` is config, not a project.

    It is a directory like any other, so once the marker became the
    directory rather than a file inside it, nothing else stopped the
    walk from calling the home directory a project — and every command
    run anywhere beneath it would have resolved there instead of the
    user default.
    """
    from toolbase import config as toolbase_config
    home = tmp_path / "home"
    (home / ".toolbase").mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", home / ".toolbase")

    assert discovery.find_project_root(cwd=home) is None
    nested = home / "some" / "where"
    nested.mkdir(parents=True)
    assert discovery.find_project_root(cwd=nested) is None


def test_a_real_project_under_home_is_still_found(tmp_path, monkeypatch):
    """Excluding the config dir must not blind the walk to projects
    that live beneath it on disk."""
    from toolbase import config as toolbase_config
    home = tmp_path / "home"
    (home / ".toolbase").mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", home / ".toolbase")

    repo = home / "code" / "repo"
    (repo / ".toolbase").mkdir(parents=True)
    assert discovery.find_project_root(cwd=repo) == repo
