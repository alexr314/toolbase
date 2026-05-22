"""Tests for ``toolbase.envs.paths`` — pure-functional path computation.

These tests cover the basic shape of the layout. Behavior-changing
tests (cache_dir actually used by install, etc.) live in the Phase 2
test files; Phase 1 just pins the substrate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase import config as toolbase_config
from toolbase.envs import paths


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR to a tmp ~/.toolbase so tests don't leak.

    See HANDOFF.md gotcha #12 — patching ``toolbase.config.CONFIG_DIR``
    is the canonical way to redirect the substrate. The resolver pattern
    in ``envs.paths`` re-reads this on every call.
    """
    fake = tmp_path / ".toolbase"
    fake.mkdir()
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def test_cache_root_returns_under_user_root(fake_home):
    assert paths.cache_root() == fake_home / "cache"


def test_cache_dir_combines_name_and_version(fake_home):
    p = paths.cache_dir("heptapod", "0.3.0")
    assert p == fake_home / "cache" / "heptapod" / "0.3.0"


def test_cache_dir_does_not_create_directory(fake_home):
    p = paths.cache_dir("heptapod", "0.3.0")
    assert not p.exists()


def test_user_config_path_under_config_subdir(fake_home):
    p = paths.user_config_path("arxiv-search")
    assert p == fake_home / "config" / "arxiv-search.yaml"


def test_default_project_root_under_user_root(fake_home):
    p = paths.default_project_root()
    assert p == fake_home / "default-project"


def test_legacy_toolkits_dir_returns_old_path(fake_home):
    """Phase 6 detects this dir to print the 'run tb reset' message."""
    assert paths.legacy_toolkits_dir() == fake_home / "toolkits"


def test_project_manifest_path_for_real_project(tmp_path, fake_home):
    """Real projects: manifest is under .toolbase/manifest.yaml."""
    project = tmp_path / "myproj"
    project.mkdir()
    assert paths.project_manifest_path(project) == (
        project / ".toolbase" / "manifest.yaml"
    )


def test_project_manifest_path_for_default_project(fake_home):
    """Default-project special case: manifest.yaml lives directly inside it,
    not under a nested ``.toolbase/`` segment (the dir IS the project)."""
    dp = paths.default_project_root()
    assert paths.project_manifest_path(dp) == dp / "manifest.yaml"


def test_project_config_path_for_real_project(tmp_path, fake_home):
    project = tmp_path / "myproj"
    project.mkdir()
    assert paths.project_config_path(project, "aster") == (
        project / ".toolbase" / "config" / "aster.yaml"
    )


def test_project_config_path_for_default_project(fake_home):
    dp = paths.default_project_root()
    assert paths.project_config_path(dp, "aster") == (
        dp / "config" / "aster.yaml"
    )


def test_path_resolution_re_reads_config_dir_at_call_time(tmp_path, monkeypatch):
    """Resolver pattern: changing CONFIG_DIR mid-flight is honored."""
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", first)
    assert paths.cache_root() == first / "cache"
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", second)
    assert paths.cache_root() == second / "cache"


def test_base_kwarg_overrides_resolved_path(fake_home, tmp_path):
    """Explicit ``base`` short-circuits the resolver — useful for testing."""
    other = tmp_path / "other"
    assert paths.cache_root(base=other) == other / "cache"
