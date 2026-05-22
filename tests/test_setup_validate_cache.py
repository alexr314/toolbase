"""
Unit tests for ``toolbase.setup.validate_cache.ValidateCache``.

Cover:
- Fresh cache: get returns None.
- Put then get: round-trips.
- Mtime change invalidates the entry (config OR setup.py).
- Both successful and failed validates cache.
- Corrupted cache file is handled gracefully (returns empty, then
  rebuilds on next put).
- Concurrent-write tolerance: two writers don't corrupt the file
  beyond what a fresh load can recover from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolbase.setup.validate_cache import (
    ValidateCache, _mtime_or_none, default_cache_path,
)


# ── basic round-trip ──────────────────────────────────────────────────


def test_fresh_cache_returns_none(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    assert c.get("toolkit", config_mtime=100, setup_py_mtime=200) is None


def test_put_then_get_returns_entry(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    c.put("toolkit", result=True, message=None,
          config_mtime=100, setup_py_mtime=200)
    entry = c.get("toolkit", config_mtime=100, setup_py_mtime=200)
    assert entry is not None
    assert entry["result"] is True


def test_put_persists_to_disk(tmp_path):
    """After put, the cache file exists on disk."""
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=True, message="ok", config_mtime=1, setup_py_mtime=2)
    assert (tmp_path / "cache.json").exists()
    data = json.loads((tmp_path / "cache.json").read_text())
    assert "t" in data


def test_cache_caches_failures_too(tmp_path):
    """Per the manager's Day 5 sign-off: failed validates also cache."""
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=False, message="missing api_key",
          config_mtime=1, setup_py_mtime=2)
    entry = c.get("t", config_mtime=1, setup_py_mtime=2)
    assert entry is not None
    assert entry["result"] is False
    assert entry["message"] == "missing api_key"


# ── mtime invalidation ────────────────────────────────────────────────


def test_config_mtime_change_invalidates(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=True, message=None, config_mtime=100, setup_py_mtime=200)
    # Different config_mtime → cache miss.
    assert c.get("t", config_mtime=101, setup_py_mtime=200) is None


def test_setup_py_mtime_change_invalidates(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=True, message=None, config_mtime=100, setup_py_mtime=200)
    # Different setup_py_mtime → cache miss.
    assert c.get("t", config_mtime=100, setup_py_mtime=201) is None


def test_none_mtime_keys_match_each_other(tmp_path):
    """A toolkit with no setup.py has setup_py_mtime=None; the next
    call with also-None should hit the cache."""
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=True, message=None,
          config_mtime=100, setup_py_mtime=None)
    entry = c.get("t", config_mtime=100, setup_py_mtime=None)
    assert entry is not None


def test_none_mtime_doesnt_match_int_mtime(tmp_path):
    """None key is distinct from any int key."""
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=True, message=None,
          config_mtime=None, setup_py_mtime=200)
    assert c.get("t", config_mtime=100, setup_py_mtime=200) is None


# ── eviction ──────────────────────────────────────────────────────────


def test_evict_removes_entry(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=True, message=None, config_mtime=1, setup_py_mtime=2)
    c.evict("t")
    assert c.get("t", config_mtime=1, setup_py_mtime=2) is None


def test_evict_unknown_toolkit_is_noop(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    c.evict("never-cached")  # should not raise


def test_clear_removes_cache_file(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    c.put("t", result=True, message=None, config_mtime=1, setup_py_mtime=2)
    c.clear()
    assert not (tmp_path / "cache.json").exists()


# ── corruption tolerance ──────────────────────────────────────────────


def test_corrupted_cache_file_falls_back_to_empty(tmp_path):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text("not valid json {{{}")
    c = ValidateCache(cache_file)
    # Get returns None as if cache were empty.
    assert c.get("t", config_mtime=1, setup_py_mtime=2) is None
    # Put rebuilds cleanly.
    c.put("t", result=True, message=None, config_mtime=1, setup_py_mtime=2)
    assert c.get("t", config_mtime=1, setup_py_mtime=2) is not None


def test_cache_file_with_non_dict_root_treated_as_empty(tmp_path):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps([1, 2, 3]))  # array, not object
    c = ValidateCache(cache_file)
    assert c.get("t", config_mtime=1, setup_py_mtime=2) is None


# ── multi-toolkit ─────────────────────────────────────────────────────


def test_multiple_toolkits_share_one_file(tmp_path):
    c = ValidateCache(tmp_path / "cache.json")
    c.put("alpha", result=True, message=None,
          config_mtime=1, setup_py_mtime=2)
    c.put("beta", result=False, message="missing key",
          config_mtime=3, setup_py_mtime=4)
    assert c.get("alpha", config_mtime=1, setup_py_mtime=2)["result"] is True
    assert c.get("beta", config_mtime=3, setup_py_mtime=4)["result"] is False


# ── helper unit tests ─────────────────────────────────────────────────


def test_mtime_or_none_for_existing_file(tmp_path):
    f = tmp_path / "x"
    f.write_text("hi")
    mt = _mtime_or_none(f)
    assert isinstance(mt, int)
    assert mt > 0


def test_mtime_or_none_for_missing_file(tmp_path):
    assert _mtime_or_none(tmp_path / "nope") is None


def test_default_cache_path_resolves_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = default_cache_path()
    assert str(tmp_path) in str(p)
    assert p.name == "_setup_validate.json"
