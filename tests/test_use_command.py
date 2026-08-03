"""``tb use`` — choose which installed version serves.

Before this command the only way to move a pin was to re-run
``tb install <name>@<version>``, which deletes the cache slot and
rebuilds the environment from scratch even when that exact version is
already installed. These tests pin the two properties that make ``use``
worth having: it only writes a file, and what it writes is what serve
then resolves.

Versions live in the active loadout, alongside the tool selection, so a
loadout is a complete specification of what an agent gets.
"""

from __future__ import annotations

from datetime import datetime
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
    resolve_version,
    write_install_meta,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    # chdir too: `-l` and project discovery walk upward from cwd, so a
    # test run from inside a real toolbase project would write its pins
    # into the developer's own .toolbase/.
    workdir = tmp_path / "_cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return fake


def _slot(name: str, version: str, bundles: list[str] | None = None) -> Path:
    slot = cache_dir(name, version)
    slot.mkdir(parents=True, exist_ok=True)
    extras: dict = {}
    if bundles is not None:
        extras["bundles"] = list(bundles)
    write_install_meta(
        slot, name=name, version=version,
        install_method="venv", python_version="3.12", extras=extras,
    )
    return slot


def _user_loadout() -> Path:
    from toolbase.envs.paths import user_loadouts_dir
    return user_loadouts_dir() / "default.yaml"


def _cwd_loadout() -> Path:
    """Where a default-scope `tb use` writes: this project's loadout,
    with .toolbase/ created in cwd if there is none above."""
    from toolbase.envs.paths import project_loadouts_dir
    return project_loadouts_dir(Path.cwd()) / "default.yaml"


def _project_loadout(project: Path, private: bool = False) -> Path:
    from toolbase.envs.paths import project_loadouts_dir
    leaf = "default.local.yaml" if private else "default.yaml"
    return project_loadouts_dir(project) / leaf


def _pins(loadout: Path) -> dict:
    """``{toolkit: version}`` from a loadout's ``versions:`` block."""
    import yaml as _yaml
    if not loadout.exists():
        return {}
    data = _yaml.safe_load(loadout.read_text()) or {}
    return dict(data.get("versions") or {})


def _curation(loadout: Path) -> dict:
    """The ``toolkits:`` block — what the loadout exposes."""
    import yaml as _yaml
    if not loadout.exists():
        return {}
    data = _yaml.safe_load(loadout.read_text()) or {}
    return dict(data.get("toolkits") or {})


class TestPinWriting:
    def test_pins_the_requested_version(self, fake_home):
        """Default scope is this project, like every other
        state-changing command."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert _pins(_cwd_loadout()) == {"kit": "1.0.0"}
        assert _pins(_user_loadout()) == {}
        assert "now serves 1.0.0" in r.output

    def test_user_scope_writes_the_user_loadout(self, fake_home):
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "-u", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert _pins(_user_loadout()) == {"kit": "1.0.0"}

    def test_does_not_touch_the_cache(self, fake_home):
        """The whole point: switching must not delete or rebuild a slot."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        before = {p: sorted(q.name for q in p.iterdir())
                  for p in (cache_dir("kit", "1.0.0"), cache_dir("kit", "2.0.0"))}
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        after = {p: sorted(q.name for q in p.iterdir())
                 for p in (cache_dir("kit", "1.0.0"), cache_dir("kit", "2.0.0"))}
        assert before == after

    def test_switching_replaces_rather_than_appends(self, fake_home):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        CliRunner().invoke(cli.main, ["use", "kit@2.0.0"])
        assert _pins(_cwd_loadout()) == {"kit": "2.0.0"}

    def test_leaves_curation_alone(self, fake_home, tmp_path, monkeypatch):
        """A version and a tool selection live in the same entry, so
        pinning must not disturb what the loadout already exposes."""
        import yaml as _yaml
        monkeypatch.chdir(tmp_path)
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit/alpha"])
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert _pins(_cwd_loadout()) == {"kit": "1.0.0"}
        assert _curation(_cwd_loadout())["kit"]["bundles"] == ["alpha"]

    def test_project_scope_writes_the_project_loadout(
        self, fake_home, tmp_path,
    ):
        project = tmp_path / "proj"
        (project / ".toolbase").mkdir(parents=True)
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(
            cli.main, ["--project-dir", str(project), "use", "-p", "kit@1.0.0"],
        )
        assert r.exit_code == 0, r.output
        assert _pins(_project_loadout(project)) == {"kit": "1.0.0"}
        # The user loadout is untouched.
        assert _pins(_user_loadout()) == {}

    def test_global_and_local_are_mutually_exclusive(self, fake_home):
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "-u", "-p", "kit@1.0.0"])
        assert r.exit_code != 0
        assert "mutually exclusive" in r.output


