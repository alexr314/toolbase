"""Tests for scope-aware command resolution in ``tb connect``.

Covers the ``toolbase``-vs-absolute-path decision the connect flow makes
before writing a harness config:

- ``_toolbase_abspath`` resolves the running binary even when it isn't on
  PATH (the bug: ``shutil.which`` returns None exactly then).
- ``_toolbase_is_env_installed`` detects venv / named-conda installs.
- ``_resolve_connect_command`` default is scope-aware (user -> abspath,
  project -> bare), ``--abspath`` / ``--portable`` force either way, and a
  project-scope bare write from an isolated env warns.
"""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pytest
import click
from rich.console import Console

from toolbase import cli


# ── _toolbase_abspath ──────────────────────────────────────────────────


def test_abspath_prefers_binary_beside_interpreter(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tb = bindir / "toolbase"
    tb.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    # Even if which() would answer differently, beside-interpreter wins.
    monkeypatch.setattr(shutil, "which", lambda _: "/somewhere/else/toolbase")
    assert cli._toolbase_abspath() == str(tb)


def test_abspath_falls_back_to_which_when_not_beside(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))  # no toolbase here
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/toolbase")
    assert cli._toolbase_abspath() == "/usr/local/bin/toolbase"


def test_abspath_last_resort_is_bare(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert cli._toolbase_abspath() == "toolbase"


# ── _toolbase_is_env_installed ─────────────────────────────────────────


def test_env_installed_true_for_venv(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/some/venv")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    assert cli._toolbase_is_env_installed() is True


def test_env_installed_true_for_named_conda(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/x")
    monkeypatch.setattr(sys, "base_prefix", "/x")  # not a venv
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "hepbench")
    assert cli._toolbase_is_env_installed() is True


def test_env_installed_false_for_base_conda(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/x")
    monkeypatch.setattr(sys, "base_prefix", "/x")
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "base")
    assert cli._toolbase_is_env_installed() is False


def test_env_installed_false_for_system(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    assert cli._toolbase_is_env_installed() is False


# ── _resolve_connect_command (scope-aware) ─────────────────────────────


def _quiet(monkeypatch):
    """Silence the env-installed warning by default so command tests don't
    depend on it; individual warning tests set it explicitly."""
    monkeypatch.setattr(cli, "_toolbase_is_env_installed", lambda: False)


def test_project_default_is_bare(monkeypatch):
    _quiet(monkeypatch)
    assert cli._resolve_connect_command(
        abspath=False, portable=False, scope="project") == "toolbase"


def test_user_default_is_abspath(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(cli, "_toolbase_abspath", lambda: "/abs/toolbase")
    assert cli._resolve_connect_command(
        abspath=False, portable=False, scope="user") == "/abs/toolbase"


def test_abspath_flag_forces_in_project_scope(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(cli, "_toolbase_abspath", lambda: "/abs/toolbase")
    assert cli._resolve_connect_command(
        abspath=True, portable=False, scope="project") == "/abs/toolbase"


def test_portable_flag_forces_bare_in_user_scope(monkeypatch):
    _quiet(monkeypatch)
    assert cli._resolve_connect_command(
        abspath=False, portable=True, scope="user") == "toolbase"


def test_both_flags_error():
    with pytest.raises(click.UsageError):
        cli._resolve_connect_command(abspath=True, portable=True, scope="user")


def test_project_bare_env_installed_warns(monkeypatch):
    monkeypatch.setattr(cli, "_toolbase_is_env_installed", lambda: True)
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, width=200))
    cmd = cli._resolve_connect_command(abspath=False, portable=False, scope="project")
    assert cmd == "toolbase"
    out = buf.getvalue()
    assert "--abspath" in out and "isolated env" in out


def test_user_scope_abspath_does_not_warn(monkeypatch):
    monkeypatch.setattr(cli, "_toolbase_is_env_installed", lambda: True)
    monkeypatch.setattr(cli, "_toolbase_abspath", lambda: "/abs/toolbase")
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, width=200))
    cli._resolve_connect_command(abspath=False, portable=False, scope="user")
    assert buf.getvalue() == ""  # abspath path: no warning
