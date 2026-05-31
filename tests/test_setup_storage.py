"""Unit tests for the Phase 3C-1 config storage layer.

Covers ``toolbase/setup/storage.py``: per-toolkit YAML read/write,
0600 permissions, comment round-tripping via ruamel.yaml, and the
resolver pattern that keeps test fixtures honest (HANDOFF.md gotcha #12).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from toolbase import config as toolbase_config
from toolbase.setup import storage


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CONFIG_DIR to tmp_path. Same pattern as auth-side tests."""
    fake = tmp_path / "toolbase"
    fake.mkdir()
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


# ── path helpers ──────────────────────────────────────────────────────


def test_config_dir_is_under_config_dir(isolated_config: Path):
    assert storage.config_dir() == isolated_config / "config"


def test_config_dir_is_pure_path_resolution(isolated_config: Path):
    # Pure path resolver: must NOT create the directory on disk. Writers
    # (save_config etc.) create parents lazily at write time so calling
    # config_path/config_dir during a read-only lookup doesn't leave an
    # empty `~/.toolbase/config/` (or `<project>/.toolbase/config/`)
    # behind for the user to wonder about.
    assert not (isolated_config / "config").exists()
    d = storage.config_dir()
    assert d == isolated_config / "config"
    assert not d.exists()
    # save_config creates the parent on demand.
    storage.save_config("demo", {"k": "v"})
    assert d.exists()


def test_project_config_dir_is_pure_path_resolution(tmp_path: Path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    d = storage.project_config_dir(project_root)
    assert d == project_root / ".toolbase" / "config"
    # Resolution must not have created anything.
    assert not d.exists()
    assert not (project_root / ".toolbase").exists()


def test_config_path_is_per_toolkit(isolated_config: Path):
    assert storage.config_path("aster") == isolated_config / "config" / "aster.yaml"


def test_config_path_does_not_create_file(isolated_config: Path):
    p = storage.config_path("aster")
    assert not p.exists()


def test_resolver_re_reads_config_dir(tmp_path: Path, monkeypatch):
    """Patching toolbase.config.CONFIG_DIR redirects without bound defaults
    sticking. HANDOFF gotcha #12 in action.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", a)
    assert storage.config_path("demo") == a / "config" / "demo.yaml"

    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", b)
    assert storage.config_path("demo") == b / "config" / "demo.yaml"


# ── load / save round-trip ───────────────────────────────────────────


def test_load_missing_returns_empty(isolated_config: Path):
    data = storage.load_config("ghost")
    assert dict(data) == {}


def test_save_then_load_round_trip(isolated_config: Path):
    storage.save_config("demo", {"api_key": "tb_user_xx", "count": 4})
    data = storage.load_config("demo")
    # 0.5.0: schema_version: 1 is stamped on every save. Strip the
    # envelope from the comparison; we care about the body.
    body = {k: v for k, v in data.items() if k != "schema_version"}
    assert body == {"api_key": "tb_user_xx", "count": 4}
    assert data["schema_version"] == 1


def test_save_sets_0600_on_posix(isolated_config: Path):
    storage.save_config("demo", {"api_key": "x"})
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(storage.config_path("demo")).st_mode)
        assert mode == 0o600


def test_save_is_atomic_no_tmp_left_behind(isolated_config: Path):
    storage.save_config("demo", {"x": 1})
    cfg = storage.config_path("demo")
    tmp = cfg.with_suffix(cfg.suffix + ".tmp")
    assert cfg.exists()
    assert not tmp.exists()


# ── comment preservation (the load-bearing reason for ruamel.yaml) ──


def test_comments_survive_round_trip(isolated_config: Path):
    """User-authored comments must NOT be stripped by save_config when the
    data was loaded via load_config."""
    cfg = storage.config_path("demo")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "# this comment must survive\n"
        "api_key: original\n"
        "# inline section comment\n"
        "max_workers: 4\n"
    )

    data = storage.load_config("demo")
    data["max_workers"] = 8  # mutate one field
    storage.save_config("demo", data)

    text = cfg.read_text()
    assert "# this comment must survive" in text
    assert "# inline section comment" in text
    assert "max_workers: 8" in text


def test_header_comment_attached_on_first_save(isolated_config: Path):
    storage.save_config(
        "demo",
        {"api_key": "x"},
        header_comment="# Configuration for demo\n# Edit anytime.",
    )
    text = storage.config_path("demo").read_text()
    assert "Configuration for demo" in text
    assert "Edit anytime." in text


def test_header_comment_not_duplicated_on_resave(isolated_config: Path):
    storage.save_config(
        "demo",
        {"x": 1},
        header_comment="# header",
    )
    data = storage.load_config("demo")
    data["x"] = 2
    storage.save_config("demo", data, header_comment="# header")
    text = storage.config_path("demo").read_text()
    assert text.count("# header") == 1


# ── single-field mutators ────────────────────────────────────────────


def test_set_config_value_creates_file_if_missing(isolated_config: Path):
    storage.set_config_value("demo", "api_key", "x")
    assert storage.load_config("demo")["api_key"] == "x"


def test_set_config_value_preserves_other_fields(isolated_config: Path):
    storage.save_config("demo", {"a": 1, "b": 2})
    storage.set_config_value("demo", "a", 99)
    data = storage.load_config("demo")
    body = {k: v for k, v in data.items() if k != "schema_version"}
    assert body == {"a": 99, "b": 2}


def test_unset_config_value_removes_field(isolated_config: Path):
    storage.save_config("demo", {"a": 1, "b": 2})
    removed = storage.unset_config_value("demo", "a")
    assert removed is True
    body = {k: v for k, v in storage.load_config("demo").items() if k != "schema_version"}
    assert body == {"b": 2}


def test_unset_config_value_missing_key_returns_false(isolated_config: Path):
    storage.save_config("demo", {"a": 1})
    removed = storage.unset_config_value("demo", "nonexistent")
    assert removed is False


# ── delete + edge cases ──────────────────────────────────────────────


def test_delete_config_removes_file(isolated_config: Path):
    storage.save_config("demo", {"x": 1})
    assert storage.delete_config("demo") is True
    assert not storage.config_path("demo").exists()


def test_delete_config_missing_returns_false(isolated_config: Path):
    assert storage.delete_config("ghost") is False


def test_load_malformed_yaml_raises_value_error(isolated_config: Path):
    cfg = storage.config_path("bad")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(":\n  - this isn't a valid mapping\n[}")
    with pytest.raises(ValueError, match="failed to parse"):
        storage.load_config("bad")


def test_load_top_level_list_raises(isolated_config: Path):
    """Toolkit config must be a mapping at the top, not a list/scalar."""
    cfg = storage.config_path("listy")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="expected a YAML mapping"):
        storage.load_config("listy")


def test_load_empty_file_returns_empty_map(isolated_config: Path):
    cfg = storage.config_path("blank")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("")
    data = storage.load_config("blank")
    assert dict(data) == {}
