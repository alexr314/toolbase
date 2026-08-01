"""``tb use`` — choose which installed version serves.

Before this command the only way to move a pin was to re-run
``tb install <name>@<version>``, which deletes the cache slot and
rebuilds the environment from scratch even when that exact version is
already installed. These tests pin the two properties that make ``use``
worth having: it only writes the manifest, and what it writes is what
serve then resolves.
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


def _global_manifest() -> Path:
    return project_manifest_path(default_project_root())


def _pins(manifest: Path) -> dict:
    return {e.name: e.version for e in load_manifest(manifest).toolkits}


class TestPinWriting:
    def test_pins_the_requested_version(self, fake_home):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert _pins(_global_manifest()) == {"kit": "1.0.0"}
        assert "now serves 1.0.0" in r.output

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
        entries = load_manifest(_global_manifest()).toolkits
        assert len(entries) == 1
        assert entries[0].version == "2.0.0"

    def test_carries_the_slot_bundle_subset_onto_the_pin(self, fake_home):
        """The manifest records what a slot actually contains, so a switch
        has to re-read it rather than keep the old version's subset."""
        _slot("kit", "1.0.0", bundles=["alpha"])
        _slot("kit", "2.0.0", bundles=["alpha", "beta"])
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert load_manifest(_global_manifest()).find("kit").bundles == ["alpha"]
        CliRunner().invoke(cli.main, ["use", "kit@2.0.0"])
        assert load_manifest(_global_manifest()).find("kit").bundles == [
            "alpha", "beta"]

    def test_local_scope_writes_the_project_manifest(self, fake_home, tmp_path):
        project = tmp_path / "proj"
        (project / ".toolbase").mkdir(parents=True)
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(
            cli.main, ["--project-dir", str(project), "use", "-p", "kit@1.0.0"],
        )
        assert r.exit_code == 0, r.output
        assert _pins(project_manifest_path(project)) == {"kit": "1.0.0"}
        # The global manifest is untouched.
        assert _pins(_global_manifest()) == {}

    def test_global_and_local_are_mutually_exclusive(self, fake_home):
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "-u", "-p", "kit@1.0.0"])
        assert r.exit_code != 0
        assert "mutually exclusive" in r.output


class TestGlobalPinInsideAProject:
    """`-g` is the default scope, but a project's own manifest is what
    governs cwd — so a global pin written from inside one changes
    nothing there. Reporting plain success would be a lie."""

    @pytest.fixture
    def project(self, fake_home, tmp_path, monkeypatch):
        proj = tmp_path / "myrepo"
        (proj / ".toolbase").mkdir(parents=True)
        (proj / ".toolbase" / "manifest.yaml").write_text(
            "toolkits: []\nschema_version: 1\n"
        )
        monkeypatch.chdir(proj)
        return proj

    def test_warns_that_the_global_pin_does_not_apply(self, project):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert "does not apply here" in r.output
        # The fix is spelled out, copy-pasteable, and doesn't rebuild.
        assert "tb use -p kit@1.0.0" in r.output

    def test_local_scope_does_not_warn(self, project):
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "-p", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert "does not apply here" not in r.output

    def test_no_warning_outside_a_project(self, fake_home):
        """fake_home chdirs somewhere with no project above it, so the
        default-project IS what governs cwd."""
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert "does not apply here" not in r.output

    def test_install_pin_warns_too(self, project, capsys):
        """`tb install` writes the same pin with the same default scope,
        and used to say nothing at all about it."""
        _slot("kit", "1.0.0")
        cli._pin_after_install("kit", "1.0.0", scope=cli.SCOPE_USER)
        out = capsys.readouterr().out
        assert "does not apply here" in out
        assert "tb use -p kit@1.0.0" in out

    def test_install_local_pin_does_not_warn(self, project, capsys):
        _slot("kit", "1.0.0")
        cli._pin_after_install("kit", "1.0.0", scope=cli.SCOPE_PROJECT)
        out = capsys.readouterr().out
        assert "does not apply here" not in out
        assert "Pinned to this project" in out


class TestEditablePin:
    def test_editable_goes_to_the_local_layer(self, fake_home):
        """An editable slot points at this machine's checkout, so its pin
        must never land in the committed manifest."""
        _slot("kit", "1.0.0")
        _slot("kit", "editable")
        r = CliRunner().invoke(cli.main, ["use", "kit@editable"])
        assert r.exit_code == 0, r.output
        assert _pins(local_manifest_path(_global_manifest())) == {
            "kit": "editable"}
        assert _pins(_global_manifest()) == {}

    def test_editable_pin_writes_a_gitignore(self, fake_home, tmp_path):
        project = tmp_path / "proj"
        (project / ".toolbase").mkdir(parents=True)
        _slot("kit", "editable")
        CliRunner().invoke(
            cli.main,
            ["--project-dir", str(project), "use", "-p", "kit@editable"],
        )
        gitignore = project / ".toolbase" / ".gitignore"
        assert "manifest.local.yaml" in gitignore.read_text()


class TestShadowingLocalPin:
    def test_local_pin_that_would_override_is_removed(self, fake_home):
        """A local pin outranks the committed layer, so leaving one would
        make the command silently do nothing."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        add_pin(local_manifest_path(_global_manifest()), "kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0, r.output
        assert "would have overridden" in r.output
        assert _pins(local_manifest_path(_global_manifest())) == {}
        # And the choice actually takes effect.
        assert resolve_version(
            ["1.0.0", "2.0.0"],
            pin=_pins(_global_manifest()).get("kit"),
        ).version == "1.0.0"

    def test_matching_local_pin_is_left_alone(self, fake_home):
        _slot("kit", "1.0.0")
        add_pin(local_manifest_path(_global_manifest()), "kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        assert r.exit_code == 0
        assert "would have overridden" not in r.output
        assert _pins(local_manifest_path(_global_manifest())) == {"kit": "1.0.0"}


class TestClearingAPin:
    def test_bare_name_clears_both_layers(self, fake_home):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        add_pin(_global_manifest(), "kit", "1.0.0")
        add_pin(local_manifest_path(_global_manifest()), "kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit"])
        assert r.exit_code == 0, r.output
        assert _pins(_global_manifest()) == {}
        assert _pins(local_manifest_path(_global_manifest())) == {}
        # Reports what the fallback now resolves to.
        assert "2.0.0" in r.output

    def test_clearing_an_unpinned_toolkit_is_not_an_error(self, fake_home):
        _slot("kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["use", "kit"])
        assert r.exit_code == 0
        assert "was not pinned" in r.output


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
        assert _pins(_global_manifest()) == {}

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