class TestVersionAndExposureAreSeparate:
    """Choosing a version is not the same act as exposing a toolkit.

    Versions live in the loadout's ``versions:`` block and curation in
    ``toolkits:``. They were briefly the same entry, which meant
    `tb use` silently activated a toolkit and `tb deactivate` silently
    discarded the version you had chosen. Both were reported from live
    use, and both are the same mistake: two facts with different
    lifetimes sharing a container.
    """

    def test_use_does_not_activate(self, fake_home):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert _pins(_cwd_loadout()) == {"kit": "1.0.0"}
        # Nothing exposed: choosing a build says nothing about exposure.
        assert _curation(_cwd_loadout()) == {}
        assert "inactive" in CliRunner().invoke(cli.main, ["list"]).output

    def test_deactivate_keeps_the_version(self, fake_home):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        CliRunner().invoke(cli.main, ["activate", "kit"])
        CliRunner().invoke(cli.main, ["deactivate", "kit"])

        # The toolkit is hidden, but the version survives — otherwise
        # re-activating would silently jump to the newest installed.
        assert _curation(_cwd_loadout()) == {}
        assert _pins(_cwd_loadout()) == {"kit": "1.0.0"}
        out = CliRunner().invoke(cli.main, ["list"]).output
        assert "serving 1.0.0 (pinned to 1.0.0)" in out

    def test_activate_does_not_set_a_version(self, fake_home):
        """The converse: exposing a toolkit leaves resolution alone."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit"])
        assert _pins(_cwd_loadout()) == {}
        out = CliRunner().invoke(cli.main, ["list"]).output
        assert "highest installed, no pin" in out


class TestUserPinInsideAProject:
    """A project's own loadout governs cwd, so a `-u` pin written from
    inside one changes nothing there. Reporting plain success is a lie,
    and this is now the only place scope comes up at all."""

    @pytest.fixture
    def project(self, fake_home, tmp_path, monkeypatch):
        proj = tmp_path / "myrepo"
        (proj / ".toolbase").mkdir(parents=True)
        (proj / ".toolbase" / "manifest.yaml").write_text(
            "toolkits: []\nschema_version: 1\n"
        )
        monkeypatch.chdir(proj)
        return proj

    def test_warns_that_the_user_pin_does_not_apply(self, project):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["use", "-u", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert "Does not apply here" in r.output
        # The fix is spelled out, copy-pasteable, and doesn't rebuild.
        assert "tb use kit@1.0.0" in r.output

    def test_default_scope_does_not_warn(self, project):
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert "Does not apply here" not in r.output

    def test_no_warning_outside_a_project(self, fake_home):
        """No project above cwd, so a -u pin is what governs."""
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "-u", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert "Does not apply here" not in r.output

    def test_install_writes_no_manifest_at_all(self, project):
        """`tb install` used to pin, which is how a pin ended up in a
        file the directory you were standing in ignored. It now writes
        no manifest, so the warning above is the only place scope comes
        up — and only for a pin you typed."""
        assert not hasattr(cli, "_pin_after_install")
        assert not hasattr(cli, "_pin_editable_local")

    def test_install_takes_no_scope_flags(self, project):
        _slot("kit", "1.0.0")
        for flag in ("-u", "-p", "--private"):
            r = CliRunner().invoke(cli.main, ["install", flag, "kit"])
            assert r.exit_code != 0, f"{flag} should not be accepted"
            assert "no such option" in r.output.lower()


class TestEditablePin:
    def test_editable_goes_to_the_private_layer(self, fake_home, tmp_path):
        """An editable pin names a directory only this machine has, so
        committing it would leave a teammate with a dangling pin."""
        project = tmp_path / "proj"
        (project / ".toolbase").mkdir(parents=True)
        _slot("kit", "1.0.0")
        _slot("kit", "editable")
        r = CliRunner().invoke(
            cli.main,
            ["--project-dir", str(project), "use", "-p", "kit@editable"],
        )
        assert r.exit_code == 0, r.output
        assert _pins(_project_loadout(project, private=True)) == {
            "kit": "editable"}
        assert _pins(_project_loadout(project)) == {}
        assert "gitignored" in r.output

    def test_editable_pin_writes_a_gitignore(self, fake_home, tmp_path):
        project = tmp_path / "proj"
        (project / ".toolbase").mkdir(parents=True)
        _slot("kit", "editable")
        CliRunner().invoke(
            cli.main,
            ["--project-dir", str(project), "use", "-p", "kit@editable"],
        )
        gitignore = project / ".toolbase" / ".gitignore"
        assert gitignore.exists()

    def test_user_scope_editable_stays_at_user_scope(self, fake_home):
        """`~/.toolbase/` is never committed, so there's nothing to
        protect against — the redirect is only for project scope."""
        _slot("kit", "editable")
        r = CliRunner().invoke(cli.main, ["use", "-u", "kit@editable"])
        assert r.exit_code == 0, r.output
        assert _pins(_user_loadout()) == {"kit": "editable"}


class TestPrivateLayerOverrides:
    def test_private_version_wins_over_the_committed_one(
        self, fake_home, tmp_path,
    ):
        """The layer exists so one person can repoint one toolkit
        without touching what the team shares."""
        from toolbase.serve.loadouts import discover_loadouts
        project = tmp_path / "proj"
        (project / ".toolbase").mkdir(parents=True)
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        args = ["--project-dir", str(project), "use"]
        CliRunner().invoke(cli.main, args + ["-p", "kit@1.0.0"])
        CliRunner().invoke(cli.main, args + ["--private", "kit@2.0.0"])

        assert _pins(_project_loadout(project)) == {"kit": "1.0.0"}
        assert _pins(_project_loadout(project, private=True)) == {"kit": "2.0.0"}
        merged = discover_loadouts(project, user_base=fake_home)
        assert merged["default"].versions["kit"] == "2.0.0"

    def test_private_layer_leaves_other_toolkits_alone(
        self, fake_home, tmp_path,
    ):
        from toolbase.serve.loadouts import discover_loadouts
        project = tmp_path / "proj"
        (project / ".toolbase").mkdir(parents=True)
        _slot("kit", "1.0.0")
        _slot("other", "3.0.0")
        args = ["--project-dir", str(project), "use"]
        CliRunner().invoke(cli.main, args + ["-p", "kit@1.0.0"])
        CliRunner().invoke(cli.main, args + ["-p", "other@3.0.0"])
        CliRunner().invoke(cli.main, args + ["--private", "kit@1.0.0"])

        merged = discover_loadouts(project, user_base=fake_home)
        assert merged["default"].versions["other"] == "3.0.0"


class TestClearingAPin:
    def test_bare_name_clears_the_version(self, fake_home):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        r = CliRunner().invoke(cli.main, ["use", "kit"])
        assert r.exit_code == 0, r.output
        assert _pins(_cwd_loadout()) == {}
        # And says what now serves instead.
        assert "2.0.0" in r.output

    def test_clearing_leaves_curation_intact(self, fake_home, tmp_path, monkeypatch):
        """Clearing a version must not deactivate the toolkit."""
        import yaml as _yaml
        monkeypatch.chdir(tmp_path)
        _slot("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit/alpha"])
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        CliRunner().invoke(cli.main, ["use", "kit"])
        assert _pins(_cwd_loadout()) == {}
        assert _curation(_cwd_loadout())["kit"]["bundles"] == ["alpha"]

    def test_clearing_an_unpinned_toolkit_is_not_an_error(self, fake_home):
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit"])
        assert r.exit_code == 0
        assert "no version set" in r.output


class TestValidation:
    def test_uninstalled_toolkit_is_rejected(self, fake_home):
        r = CliRunner().invoke(cli.main, ["use", "ghost@1.0.0"])
        assert r.exit_code == 1
        assert "not installed" in r.output
        assert "toolbase install ghost" in r.output

    def test_uninstalled_version_lists_what_is_there(self, fake_home):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@3.0.0"])
        assert r.exit_code == 1
        assert "v3.0.0 is not installed" in r.output
        # Highest first, and the fix is spelled out.
        assert "2.0.0, 1.0.0" in r.output
        assert "toolbase install kit@3.0.0" in r.output
        # Nothing was written.
        assert _pins(_user_loadout()) == {}

    def test_empty_name_is_a_usage_error(self, fake_home):
        r = CliRunner().invoke(cli.main, ["use", "@1.0.0"])
        assert r.exit_code != 0
        assert "expected <toolkit>" in r.output


class TestAgreesWithServe:
    def test_serve_discovery_follows_the_choice(self, fake_home, monkeypatch):
        """The command is only useful if `tb serve` agrees with it."""
        from toolbase.serve import orchestrator as orch

        for version in ("1.0.0", "2.0.0"):
            slot = _slot("kit", version)
            (slot / "toolkit.yaml").write_text(
                f"name: kit\nversion: {version}\ndescription: x\n"
                "author: x\nlicense: MIT\ncategory: general\n"
                "python_version: '3.12'\n"
            )
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        disc = {d.name: d for d in orch.discover_toolkits()}
        assert disc["kit"].path.name == "1.0.0"
        assert disc["kit"].skip_reason is None

        CliRunner().invoke(cli.main, ["use", "kit@2.0.0"])
        disc = {d.name: d for d in orch.discover_toolkits()}
        assert disc["kit"].path.name == "2.0.0"
