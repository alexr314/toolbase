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

What survives is everything expensive -- the toolkit and the whole of
site-packages. Only the link to the base is gone, so the fix is to
re-point it rather than reinstall.

These cover the detection rule, both surfaces that report it, and the
repair, including its refusal to cross a minor version: 3.12 packages
under a 3.13 interpreter would import and then fail far less legibly
than the error being repaired.
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
        assert "tb repair demo-kit" in d["demo-kit"].skip_reason

    def test_status_reports_it_with_the_repair_hint(self, env):
        _slot(python_path="/nonexistent/bin/python")
        r = CliRunner().invoke(cli.main, ["status"])
        assert r.exit_code == 0, r.output
        flat = " ".join(r.output.split())
        assert "interpreter missing" in flat
        assert "tb repair demo-kit" in flat

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


# ── the repair ──────────────────────────────────────────────────────────


def _real_venv(slot: Path, minor: str = None) -> Path:
    """Build a real venv inside a slot, then strand it."""
    minor = minor or f"{sys.version_info.major}.{sys.version_info.minor}"
    venv = slot / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)],
                   check=True, capture_output=True)
    return venv


def _strand(venv: Path, minor: str) -> None:
    """Repoint the interpreter links at a path that doesn't exist --
    exactly what deleting the parent environment leaves behind."""
    for p in (venv / "bin").glob("python*"):
        p.unlink()
    (venv / "bin" / f"python{minor}").symlink_to(
        f"/nonexistent/env/bin/python{minor}")
    (venv / "bin" / "python").symlink_to(f"python{minor}")
    cfg = venv / "pyvenv.cfg"
    cfg.write_text(
        f"home = /nonexistent/env/bin\nversion = {minor}.0\n"
        "include-system-site-packages = false\n"
    )


class TestRepair:
    def test_repairs_a_stranded_venv_in_place(self, env):
        minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        slot = cache_dir("demo-kit", "1.0.0")
        slot.mkdir(parents=True, exist_ok=True)
        venv = _real_venv(slot)
        marker = venv / "lib" / f"python{minor}" / "site-packages" / "proof.py"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("VALUE = 42\n")
        _strand(venv, minor)
        _slot(python_path=str(venv / "bin" / "python"))

        assert interpreter_problem(_meta(slot)) == "interpreter missing"
        r = CliRunner().invoke(cli.main, ["repair", "demo-kit", "--yes"])
        assert r.exit_code == 0, r.output
        assert "repaired" in r.output
        assert interpreter_problem(_meta(slot)) is None
        # The point of repairing rather than reinstalling.
        assert marker.exists(), "site-packages was destroyed"

    def test_refuses_to_cross_a_minor_version(self, env):
        """3.12 packages under a 3.13 interpreter import, then fail in
        ways much harder to read than the error being repaired."""
        slot = cache_dir("demo-kit", "1.0.0")
        slot.mkdir(parents=True, exist_ok=True)
        venv = _real_venv(slot)
        _strand(venv, "3.4")            # long dead, certainly absent
        _slot(python_path=str(venv / "bin" / "python"))

        r = CliRunner().invoke(cli.main, ["repair", "demo-kit", "--yes"])
        assert r.exit_code == 1
        flat = " ".join(r.output.split())
        assert "needs Python 3.4" in flat
        assert "isn't installed" in flat
        # And it points at the fallback rather than leaving you stuck.
        assert "tb install demo-kit@1.0.0" in flat

    def test_says_so_when_there_is_nothing_to_repair(self, env, tmp_path):
        py = tmp_path / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
        _slot(python_path=str(py))
        r = CliRunner().invoke(cli.main, ["repair", "demo-kit", "--yes"])
        assert r.exit_code == 0, r.output
        assert "Nothing to repair" in r.output

    def test_unknown_toolkit_is_an_error(self, env):
        r = CliRunner().invoke(cli.main, ["repair", "nosuch", "--yes"])
        assert r.exit_code == 1
        assert "not installed" in r.output

    def test_requires_a_target_or_all(self, env):
        r = CliRunner().invoke(cli.main, ["repair"])
        assert r.exit_code != 0
        assert "toolkit name" in r.output or "--all" in r.output

    def test_all_scans_every_toolkit(self, env, tmp_path):
        healthy = tmp_path / "python"
        healthy.write_text("#!/bin/sh\n")
        healthy.chmod(0o755)
        _slot("fine-kit", "1.0.0", python_path=str(healthy))
        _slot("broken-kit", "1.0.0", python_path="/nonexistent/bin/python")
        r = CliRunner().invoke(cli.main, ["repair", "--all", "--yes"])
        assert "broken-kit" in r.output
        assert "fine-kit" not in r.output

    def test_a_version_can_be_targeted(self, env, tmp_path):
        healthy = tmp_path / "python"
        healthy.write_text("#!/bin/sh\n")
        healthy.chmod(0o755)
        _slot("demo-kit", "1.0.0", python_path="/nonexistent/bin/python")
        _slot("demo-kit", "2.0.0", python_path=str(healthy))
        r = CliRunner().invoke(cli.main, ["repair", "demo-kit@2.0.0", "--yes"])
        assert r.exit_code == 0, r.output
        assert "Nothing to repair" in r.output
