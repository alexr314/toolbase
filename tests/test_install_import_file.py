"""Unit tests for `tb install <file>.yaml` import files.

An import file is the shareable counterpart to per-toolkit installs: a
project commits e.g. toolkits.yaml and a fresh machine provisions with
one command. These tests pin:

  - strict parsing (unknown keys, missing name/source, editable
    without source, malformed bundles — all loud errors, never
    silently-partial installs)
  - relative-source resolution against the file's own directory and
    ${VAR} expansion (the file must travel with a repo)
  - per-entry dispatch through the normal install path with file-level
    flags applied, continue-on-failure with a nonzero summary
  - CLI detection: a YAML *file* argument enters import mode, and the
    per-toolkit flags (-e/--version/--bundle) are rejected at file level
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase.cli import _install_from_import_file, _parse_import_file


def _write(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "toolkits.yaml"
    f.write_text(text)
    return f


# ── parsing ────────────────────────────────────────────────────────────────


def test_registry_and_source_entries(tmp_path):
    f = _write(tmp_path, """
toolkits:
  - name: calculator
    version: 0.2.0
    bundles: [scientific]
  - source: ../heptapod
    editable: true
""")
    entries = _parse_import_file(f)
    assert entries[0] == {"target": "calculator", "label": "calculator",
                          "version": "0.2.0", "editable": False,
                          "bundles": ("scientific",)}
    # Relative source resolved against the file's directory.
    assert entries[1]["target"] == str((tmp_path / "../heptapod").resolve())
    assert entries[1]["editable"] is True


def test_env_var_expansion_in_source(tmp_path, monkeypatch):
    monkeypatch.setenv("TK_HOME", str(tmp_path / "kits"))
    f = _write(tmp_path, "toolkits:\n  - source: ${TK_HOME}/demo\n")
    entries = _parse_import_file(f)
    assert entries[0]["target"] == str(tmp_path / "kits" / "demo")


@pytest.mark.parametrize("body,fragment", [
    ("toolkits:\n  - name: a\n    nope: 1\n", "unknown key"),
    ("toolkits:\n  - version: 1.0\n", "needs `name:"),
    ("toolkits:\n  - name: a\n    editable: true\n", "requires `source:"),
    ("toolkits:\n  - name: a\n    bundles: nope\n", "list of strings"),
    ("toolkits: []\n", "empty"),
    ("nope: 1\n", "`toolkits:` list"),
])
def test_malformed_files_fail_loud(tmp_path, body, fragment):
    f = _write(tmp_path, body)
    with pytest.raises(click.UsageError) as e:
        _parse_import_file(f)
    assert fragment in str(e.value)


# ── dispatch ───────────────────────────────────────────────────────────────


def _dispatch(tmp_path, body, invoke):
    f = _write(tmp_path, body)
    return _install_from_import_file(
        None, f, global_scope=False, local_scope=True, no_skills=False,
        activate_after=False, rebuild=False, yes=True, no_=False,
        no_input=True, invoke=invoke,
    )


def test_entries_dispatch_with_file_level_flags(tmp_path):
    calls = []
    _dispatch(tmp_path, """
toolkits:
  - name: calculator
  - source: ./kit
    editable: true
""", lambda **kw: calls.append(kw))
    assert len(calls) == 2
    assert calls[0]["name"] == "calculator" and calls[0]["editable"] is False
    assert calls[1]["editable"] is True
    assert calls[1]["name"] == str((tmp_path / "kit").resolve())
    # File-level scope/prompt flags applied to every entry.
    assert all(c["local_scope"] and c["no_input"] for c in calls)


def test_failures_continue_then_exit_nonzero(tmp_path):
    seen = []

    def invoke(**kw):
        seen.append(kw["name"])
        if kw["name"] == "bad":
            raise RuntimeError("boom")

    with pytest.raises(click.ClickException) as e:
        _dispatch(tmp_path,
                  "toolkits:\n  - name: bad\n  - name: good\n", invoke)
    assert seen == ["bad", "good"]          # second entry still ran
    assert "1/2" in str(e.value)


# ── CLI detection ─────────────────────────────────────────────────────────


def test_yaml_file_argument_enters_import_mode(tmp_path, monkeypatch):
    f = _write(tmp_path, "toolkits:\n  - name: calculator\n")
    captured = {}

    def fake(ctx, path, **flags):
        captured["path"] = path
        captured["flags"] = flags

    monkeypatch.setattr(cli, "_install_from_import_file", fake)
    result = CliRunner().invoke(cli.main, ["install", str(f), "--no-input"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == f
    assert captured["flags"]["no_input"] is True


def test_per_toolkit_flags_rejected_at_file_level(tmp_path):
    f = _write(tmp_path, "toolkits:\n  - name: calculator\n")
    result = CliRunner().invoke(cli.main, ["install", str(f), "-e"])
    assert result.exit_code != 0
    assert "inside the import file" in result.output
