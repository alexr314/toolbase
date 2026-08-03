"""``tb status`` — what applies here, what would serve, what's broken.

The three questions that otherwise take three commands and some
inference. Sections appear only when they have content, so a healthy
setup stays short and anything in ``Issues`` is worth reading.

Read-only: it must never create a project or write a file, because the
natural thing to do when confused is run it, and a command that changes
what it reports is worse than none.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config
from toolbase.envs import cache_dir, write_install_meta


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    workdir = tmp_path / "_cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return fake


def _slot(name: str, version: str, source_path: str | None = None) -> Path:
    slot = cache_dir(name, version)
    slot.mkdir(parents=True, exist_ok=True)
    extras: dict = {}
    if version == "editable":
        extras = {"editable": True, "source_path": source_path or "/src/kit"}
    write_install_meta(
        slot, name=name, version=version,
        install_method="venv", python_version="3.12", extras=extras,
    )
    return slot


def _run(*args):
    return CliRunner().invoke(cli.main, ["status", *args])


def _flat(result) -> str:
    """Rich soft-wraps at the narrow test terminal; collapse whitespace
    before matching phrases that can straddle a line break."""
    return " ".join(result.output.split())


class TestContext:
    def test_names_the_project_and_how_it_was_found(self, env):
        r = _run()
        assert r.exit_code == 0, r.output
        assert "On project" in r.output
        assert "no .toolbase/ above cwd" in _flat(r)

    def test_names_a_real_project_when_in_one(self, env, tmp_path):
        project = tmp_path / "repo"
        (project / ".toolbase").mkdir(parents=True)
        r = CliRunner().invoke(
            cli.main, ["--project-dir", str(project), "status"],
        )
        assert r.exit_code == 0, r.output
        assert "--project-dir" in _flat(r)

    def test_names_the_active_loadout(self, env):
        _slot("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit", "-u"])
        r = _run()
        assert "Loadout" in r.output
        assert "default" in r.output


class TestWhatServes:
    def test_active_toolkits_are_listed_with_their_version(self, env):
        _slot("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit", "-u"])
        r = _run()
        assert r.exit_code == 0, r.output
        assert "Active" in r.output
        assert "kit" in r.output
        assert "1.0.0" in r.output

    def test_says_why_each_version_was_chosen(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit", "-u"])
        r = _run()
        assert "latest" in r.output
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        r = _run()
        assert "pinned" in r.output

    def test_installed_but_inactive_is_a_separate_section(self, env):
        _slot("kit", "1.0.0")
        r = _run()
        assert "Installed, not active" in r.output
        assert "kit" in r.output

    def test_empty_loadout_says_how_to_fix_it(self, env):
        _slot("kit", "1.0.0")
        r = _run()
        assert "tb activate <toolkit>" in _flat(r)

    def test_editable_slots_show_their_source(self, env):
        _slot("kit", "editable", "/src/mykit")
        r = _run()
        assert "/src/mykit" in _flat(r)

    def test_other_installed_versions_are_noted(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = _run()
        assert "also installed" in _flat(r)


class TestIssues:
    def test_a_pin_naming_an_absent_version_is_reported(self, env):
        """Serve skips such a toolkit outright, which nothing else in
        the output would convey."""
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        CliRunner().invoke(cli.main, ["use", "kit@2.0.0"])
        CliRunner().invoke(cli.main, ["uninstall", "kit@2.0.0", "--yes"])
        # Re-pin by hand: uninstall clears the pin it invalidated, so
        # forge the state a stale checkout or edited file would leave.
        CliRunner().invoke(cli.main, ["use", "kit@1.0.0"])
        from toolbase.serve.loadout_scaffold import set_version
        set_version("kit", "9.9.9", scope="project", project_root=Path.cwd())

        r = _run()
        assert "Issues" in r.output
        assert "9.9.9" in r.output
        assert "not installed" in r.output

    def test_a_pin_for_an_uninstalled_toolkit_is_reported(self, env):
        from toolbase.serve.loadout_scaffold import set_version
        set_version("ghost", "1.0.0", scope="project", project_root=Path.cwd())
        r = _run()
        assert "Issues" in r.output
        assert "ghost" in r.output
        assert "pinned, not installed" in _flat(r)

    def test_the_section_is_absent_when_healthy(self, env):
        """A clean setup should not print an empty warning heading."""
        _slot("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit"])
        CliRunner().invoke(cli.main, ["connect", "claude-code"])
        r = _run()
        assert "Issues" not in r.output

    def test_active_toolkits_with_no_harness_is_an_issue(self, env):
        """Serving is only half the path — tools reach an agent through
        a harness, and an unwired one looks exactly like an empty
        loadout from the agent's side."""
        _slot("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit"])
        r = _run()
        assert "no harness wired here" in _flat(r)
        assert "tb connect <harness>" in _flat(r)

    def test_nothing_active_means_no_harness_complaint(self, env):
        """Nothing to serve, so an unwired harness isn't the problem."""
        _slot("kit", "1.0.0")
        r = _run()
        assert "no harness wired" not in _flat(r)


class TestHarnesses:
    def test_wired_harnesses_are_listed(self, env):
        _slot("kit", "1.0.0")
        CliRunner().invoke(cli.main, ["activate", "kit"])
        CliRunner().invoke(cli.main, ["connect", "claude-code"])
        r = _run()
        assert "Wired harnesses" in r.output
        assert "claude-code" in r.output

    def test_the_section_is_absent_when_nothing_is_wired(self, env):
        _slot("kit", "1.0.0")
        r = _run()
        assert "Wired harnesses" not in r.output


class TestReadOnly:
    def test_creates_no_project_directory(self, env, tmp_path):
        workdir = tmp_path / "_cwd"
        _run()
        assert not (workdir / ".toolbase").exists()

    def test_writes_nothing_at_all(self, env, tmp_path):
        _slot("kit", "1.0.0")
        before = sorted(p.relative_to(env) for p in env.rglob("*"))
        _run()
        after = sorted(p.relative_to(env) for p in env.rglob("*"))
        assert before == after

    def test_works_with_an_empty_cache(self, env):
        r = _run()
        assert r.exit_code == 0, r.output
        assert "Active" in r.output
