"""``tb uninstall`` cleans up the pins that named what it deleted.

The cleanup existed but only looked at the *active* project, while
``tb install`` pins the *default-project* by default (-g). Run from
inside a project those are different files, so uninstalling left a pin
naming a version that no longer existed — and a dangling pin makes
serve skip the toolkit entirely, so the toolkit stayed broken after a
reinstall of a different version.

Only the e2e harness covered this, and it happened to pass because it
was run from a directory with no project above it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import config as toolbase_config
from toolbase import cli
from toolbase.envs import (
    add_pin,
    cache_dir,
    default_project_root,
    load_manifest,
    local_manifest_path,
    project_manifest_path,
    write_install_meta,
)


@pytest.fixture
def in_project(tmp_path, monkeypatch):
    """A fake home plus a cwd inside a real project, so the active
    project and the default-project are two different manifests."""
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)

    project = tmp_path / "myrepo"
    (project / ".toolbase").mkdir(parents=True)
    (project / ".toolbase" / "manifest.yaml").write_text(
        "toolkits: []\nschema_version: 1\n"
    )
    monkeypatch.chdir(project)
    return project


def _slot(name: str, version: str) -> Path:
    slot = cache_dir(name, version)
    slot.mkdir(parents=True, exist_ok=True)
    write_install_meta(
        slot, name=name, version=version,
        install_method="venv", python_version="3.12",
    )
    return slot


def _global_manifest() -> Path:
    return project_manifest_path(default_project_root())


def _pins(manifest: Path) -> dict:
    return {e.name: e.version for e in load_manifest(manifest).toolkits}


def _uninstall(target: str):
    return CliRunner().invoke(
        cli.main, ["uninstall", target, "--yes"], catch_exceptions=False,
    )


class TestFullUninstall:
    def test_clears_the_default_project_pin_from_inside_a_project(
        self, in_project,
    ):
        """The pin `tb install` writes by default must not survive the
        removal of the slot it names."""
        _slot("kit", "1.0.0")
        add_pin(_global_manifest(), "kit", "1.0.0")

        r = _uninstall("kit")
        assert r.exit_code == 0, r.output
        assert _pins(_global_manifest()) == {}

    def test_clears_the_active_project_pin_too(self, in_project):
        _slot("kit", "1.0.0")
        add_pin(project_manifest_path(in_project), "kit", "1.0.0")
        add_pin(_global_manifest(), "kit", "1.0.0")

        r = _uninstall("kit")
        assert r.exit_code == 0, r.output
        assert _pins(project_manifest_path(in_project)) == {}
        assert _pins(_global_manifest()) == {}

    def test_clears_local_layers_of_both_roots(self, in_project):
        _slot("kit", "editable")
        add_pin(local_manifest_path(project_manifest_path(in_project)),
                "kit", "editable")
        add_pin(local_manifest_path(_global_manifest()), "kit", "editable")

        r = _uninstall("kit")
        assert r.exit_code == 0, r.output
        assert _pins(
            local_manifest_path(project_manifest_path(in_project))) == {}
        assert _pins(local_manifest_path(_global_manifest())) == {}

    def test_leaves_other_toolkits_pins_alone(self, in_project):
        _slot("kit", "1.0.0")
        add_pin(_global_manifest(), "kit", "1.0.0")
        add_pin(_global_manifest(), "other", "2.0.0")

        _uninstall("kit")
        assert _pins(_global_manifest()) == {"other": "2.0.0"}


class TestPartialUninstall:
    def test_removes_a_pin_naming_the_deleted_slot(self, in_project):
        """Pinned 2.0.0, uninstalled 2.0.0, 1.0.0 remains: the pin now
        dangles and would make serve skip the toolkit outright."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        add_pin(_global_manifest(), "kit", "2.0.0")

        r = _uninstall("kit@2.0.0")
        assert r.exit_code == 0, r.output
        assert _pins(_global_manifest()) == {}
        assert "Removed stale pin kit@2.0.0" in r.output
        # The message names the manifest, since two are in play.
        assert "manifest.yaml" in r.output
        assert "tb use kit@<version>" in r.output

    def test_keeps_a_pin_that_still_names_an_installed_slot(self, in_project):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        add_pin(_global_manifest(), "kit", "1.0.0")

        r = _uninstall("kit@2.0.0")
        assert r.exit_code == 0, r.output
        assert _pins(_global_manifest()) == {"kit": "1.0.0"}
        assert "stale pin" not in r.output


class TestErrorMessages:
    def test_installed_versions_listed_highest_first(self, in_project):
        """Lexicographic order puts 2.10.0 before 2.9.0, contradicting
        `tb list` and `tb use`, which sort numerically."""
        for v in ("2.9.0", "2.10.0", "1.0.0"):
            _slot("kit", v)
        r = _uninstall("kit@9.9.9")
        assert r.exit_code == 1
        assert "2.10.0, 2.9.0, 1.0.0" in r.output


class TestOutsideAProject:
    def test_default_project_is_not_processed_twice(self, tmp_path, monkeypatch):
        """With no project above cwd the active project IS the
        default-project; it must be cleaned once, not deduped away."""
        fake = tmp_path / "_home" / ".toolbase"
        fake.mkdir(parents=True)
        monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
        workdir = tmp_path / "_cwd"
        workdir.mkdir()
        monkeypatch.chdir(workdir)

        _slot("kit", "1.0.0")
        add_pin(_global_manifest(), "kit", "1.0.0")

        r = _uninstall("kit")
        assert r.exit_code == 0, r.output
        assert _pins(_global_manifest()) == {}
        # Reported once, not once per resolved root.
        assert r.output.count("Uninstalled kit") == 1
