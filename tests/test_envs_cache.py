"""Tests for ``toolbase.envs.cache`` — cache-slot metadata helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from toolbase import config as toolbase_config
from toolbase.envs import cache as cache_mod


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def _make_slot(fake_home: Path, name: str, version: str) -> Path:
    slot = fake_home / "cache" / name / version
    slot.mkdir(parents=True)
    return slot


# ── install_meta ────────────────────────────────────────────────────


def test_write_and_read_install_meta(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    cache_mod.write_install_meta(
        slot,
        name="aster",
        version="1.0.0",
        install_method="venv",
        python_version="3.12",
        extras={"python_path": "/path/to/python"},
    )
    meta = cache_mod.read_install_meta(slot)
    assert meta["name"] == "aster"
    assert meta["version"] == "1.0.0"
    assert meta["install_method"] == "venv"
    assert meta["python_version"] == "3.12"
    assert meta["python_path"] == "/path/to/python"
    assert "installed_at" in meta


def test_read_install_meta_missing_returns_none(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    assert cache_mod.read_install_meta(slot) is None


def test_install_meta_carries_schema_version(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    cache_mod.write_install_meta(
        slot,
        name="aster", version="1.0.0",
        install_method="venv", python_version="3.12",
    )
    body = (slot / cache_mod.INSTALL_META_FILE).read_text()
    assert "schema_version" in body


# ── legacy_meta carry ───────────────────────────────────────────────


def test_write_and_read_legacy_meta(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    payload = {
        "name": "aster",
        "version": "1.0.0",
        "environment": "venv",
        "python_path": "/p/p",
    }
    cache_mod.write_legacy_meta(slot, payload)
    assert cache_mod.read_legacy_meta(slot) == payload


def test_read_legacy_meta_missing_returns_empty_dict(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    assert cache_mod.read_legacy_meta(slot) == {}


def test_read_legacy_meta_malformed_returns_empty_dict(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    (slot / cache_mod.LEGACY_META_FILE).write_text("not-json{")
    assert cache_mod.read_legacy_meta(slot) == {}


# ── last_used ───────────────────────────────────────────────────────


def test_touch_last_used_creates_file_with_timestamp(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    cache_mod.touch_last_used(slot)
    stamp = cache_mod.read_last_used(slot)
    assert stamp is not None
    # Parseable as ISO-8601.
    datetime.fromisoformat(stamp)


def test_touch_last_used_overwrites_atomically(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    cache_mod.touch_last_used(slot, when=datetime(2026, 5, 1, 12, 0, 0))
    cache_mod.touch_last_used(slot, when=datetime(2026, 5, 12, 9, 0, 0))
    stamp = cache_mod.read_last_used(slot)
    assert stamp.startswith("2026-05-12")
    # No .tmp left behind.
    assert not (slot / (cache_mod.LAST_USED_FILE + ".tmp")).exists()


def test_read_last_used_missing_returns_none(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    assert cache_mod.read_last_used(slot) is None


def test_touch_last_used_tolerant_of_unwritable_path(tmp_path):
    """Last-used is best-effort; a non-existent dir doesn't crash."""
    # If slot dir doesn't exist, the helper should silently fail.
    slot = tmp_path / "non-existent"  # no mkdir
    # Should not raise.
    cache_mod.touch_last_used(slot)


# ── disk_size ───────────────────────────────────────────────────────


