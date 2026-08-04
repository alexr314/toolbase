"""A half-migrated tree resolves, and `tb migrate` finishes the job.

Discovery reads the pre-0.12 ``profiles/`` directory as well as
``loadouts/``. It used to read whichever one existed:

    directory = current if current.is_dir() else legacy

which meant the first write to ``loadouts/`` orphaned every loadout
still in ``profiles/``. The write that triggers it need have nothing to
do with curation -- ``tb use`` recording a version creates
``loadouts/default.yaml``, and that alone was enough to make seven
curated loadouts in a real project vanish at once, reported as "No
loadout named ...". Every benchmark depending on them silently lost its
tools.

So the two directories merge, current winning per name. That keeps a
half-migrated tree working, and ``tb migrate`` exists so a tree does not
have to stay half-migrated: reading both shapes forever is not the same
as being migrated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config
from toolbase.serve.loadouts import discover_loadouts


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    project = tmp_path / "proj"
    (project / ".toolbase").mkdir(parents=True)
    monkeypatch.chdir(project)
    return project


def _write(directory: Path, name: str, bundle: str = "eda") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{name}.yaml"
    p.write_text(f"toolkits:\n  heptapod:\n    bundles: [{bundle}]\n")
    return p


def _legacy(project: Path) -> Path:
    return project / ".toolbase" / "profiles"


def _current(project: Path) -> Path:
    return project / ".toolbase" / "loadouts"


def _run(*args):
    return CliRunner().invoke(cli.main, list(args))


# ── discovery merges rather than chooses ────────────────────────────────


class TestDiscovery:
    def test_legacy_only_still_resolves(self, env):
        _write(_legacy(env), "hep-symb")
        assert "hep-symb" in discover_loadouts(env)

    def test_a_write_to_loadouts_does_not_orphan_profiles(self, env):
        """The reported failure. `tb use` records a version, creating
        loadouts/default.yaml, and every curated loadout disappeared."""
        for n in ("hep-symb", "hep-recast", "forward-llp-defined"):
            _write(_legacy(env), n)
        _write(_current(env), "default")

        found = discover_loadouts(env)
        for n in ("hep-symb", "hep-recast", "forward-llp-defined", "default"):
            assert n in found, f"{n} was orphaned"

    def test_current_wins_on_a_name_in_both(self, env):
        _write(_legacy(env), "shared", bundle="old")
        _write(_current(env), "shared", bundle="new")
        sel = discover_loadouts(env)["shared"].toolkits["heptapod"]
        assert sel.bundles == ["new"]

    def test_a_private_layer_left_in_profiles_still_applies(self, env):
        """Migrating the committed file alone must not silently drop the
        machine's own overrides."""
        _write(_current(env), "paper", bundle="committed")
        (_legacy(env)).mkdir(parents=True, exist_ok=True)
        (_legacy(env) / "paper.local.yaml").write_text(
            "toolkits:\n  heptapod:\n    bundles: [private]\n")
        sel = discover_loadouts(env)["paper"].toolkits["heptapod"]
        assert sel.bundles == ["private"]

    def test_user_scope_merges_the_same_way(self, env):
        from toolbase.envs.paths import (
            user_loadouts_dir, legacy_user_profiles_dir,
        )
        _write(legacy_user_profiles_dir(), "user-legacy")
        _write(user_loadouts_dir(), "user-current")
        found = discover_loadouts(env)
        assert "user-legacy" in found and "user-current" in found


# ── tb migrate ──────────────────────────────────────────────────────────


class TestMigrate:
    def test_moves_profiles_into_loadouts(self, env):
        _write(_legacy(env), "hep-symb")
        r = _run("migrate", "--yes")
        assert r.exit_code == 0, r.output
        assert (_current(env) / "hep-symb.yaml").exists()
        assert not (_legacy(env) / "hep-symb.yaml").exists()

    def test_removes_the_emptied_legacy_directory(self, env):
        """Otherwise it is just a place for the next one to reappear."""
        _write(_legacy(env), "hep-symb")
        _run("migrate", "--yes")
        assert not _legacy(env).exists()

    def test_private_layers_move_too(self, env):
        _write(_legacy(env), "paper")
        (_legacy(env) / "paper.local.yaml").write_text("toolkits: {}\n")
        _run("migrate", "--yes")
        assert (_current(env) / "paper.local.yaml").exists()

    def test_never_overwrites_a_name_already_migrated(self, env):
        _write(_legacy(env), "shared", bundle="old")
        _write(_current(env), "shared", bundle="new")
        r = _run("migrate", "--yes")
        assert "already exists" in " ".join(r.output.split())
        assert "new" in (_current(env) / "shared.yaml").read_text()
        assert (_legacy(env) / "shared.yaml").exists()   # left for the user

    def test_dry_run_moves_nothing(self, env):
        _write(_legacy(env), "hep-symb")
        r = _run("migrate", "--dry-run")
        assert (_legacy(env) / "hep-symb.yaml").exists()
        assert "nothing moved" in r.output.lower()

    def test_declining_moves_nothing(self, env):
        _write(_legacy(env), "hep-symb")
        _run("migrate", "--no")
        assert (_legacy(env) / "hep-symb.yaml").exists()

    def test_is_idempotent(self, env):
        _write(_legacy(env), "hep-symb")
        _run("migrate", "--yes")
        r = _run("migrate", "--yes")
        assert "Nothing to migrate" in r.output

    def test_says_so_when_there_is_nothing_to_do(self, env):
        assert "Nothing to migrate" in _run("migrate", "--yes").output

    def test_everything_still_resolves_afterwards(self, env):
        for n in ("hep-symb", "hep-recast"):
            _write(_legacy(env), n)
        _write(_current(env), "default")
        _run("migrate", "--yes")
        found = discover_loadouts(env)
        assert {"hep-symb", "hep-recast", "default"} <= set(found)


class TestServeYamlKey:
    def _serve_yaml(self, env) -> Path:
        return Path(toolbase_config.CONFIG_DIR) / "serve.yaml"

    def test_renames_the_pre_012_key(self, env):
        self._serve_yaml(env).write_text(
            "default:\n  profile: hep-symb\n")
        _run("migrate", "--yes")
        assert "loadout: hep-symb" in self._serve_yaml(env).read_text()
        assert "profile:" not in self._serve_yaml(env).read_text()

    def test_preserves_comments_and_ordering(self, env):
        """serve.yaml is a file people edit; a YAML round-trip to change
        one key would reflow everything around it."""
        self._serve_yaml(env).write_text(
            "# which loadout serves\ndefault:\n  # the choice\n"
            "  profile: hep-symb\n  disabled: []\n")
        _run("migrate", "--yes")
        out = self._serve_yaml(env).read_text()
        assert "# which loadout serves" in out
        assert "# the choice" in out
        assert "disabled: []" in out

    def test_leaves_an_already_current_key_alone(self, env):
        self._serve_yaml(env).write_text("default:\n  loadout: hep-symb\n")
        r = _run("migrate", "--yes")
        assert "Nothing to migrate" in r.output

    def test_prefers_the_current_key_when_both_are_present(self, env):
        self._serve_yaml(env).write_text(
            "default:\n  loadout: new\n  profile: old\n")
        r = _run("migrate", "--yes")
        assert "Nothing to migrate" in r.output
        assert "loadout: new" in self._serve_yaml(env).read_text()
