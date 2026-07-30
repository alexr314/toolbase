"""Tests for command resolution in ``tb connect``."""

from __future__ import annotations

import shutil
import sys

import click
import pytest

from toolbase import cli


# ── _toolbase_abspath ──────────────────────────────────────────────────


def test_abspath_prefers_binary_beside_interpreter(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tb = bindir / "toolbase"
    tb.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(shutil, "which", lambda _: "/somewhere/else/toolbase")
    assert cli._toolbase_abspath() == str(tb)


def test_abspath_falls_back_to_which_when_not_beside(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/toolbase")
    assert cli._toolbase_abspath() == "/usr/local/bin/toolbase"


def test_abspath_last_resort_is_bare(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert cli._toolbase_abspath() == "toolbase"


# ── _resolve_connect_command ───────────────────────────────────────────


def test_project_default_is_abspath(monkeypatch):
    monkeypatch.setattr(cli, "_toolbase_abspath", lambda: "/abs/toolbase")
    assert cli._resolve_connect_command(
        abspath=False, portable=False, scope="project"
    ) == "/abs/toolbase"


def test_user_default_is_abspath(monkeypatch):
    monkeypatch.setattr(cli, "_toolbase_abspath", lambda: "/abs/toolbase")
    assert cli._resolve_connect_command(
        abspath=False, portable=False, scope="user"
    ) == "/abs/toolbase"


def test_abspath_flag_forces_in_project_scope(monkeypatch):
    monkeypatch.setattr(cli, "_toolbase_abspath", lambda: "/abs/toolbase")
    assert cli._resolve_connect_command(
        abspath=True, portable=False, scope="project"
    ) == "/abs/toolbase"


@pytest.mark.parametrize("scope", ["project", "user"])
def test_portable_flag_forces_bare(scope):
    assert cli._resolve_connect_command(
        abspath=False, portable=True, scope=scope
    ) == "toolbase"


def test_both_flags_error():
    with pytest.raises(click.UsageError):
        cli._resolve_connect_command(abspath=True, portable=True, scope="user")
