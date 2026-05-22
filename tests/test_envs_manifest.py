"""Tests for ``toolbase.envs.manifest`` — pin list round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase.envs import manifest as manifest_mod


def test_load_manifest_returns_empty_when_file_absent(tmp_path):
    m = manifest_mod.load_manifest(tmp_path / "no.yaml")
    assert m.toolkits == []


def test_save_and_load_round_trip(tmp_path):
    m = manifest_mod.Manifest(
        toolkits=[
            manifest_mod.ManifestEntry(
                name="heptapod", version="0.3.0",
                pinned_at="2026-05-12T10:00:00",
            ),
            manifest_mod.ManifestEntry(
                name="arxiv-search", version="0.2.0",
                pinned_at="2026-05-12T11:00:00",
            ),
        ],
    )
    path = tmp_path / "manifest.yaml"
    manifest_mod.save_manifest(path, m)
    assert path.exists()

    loaded = manifest_mod.load_manifest(path)
    # Entries are sorted by name on write — arxiv-search before heptapod.
    names = [e.name for e in loaded.toolkits]
    assert names == ["arxiv-search", "heptapod"]
    assert loaded.find("heptapod").version == "0.3.0"
    assert loaded.find("arxiv-search").version == "0.2.0"


def test_save_writes_schema_version(tmp_path):
    m = manifest_mod.Manifest(
        toolkits=[manifest_mod.ManifestEntry("x", "1.0.0", "")],
    )
    path = tmp_path / "m.yaml"
    manifest_mod.save_manifest(path, m)
    body = path.read_text()
    assert "schema_version" in body


def test_add_pin_to_empty_manifest(tmp_path):
    path = tmp_path / "m.yaml"
    result = manifest_mod.add_pin(path, "heptapod", "0.3.0")
    assert len(result.toolkits) == 1
    assert result.toolkits[0].name == "heptapod"
    assert result.toolkits[0].version == "0.3.0"
    assert result.toolkits[0].pinned_at  # non-empty timestamp


def test_add_pin_replaces_existing(tmp_path):
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "heptapod", "0.1.0", pinned_at="early")
    result = manifest_mod.add_pin(path, "heptapod", "0.3.0", pinned_at="later")
    assert len(result.toolkits) == 1
    assert result.toolkits[0].version == "0.3.0"
    assert result.toolkits[0].pinned_at == "later"


def test_add_pin_multiple_toolkits(tmp_path):
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "heptapod", "0.3.0")
    manifest_mod.add_pin(path, "arxiv-search", "0.2.0")
    loaded = manifest_mod.load_manifest(path)
    names = sorted(e.name for e in loaded.toolkits)
    assert names == ["arxiv-search", "heptapod"]


def test_remove_pin_returns_true_on_removal(tmp_path):
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "x", "1.0")
    assert manifest_mod.remove_pin(path, "x") is True
    loaded = manifest_mod.load_manifest(path)
    assert loaded.toolkits == []


def test_remove_pin_returns_false_when_absent(tmp_path):
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "x", "1.0")
    assert manifest_mod.remove_pin(path, "y") is False


def test_remove_pin_returns_false_when_file_absent(tmp_path):
    assert manifest_mod.remove_pin(tmp_path / "no.yaml", "x") is False


def test_get_pin_returns_entry(tmp_path):
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "x", "1.0")
    entry = manifest_mod.get_pin(path, "x")
    assert entry is not None
    assert entry.version == "1.0"


def test_get_pin_returns_none_when_absent(tmp_path):
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "x", "1.0")
    assert manifest_mod.get_pin(path, "y") is None


def test_get_pin_returns_none_when_file_absent(tmp_path):
    assert manifest_mod.get_pin(tmp_path / "no.yaml", "x") is None


def test_atomic_write_crash_safety(tmp_path, monkeypatch):
    """If os.replace fails, the .tmp file is left behind but the
    original (or its absence) is intact."""
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "x", "1.0")
    original = path.read_text()

    # Simulate failure mid-write: monkeypatch os.replace to raise.
    import os as os_mod

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(os_mod, "replace", boom)

    with pytest.raises(OSError):
        manifest_mod.add_pin(path, "y", "2.0")

    # Original file is intact (the .tmp may or may not exist; we just
    # need to confirm the actual manifest wasn't corrupted).
    assert path.read_text() == original


def test_manifest_entry_from_dict_rejects_missing_name():
    with pytest.raises(ValueError):
        manifest_mod.ManifestEntry.from_dict({"version": "1.0"})


def test_manifest_entry_from_dict_rejects_missing_version():
    with pytest.raises(ValueError):
        manifest_mod.ManifestEntry.from_dict({"name": "x"})


def test_manifest_from_dict_rejects_non_list_toolkits():
    with pytest.raises(ValueError):
        manifest_mod.Manifest.from_dict({"toolkits": "not a list"})


def test_load_rejects_too_new_schema(tmp_path):
    """Schema-too-new propagates from the read path."""
    from toolbase.envs.schema import SchemaTooNewError
    path = tmp_path / "m.yaml"
    path.write_text("schema_version: 99\ntoolkits: []\n")
    with pytest.raises(SchemaTooNewError):
        manifest_mod.load_manifest(path)


def test_save_then_inspect_yaml_format(tmp_path):
    """The on-disk shape is block style, schema_version stamped, sorted."""
    path = tmp_path / "m.yaml"
    manifest_mod.add_pin(path, "z", "1.0", pinned_at="t1")
    manifest_mod.add_pin(path, "a", "2.0", pinned_at="t2")
    body = path.read_text()
    # Block style — entries on their own lines.
    assert "name: a" in body
    assert "name: z" in body
    # a comes before z (sorted on write).
    assert body.index("name: a") < body.index("name: z")
