"""A toolkit whose interpreter was deleted is detected, and repairable.

A venv holds no interpreter of its own -- it symlinks the one that built
it -- and ``tb install`` builds with whatever Python was running it. So
deleting that environment strands every toolkit installed from it, from
everywhere at once, because the cache holds one copy shared by all
environments.

Nothing noticed. The metadata and the toolkit's files are intact, so
discovery called the slot ready and serve tried to spawn it on every
startup, failing at connect with ``mcp connect failed: [Errno 2] No such
file or directory``. Found on a real machine: an install from a conda env
that had since been removed.

What survives the breakage is everything expensive -- the toolkit and
the whole of site-packages -- but re-pointing the venv is not worth a
command: it can only aim at the interpreter running toolbase, which is
usually another environment that can be deleted in turn, so it buys a
saved download rather than durability. ``tb clean`` removes what cannot
run and prints how to put it back.

These cover the detection rule, both surfaces that report it, and clean.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config
from toolbase.envs import (
    cache_dir,
    interpreter_problem,
    write_install_meta,
    write_legacy_meta,
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


def _slot(name="demo-kit", version="1.0.0", *, python_path=None,
          environment="venv"):
    """A cache slot whose metadata points at ``python_path``."""
    slot = cache_dir(name, version)
    slot.mkdir(parents=True, exist_ok=True)
    py = python_path if python_path is not None else str(slot / ".venv/bin/python")
    write_install_meta(
        slot, name=name, version=version, install_method=environment,
        python_version="3.12", extras={"python_path": py},
    )
    write_legacy_meta(slot, {
        "name": name, "version": version, "environment": environment,
        "python_path": py, "python_version": "3.12",
    })
    return slot


def _meta(slot):
    from toolbase.envs import read_install_meta, read_legacy_meta
    m = dict(read_install_meta(slot) or {})
    m.update(read_legacy_meta(slot) or {})
    return m


# ── the rule ────────────────────────────────────────────────────────────


class TestInterpreterProblem:
    def test_a_present_executable_is_healthy(self, env, tmp_path):
        py = tmp_path / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
        assert interpreter_problem(_meta(_slot(python_path=str(py)))) is None

    def test_a_missing_path_is_reported(self, env):
        slot = _slot(python_path="/nonexistent/bin/python")
        assert interpreter_problem(_meta(slot)) == "interpreter missing"

    def test_a_dangling_symlink_is_reported(self, env, tmp_path):
        """The real shape: the link survives, its target does not."""
        link = tmp_path / "python"
        link.symlink_to("/nonexistent/env/bin/python3.12")
        assert link.is_symlink() and not link.exists()
        assert interpreter_problem(_meta(_slot(python_path=str(link)))) == (
            "interpreter missing")

    def test_a_non_executable_file_is_reported(self, env, tmp_path):
        py = tmp_path / "python"
        py.write_text("")
        py.chmod(0o644)
        assert interpreter_problem(_meta(_slot(python_path=str(py)))) == (
            "interpreter not executable")

    def test_conda_slots_are_left_alone(self, env):
        """They are named, not pathed; resolving one means shelling out
        to conda, which is too slow for a listing."""
        slot = _slot(environment="conda", python_path="/nonexistent/python")
        assert interpreter_problem(_meta(slot)) is None

    def test_missing_python_path_is_not_double_reported(self, env):
        """Spawn raises its own clear error for this case."""
        assert interpreter_problem(
            {"environment": "venv", "install_method": "venv"}) is None


# ── the surfaces ────────────────────────────────────────────────────────


class TestSurfaces:
    def test_serve_discovery_skips_it_and_names_the_fix(self, env):
        from toolbase.serve.orchestrator import discover_toolkits
        _slot(python_path="/nonexistent/bin/python")
        (cache_dir("demo-kit", "1.0.0") / "toolkit.yaml").write_text(
            "name: demo-kit\nversion: 1.0.0\ndescription: x\nauthor: x\n"
            "license: MIT\ncategory: general\npython_version: '3.12'\n"
        )
        d = {x.name: x for x in discover_toolkits()}
        assert "interpreter missing" in (d["demo-kit"].skip_reason or "")
        assert "tb clean" in d["demo-kit"].skip_reason

    def test_status_reports_it_and_names_the_way_out(self, env):
        _slot(python_path="/nonexistent/bin/python")
        r = CliRunner().invoke(cli.main, ["status"])
        assert r.exit_code == 0, r.output
        flat = " ".join(r.output.split())
        assert "interpreter missing" in flat
        assert "tb clean" in flat

    def test_status_does_not_offer_the_pin_fix_for_this(self, env):
        """Different problem, different fix: reinstalling a toolkit whose
        files are fine sends you to redownload for nothing."""
        _slot(python_path="/nonexistent/bin/python")
        r = CliRunner().invoke(cli.main, ["status"])
        assert "clears a pin" not in " ".join(r.output.split())

    def test_a_healthy_slot_produces_no_issue(self, env, tmp_path):
        py = tmp_path / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
        _slot(python_path=str(py))
        r = CliRunner().invoke(cli.main, ["status"])
        assert "interpreter missing" not in r.output




# ── tb clean ────────────────────────────────────────────────────────────


class TestClean:
    """The fallback when repair can't apply.

    Repair only ever re-points a venv at ``sys.executable`` -- searching
    the host for a matching Python means encoding a guess about someone
    else's machine. So a slot built on a different minor than the one
    toolbase is running can't be repaired here, and removing it is the
    honest option.
    """

    def test_removes_a_slot_that_cannot_run(self, env):
        _slot(python_path="/nonexistent/bin/python")
        slot = cache_dir("demo-kit", "1.0.0")
        r = CliRunner().invoke(cli.main, ["clean", "--yes"])
        assert r.exit_code == 0, r.output
        assert not slot.exists()

    def test_drops_the_empty_toolkit_dir_too(self, env):
        """Otherwise the walker keeps reporting a toolkit with no
        versions in it."""
        _slot(python_path="/nonexistent/bin/python")
        CliRunner().invoke(cli.main, ["clean", "--yes"])
        assert not cache_dir("demo-kit", "1.0.0").parent.exists()

    def test_leaves_healthy_installs_alone(self, env, tmp_path):
        py = tmp_path / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
        _slot("fine-kit", "1.0.0", python_path=str(py))
        _slot("broken-kit", "1.0.0", python_path="/nonexistent/bin/python")
        CliRunner().invoke(cli.main, ["clean", "--yes"])
        assert cache_dir("fine-kit", "1.0.0").exists()
        assert not cache_dir("broken-kit", "1.0.0").exists()

    def test_never_removes_an_editable_install(self, env):
        """The slot is a symlink to a working copy, and rebuilding it
        needs the original `tb install -e <path>` that only the user
        knows."""
        slot = cache_dir("edit-kit", "editable")
        slot.mkdir(parents=True, exist_ok=True)
        from toolbase.envs import write_install_meta as _wim
        from toolbase.envs import write_legacy_meta as _wlm
        extras = {"python_path": "/nonexistent/bin/python",
                  "editable": True, "source_path": "/src/edit-kit"}
        _wim(slot, name="edit-kit", version="editable", install_method="venv",
             python_version="3.12", extras=extras)
        _wlm(slot, {"name": "edit-kit", "version": "editable",
                    "environment": "venv", **extras})

        r = CliRunner().invoke(cli.main, ["clean", "--yes"])
        assert slot.exists(), "an editable working copy was removed"
        assert "tb install -e /src/edit-kit" in " ".join(r.output.split())

    def test_dry_run_removes_nothing(self, env):
        _slot(python_path="/nonexistent/bin/python")
        r = CliRunner().invoke(cli.main, ["clean", "--dry-run"])
        assert r.exit_code == 0, r.output
        assert cache_dir("demo-kit", "1.0.0").exists()
        assert "nothing removed" in r.output.lower()

    def test_declining_removes_nothing(self, env):
        _slot(python_path="/nonexistent/bin/python")
        r = CliRunner().invoke(cli.main, ["clean", "--no"])
        assert cache_dir("demo-kit", "1.0.0").exists()

    def test_says_so_when_everything_is_healthy(self, env, tmp_path):
        py = tmp_path / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
        _slot(python_path=str(py))
        r = CliRunner().invoke(cli.main, ["clean", "--yes"])
        assert "Nothing to clean" in r.output

    def test_clears_the_version_records_of_what_it_removed(self, env):
        """A pin naming a deleted slot doesn't fall back -- serve skips
        the toolkit outright, so it would stay broken even after a good
        reinstall."""
        from toolbase.envs import (
            add_pin, project_manifest_path, default_project_root, load_manifest,
        )
        _slot(python_path="/nonexistent/bin/python")
        manifest = project_manifest_path(default_project_root())
        add_pin(manifest, "demo-kit", "1.0.0")

        r = CliRunner().invoke(cli.main, ["clean", "--yes"])
        assert r.exit_code == 0, r.output
        pins = {e.name: e.version for e in load_manifest(manifest).toolkits}
        assert "demo-kit" not in pins