def test_compute_and_write_disk_size_writes_byte_count(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    (slot / "f1.bin").write_bytes(b"x" * 1000)
    (slot / "f2.bin").write_bytes(b"y" * 500)
    size = cache_mod.compute_and_write_disk_size(slot)
    # ~1500 (plus the .disk_size file itself, since we walk after write
    # — actually we walk before write). The walk happens before any
    # writes; we accept >=1500 because the result file is written *after*
    # the walk completes.
    assert size is not None
    assert size >= 1500
    assert cache_mod.read_disk_size(slot) == size


def test_read_disk_size_missing_returns_none(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    assert cache_mod.read_disk_size(slot) is None


def test_compute_disk_size_respects_budget(fake_home, monkeypatch):
    """If the walk would exceed the budget, return None and don't write."""
    slot = _make_slot(fake_home, "aster", "1.0.0")
    (slot / "f.bin").write_bytes(b"x")
    # Make ``time.monotonic()`` claim we're already past the deadline.
    import toolbase.envs.cache as cmod
    monotonic_calls = [0.0, 9999.0, 9999.0, 9999.0]
    def fake_monotonic():
        return monotonic_calls.pop(0) if monotonic_calls else 9999.0
    monkeypatch.setattr(cmod.time, "monotonic", fake_monotonic)
    out = cache_mod.compute_and_write_disk_size(slot, budget_seconds=0.001)
    assert out is None
    # No file written.
    assert not (slot / cache_mod.DISK_SIZE_FILE).exists()


def test_read_disk_size_malformed_returns_none(fake_home):
    slot = _make_slot(fake_home, "aster", "1.0.0")
    (slot / cache_mod.DISK_SIZE_FILE).write_text("not-a-number\n")
    assert cache_mod.read_disk_size(slot) is None


# ── walk_cache ──────────────────────────────────────────────────────


def test_walk_cache_empty(fake_home):
    assert cache_mod.walk_cache() == []


def _seed_install(fake_home: Path, name: str, version: str) -> Path:
    slot = _make_slot(fake_home, name, version)
    cache_mod.write_install_meta(
        slot,
        name=name, version=version,
        install_method="venv", python_version="3.12",
    )
    return slot


def test_walk_cache_returns_installs(fake_home):
    _seed_install(fake_home, "aster", "1.0.0")
    _seed_install(fake_home, "aster", "1.1.0")
    _seed_install(fake_home, "arxiv-search", "0.2.0")
    entries = cache_mod.walk_cache()
    keys = [(e.name, e.version) for e in entries]
    assert keys == [
        ("arxiv-search", "0.2.0"),
        ("aster", "1.0.0"),
        ("aster", "1.1.0"),
    ]


def test_walk_cache_skips_partial_install_with_no_meta(fake_home):
    """A version dir without .install_meta.yaml or .stk_meta.json is skipped."""
    slot = fake_home / "cache" / "aster" / "1.0.0"
    slot.mkdir(parents=True)
    # No meta file written.
    (slot / "tools").mkdir()
    assert cache_mod.walk_cache() == []


def test_walk_cache_accepts_legacy_meta_only(fake_home):
    """A version slot with only the .stk_meta.json carry survives the walk."""
    slot = _make_slot(fake_home, "aster", "1.0.0")
    cache_mod.write_legacy_meta(slot, {
        "name": "aster", "version": "1.0.0",
        "environment": "venv", "python_path": "/p",
    })
    entries = cache_mod.walk_cache()
    assert len(entries) == 1
    assert entries[0].legacy_meta["python_path"] == "/p"
    assert entries[0].install_meta == {}


def test_walk_cache_skips_underscore_prefixed_names(fake_home):
    """Phase 3C's _setup_validate.json (file) or hypothetical _foo dirs."""
    (fake_home / "cache").mkdir(parents=True, exist_ok=True)
    (fake_home / "cache" / "_setup_validate.json").write_text("{}")
    udir = fake_home / "cache" / "_foo"
    udir.mkdir()
    _seed_install(fake_home, "aster", "1.0.0")
    entries = cache_mod.walk_cache()
    assert [e.name for e in entries] == ["aster"]


def test_walk_cache_skips_dot_prefixed_versions(fake_home):
    """A dot-prefixed sub-dir (e.g., .DS_Store directory) is ignored."""
    _seed_install(fake_home, "aster", "1.0.0")
    weird = fake_home / "cache" / "aster" / ".DS_Store"
    weird.mkdir()
    entries = cache_mod.walk_cache()
    assert [(e.name, e.version) for e in entries] == [("aster", "1.0.0")]


def test_walk_cache_skips_files_at_name_level(fake_home):
    """A file under cache/ (like the downloads cache) is not a toolkit name."""
    (fake_home / "cache").mkdir(parents=True)
    (fake_home / "cache" / "loose-file.bin").write_bytes(b"x")
    _seed_install(fake_home, "aster", "1.0.0")
    entries = cache_mod.walk_cache()
    assert [e.name for e in entries] == ["aster"]


def test_walk_cache_skips_files_at_version_level(fake_home):
    """A file under cache/<name>/ (like a download artifact) isn't a version."""
    _seed_install(fake_home, "aster", "1.0.0")
    (fake_home / "cache" / "aster" / "downloaded.tar.gz").write_bytes(b"x")
    entries = cache_mod.walk_cache()
    assert [e.version for e in entries] == ["1.0.0"]


def test_walk_cache_populates_last_used_and_disk_size(fake_home):
    slot = _seed_install(fake_home, "aster", "1.0.0")
    cache_mod.touch_last_used(slot)
    (slot / "f.bin").write_bytes(b"x" * 100)
    cache_mod.compute_and_write_disk_size(slot)
    entries = cache_mod.walk_cache()
    assert entries[0].last_used_iso is not None
    assert entries[0].disk_size_bytes is not None and entries[0].disk_size_bytes >= 100


def test_list_versions_filters_by_name(fake_home):
    _seed_install(fake_home, "aster", "1.0.0")
    _seed_install(fake_home, "aster", "1.1.0")
    _seed_install(fake_home, "arxiv-search", "0.1.0")
    assert sorted(cache_mod.list_versions("aster")) == ["1.0.0", "1.1.0"]
    assert cache_mod.list_versions("none") == []


def test_find_slot_returns_entry_or_none(fake_home):
    _seed_install(fake_home, "aster", "1.0.0")
    e = cache_mod.find_slot("aster", "1.0.0")
    assert e is not None
    assert e.version == "1.0.0"
    assert cache_mod.find_slot("aster", "9.9.9") is None
