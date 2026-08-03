"""User-level versions layer under project-level ones.

A project's loadout shadows the user's *whole*, which is right for
curation — a half-merged tool selection is one nobody designed. It is
wrong for versions: a project that says nothing about a toolkit should
keep whatever you chose machine-wide, not silently fall back to
newest-installed.

Reported from live use. Running `tb activate` in a plain directory makes
it a project, and before this the toolkit jumped from the pinned build
to the newest one with nothing said — the choice was still on disk, it
just stopped applying.

Layers, lowest priority first:

    user legacy manifest -> user loadout -> project legacy manifest ->
    project loadout

which is the same user->project chain per-toolkit config already uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config
from toolbase.envs import (
    active_pins,
    add_pin,
    cache_dir,
    default_project_root,
    project_manifest_path,
    write_install_meta,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    workdir = tmp_path / "_cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return fake


def _slot(name: str, version: str) -> Path:
    slot = cache_dir(name, version)
    slot.mkdir(parents=True, exist_ok=True)
    write_install_meta(
        slot, name=name, version=version,
        install_method="venv", python_version="3.12",
    )
    return slot


def _set_user_version(name: str, version: str) -> None:
    from toolbase.serve.loadout_scaffold import set_version
    set_version(name, version, scope="user")


class TestInheritance:
    def test_a_new_project_inherits_the_user_version(self, env):
        """The reported bug: activating in a plain directory made it a
        project, and the user's chosen version stopped applying."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        _set_user_version("kit", "1.0.0")

        r = CliRunner().invoke(cli.main, ["activate", "kit"])
        assert r.exit_code == 0, r.output
        assert (Path.cwd() / ".toolbase").is_dir()   # now a project
        assert active_pins(Path.cwd()) == {"kit": "1.0.0"}

    def test_a_legacy_user_manifest_pin_is_inherited_too(self, env):
        """Pre-0.12 pins live in the default-project manifest, not a
        loadout, and must layer the same way."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        add_pin(project_manifest_path(default_project_root()), "kit", "1.0.0")

        CliRunner().invoke(cli.main, ["activate", "kit"])
        assert active_pins(Path.cwd()) == {"kit": "1.0.0"}

    def test_status_and_list_both_show_the_inherited_version(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        _set_user_version("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit"])

        assert "1.0.0" in CliRunner().invoke(cli.main, ["status"]).output
        assert "serving 1.0.0" in CliRunner().invoke(cli.main, ["list"]).output


class TestOverride:
    def test_a_project_version_wins_over_the_user_one(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        _set_user_version("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["use", "kit@2.0.0"])   # project scope
        assert active_pins(Path.cwd()) == {"kit": "2.0.0"}

    def test_overriding_one_toolkit_leaves_the_others_inherited(self, env):
        """Per toolkit, not all-or-nothing — the whole point of layering
        rather than shadowing."""
        for name in ("kit", "other"):
            _slot(name, "1.0.0")
            _slot(name, "2.0.0")
            _set_user_version(name, "1.0.0")
        CliRunner().invoke(cli.main, ["use", "kit@2.0.0"])
        assert active_pins(Path.cwd()) == {"kit": "2.0.0", "other": "1.0.0"}

    def test_clearing_the_project_version_falls_back_to_the_user_one(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        _set_user_version("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["use", "kit@2.0.0"])
        CliRunner().invoke(cli.main, ["use", "kit"])          # clear
        assert active_pins(Path.cwd()) == {"kit": "1.0.0"}


class TestCurationStillShadows:
    def test_a_project_loadout_replaces_the_user_tool_selection(self, env):
        """Only versions layer. A curated set is a complete
        specification, and merging two of them yields one nobody chose.
        """
        from toolbase.serve.loadouts import resolve_loadout
        _slot("kit", "1.0.0")
        _slot("other", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "other", "-u"])
        CliRunner().invoke(cli.main, ["activate", "kit"])     # project

        resolved = resolve_loadout(Path.cwd())
        assert set(resolved.toolkits) == {"kit"}


class TestNoProject:
    def test_outside_a_project_the_user_layer_is_the_answer(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        _set_user_version("kit", "1.0.0")
        assert active_pins() == {"kit": "1.0.0"}

    def test_merging_is_idempotent_with_no_project(self, env):
        """The user root appears once, not twice — a duplicated layer
        would be harmless but signals the dedup is gone."""
        _slot("kit", "1.0.0")
        _set_user_version("kit", "1.0.0")
        root = default_project_root()
        assert active_pins(root) == {"kit": "1.0.0"}
