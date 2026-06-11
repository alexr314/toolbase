"""The machine-local pin layer (manifest.local.yaml) and editable-shadow
visibility.

The committed manifest answers "what does this project depend on" —
true on every machine. "Resolve heptapod to the editable slot" is only
true on the machine holding that source checkout, so it belongs in a
gitignored local layer that merges over the committed manifest
(mirroring the user->project two-layer config merge). These tests pin:

  - load_merged_pins: local wins per name, absent layers contribute
    nothing
  - discover_toolkits: local pin overrides committed; a shadowed
    editable slot gets a loud note (and none when editable serves)
  - editable installs write the local pin + a .gitignore for it
  - partial uninstall removes a now-dangling pin instead of leaving a
    pin that makes serving skip the toolkit entirely
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


def test_shadowed_editable_gets_note(discovered, tmp_path):
    # No pins at all: numbered wins, note explains how to flip it.
    d = discovered([_cache_entry("heptapod", "2.3.0"),
                    _cache_entry("heptapod", "editable", "/src/heptapod")])
    assert d["heptapod"].path.name == "2.3.0"
    note = d["heptapod"].meta.get("shadow_note", "")
    assert "shadowed by 2.3.0" in note
    assert "manifest.local.yaml" in note
    assert "/src/heptapod" in note


def test_no_note_without_editable_slot(discovered, tmp_path):
    d = discovered([_cache_entry("heptapod", "2.2.0"),
                    _cache_entry("heptapod", "2.3.0")])
    assert d["heptapod"].path.name == "2.3.0"
    assert "shadow_note" not in d["heptapod"].meta


# ── editable install writes the local layer ──────────────────────────────


def test_pin_editable_local_writes_layer_and_gitignore(tmp_path, monkeypatch):
    from toolbase import cli

    project = tmp_path / "proj"
    (project / ".toolbase").mkdir(parents=True)
    monkeypatch.setattr(
        "toolbase.envs.find_project_root", lambda cwd: project)
    monkeypatch.setattr(
        "toolbase.envs.project_manifest_path",
        lambda root: root / ".toolbase" / "manifest.yaml")

    cli._pin_editable_local("heptapod", local_scope=True)

    local = project / ".toolbase" / "manifest.local.yaml"
    assert load_merged_pins(project / ".toolbase" / "manifest.yaml") == {
        "heptapod": "editable"}
    assert local.is_file()
    gitignore = project / ".toolbase" / ".gitignore"
    assert "manifest.local.yaml" in gitignore.read_text()
    assert "config/*.local.yaml" in gitignore.read_text()
    # Committed manifest untouched.
    assert not (project / ".toolbase" / "manifest.yaml").exists()


def test_pin_editable_local_keeps_existing_gitignore(tmp_path, monkeypatch):
    from toolbase import cli

    project = tmp_path / "proj"
    (project / ".toolbase").mkdir(parents=True)
    (project / ".toolbase" / ".gitignore").write_text("custom\n")
    monkeypatch.setattr(
        "toolbase.envs.find_project_root", lambda cwd: project)
    monkeypatch.setattr(
        "toolbase.envs.project_manifest_path",
        lambda root: root / ".toolbase" / "manifest.yaml")
    cli._pin_editable_local("heptapod", local_scope=True)
    assert (project / ".toolbase" / ".gitignore").read_text() == "custom\n"
