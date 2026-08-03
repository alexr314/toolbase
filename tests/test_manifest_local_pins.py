"""The machine-local pin layer (manifest.local.yaml) and editable-shadow
visibility.

The committed manifest answers "what does this project depend on" —
true on every machine. "Resolve heptapod to the editable slot" is only
true on the machine holding that source checkout, so it belongs in a
gitignored local layer that merges over the committed manifest
(mirroring the user->project two-layer config merge). These tests pin:

  - load_merged_pins: local wins per name, absent layers contribute
    nothing
  - discover_toolkits: local pin overrides committed; an editable slot
    that isn't serving gets a note naming the command that opts in
  - editable installs write no pin at all — the checkout waits in the
    cache until `tb use` selects it
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from toolbase.envs.manifest import (
    add_pin,
    load_merged_pins,
    local_manifest_path,
)


# ── merge semantics ────────────────────────────────────────────────────────


def test_local_layer_wins_per_name(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    add_pin(manifest, "heptapod", "2.3.0")
    add_pin(manifest, "calculator", "0.2.0")
    add_pin(local_manifest_path(manifest), "heptapod", "editable")
    pins = load_merged_pins(manifest)
    assert pins == {"heptapod": "editable", "calculator": "0.2.0"}


def test_absent_layers_contribute_nothing(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    assert load_merged_pins(manifest) == {}            # neither file
    add_pin(local_manifest_path(manifest), "heptapod", "editable")
    assert load_merged_pins(manifest) == {"heptapod": "editable"}  # local only


# ── discovery: override + shadow note ─────────────────────────────────────


def _cache_entry(name, version, source_path=None):
    return SimpleNamespace(
        name=name, version=version,
        legacy_meta={"environment": "venv", "python_path": "x",
                     "python_version": "3.12"},
        install_meta=(
            {"editable": True, "source_path": source_path,
             "install_method": "venv"}
            if version == "editable" else {"install_method": "venv"}
        ),
        path=Path(f"/cache/{name}/{version}"),
    )


@pytest.fixture
def discovered(tmp_path, monkeypatch):
    """Run discover_toolkits against a fake two-slot cache, with the
    project's pins coming from tmp_path's manifest pair."""
    from toolbase.serve import orchestrator as orch

    def run(entries):
        monkeypatch.setattr("toolbase.envs.walk_cache", lambda: entries)
        monkeypatch.setattr(
            "toolbase.cli._resolve_active_project_root",
            lambda: (tmp_path, "test"))
        monkeypatch.setattr(
            "toolbase.envs.project_manifest_path",
            lambda root: tmp_path / "manifest.yaml")
        found = orch.discover_toolkits()
        return {d.name: d for d in found}

    return run


def test_local_editable_pin_overrides_committed(discovered, tmp_path):
    add_pin(tmp_path / "manifest.yaml", "heptapod", "2.3.0")
    add_pin(local_manifest_path(tmp_path / "manifest.yaml"),
            "heptapod", "editable")
    d = discovered([_cache_entry("heptapod", "2.3.0"),
                    _cache_entry("heptapod", "editable", "/src/heptapod")])
    assert d["heptapod"].path.name == "editable"
    assert "shadow_note" not in d["heptapod"].meta


def test_editable_shadow_note_names_the_fix(discovered, tmp_path):
    """No pins: a numbered slot wins and the checkout doesn't serve.
    The note has to name the command that opts in, because the symptom
    ("my edits do nothing") gives no clue on its own. Full coverage of
    the editable rule lives in test_editable_resolution.py."""
    d = discovered([_cache_entry("heptapod", "2.3.0"),
                    _cache_entry("heptapod", "editable", "/src/heptapod")])
    assert d["heptapod"].path.name == "2.3.0"
    note = d["heptapod"].meta.get("shadow_note", "")
    assert "NOT what serves" in note
    assert "/src/heptapod" in note
    assert "tb use heptapod@editable" in note


def test_no_note_once_the_local_layer_pins_editable(discovered, tmp_path):
    """The gitignored layer is how you opt in for one machine without
    touching what the team committed."""
    add_pin(local_manifest_path(tmp_path / "manifest.yaml"),
            "heptapod", "editable")
    d = discovered([_cache_entry("heptapod", "2.3.0"),
                    _cache_entry("heptapod", "editable", "/src/heptapod")])
    assert d["heptapod"].path.name == "editable"
    assert "shadow_note" not in d["heptapod"].meta


def test_no_note_without_editable_slot(discovered, tmp_path):
    d = discovered([_cache_entry("heptapod", "2.2.0"),
                    _cache_entry("heptapod", "2.3.0")])
    assert d["heptapod"].path.name == "2.3.0"
    assert "shadow_note" not in d["heptapod"].meta


# ── editable installs no longer pin ──────────────────────────────────────


def test_editable_install_writes_no_pin(discovered, tmp_path):
    """`-e` used to write a private pin on your behalf. Install no
    longer writes any manifest, so the checkout sits in the cache until
    you opt in — the same rule every other install follows."""
    from toolbase import cli
    assert not hasattr(cli, "_pin_editable_local")

    d = discovered([_cache_entry("heptapod", "2.3.0"),
                    _cache_entry("heptapod", "editable", "/src/heptapod")])
    assert load_merged_pins(tmp_path / "manifest.yaml") == {}
    assert d["heptapod"].path.name == "2.3.0"
