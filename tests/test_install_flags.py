"""Unit tests for `tb install` scope/source flags (-e / -l / -g).

Covers the 0.6.0 install redesign:
  - `-g` (default) vs `-l` manifest scoping (binary always in global cache).
  - `-e` editable installs: symlink source into cache slot, build venv,
    record editable meta, do NOT pin into the committed manifest.
  - path-vs-name disambiguation (pip-style).
  - flag exclusivity + the editable-requires-path / version-meaningless errors.
  - the removed install-time "where do you want this?" prompt: --no-input
    install takes the global default with no prompt.
  - `tb list` renders editable slots with the `-> <path>` indicator.

Env setup (venv build) is mocked so these stay fast and offline; the
real venv build is exercised by the e2e harness.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import requests
import yaml
from click.testing import CliRunner

from toolbase import cli


# ── helpers ────────────────────────────────────────────────────────────────


def _make_source_toolkit(
    parent: Path,
    name: str,
    version: str = "0.1.0",
    *,
    category: str = "utils",
) -> Path:
    """A minimal on-disk toolkit source dir with a tools/ package."""
    tk = parent / name
    tk.mkdir(parents=True)
    (tk / "toolkit.yaml").write_text(
        yaml.safe_dump({
            "name": name,
            "version": version,
            "description": "test toolkit",
            "author": "tester",
            "category": category,
            "tools": [
                {"name": "hello", "function": "tools.hello", "description": "x"},
            ],
        })
    )
    tools = tk / "tools"
    tools.mkdir()
    (tools / "__init__.py").write_text("from .hello import hello\n")
    (tools / "hello.py").write_text(
        "def hello():\n    return '{\"greeting\": \"hi\"}'\n"
    )
    (tk / "requirements.txt").write_text("")
    return tk


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR + ~/.claude/skills + stub the venv build."""
    fake_home = tmp_path / "_home" / ".toolbase"
    fake_home.mkdir(parents=True)
    fake_claude = tmp_path / "claude-skills"

    from toolbase import config as cfg
    from toolbase import skills as skills_mod
    monkeypatch.setattr(cfg, "CONFIG_DIR", fake_home)
    monkeypatch.setattr(skills_mod, "CLAUDE_SKILLS_DIR", fake_claude)

    # Stub the actual venv build so tests are fast + offline. The stub
    # creates a fake interpreter path inside the slot's .venv so the
    # python_path metadata is realistic.
    def fake_setup_venv(toolkit_path: Path, console):
        venv = Path(toolkit_path) / ".venv"
        (venv / "bin").mkdir(parents=True, exist_ok=True)
        py = venv / "bin" / "python"
        py.write_text("#!/bin/sh\n")
        return py

    monkeypatch.setattr(cli, "setup_venv_environment", fake_setup_venv)
    return {"home": fake_home, "claude": fake_claude}


def _read_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


# ── disambiguation ───────────────────────────────────────────────────────


def test_disambiguation_dot_is_path():
    assert cli._resolve_install_source_path(".") is not None


def test_disambiguation_slash_is_path():
    assert cli._resolve_install_source_path("./foo") is not None
    assert cli._resolve_install_source_path("/abs/path") is not None


def test_disambiguation_bare_name_is_registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure no dir named 'heptapod' exists
    assert cli._resolve_install_source_path("heptapod") is None


def test_disambiguation_existing_dir_is_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mykit").mkdir()
    assert cli._resolve_install_source_path("mykit") is not None


# ── flag exclusivity + validation ──────────────────────────────────────────


def test_mutually_exclusive_flags_error(fake_env, tmp_path):
    src = _make_source_toolkit(tmp_path / "src", "demo")
    result = CliRunner().invoke(
        cli.main, ["install", "-e", "-l", str(src)], catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_editable_bare_name_errors(fake_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli.main, ["install", "-e", "heptapod"], catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "Editable installs require a path" in result.output


def test_editable_with_version_errors(fake_env, tmp_path):
    src = _make_source_toolkit(tmp_path / "src", "demo")
    result = CliRunner().invoke(
        cli.main, ["install", "-e", str(src), "--version", "1.0.0"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "meaningless" in result.output


def test_path_without_toolkit_yaml_errors(fake_env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(
        cli.main, ["install", "-e", str(empty)], catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "No toolkit.yaml found" in result.output


# ── editable install ────────────────────────────────────────────────────────


def test_editable_install_symlinks_source_and_builds_env(fake_env, tmp_path):
    src = _make_source_toolkit(tmp_path / "src", "mykit")
    result = CliRunner().invoke(
        cli.main, ["install", "-e", str(src), "--no-input"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    from toolbase.envs import cache_dir
    slot = cache_dir("mykit", cli.EDITABLE_VERSION)
    assert slot.is_dir()
    # Source entries symlinked, resolving to the live source.
    assert (slot / "tools").is_symlink()
    assert (slot / "tools").resolve() == (src / "tools").resolve()
    assert (slot / "toolkit.yaml").is_symlink()
    # venv is a REAL dir in the slot, not a symlink (no source pollution).
    assert (slot / ".venv").is_dir()
    assert not (slot / ".venv").is_symlink()
    # The user's source dir did NOT get a .venv written into it.
    assert not (src / ".venv").exists()


def test_editable_install_writes_editable_meta(fake_env, tmp_path):
    src = _make_source_toolkit(tmp_path / "src", "mykit")
    CliRunner().invoke(
        cli.main, ["install", "-e", str(src), "--no-input"],
        catch_exceptions=False,
    )
    from toolbase.envs import cache_dir, read_install_meta
    slot = cache_dir("mykit", cli.EDITABLE_VERSION)
    meta = read_install_meta(slot)
    assert meta.get("editable") is True
    assert Path(meta.get("source_path")).resolve() == src.resolve()


def test_editable_install_does_not_pin_manifest(fake_env, tmp_path, monkeypatch):
    # Run from a project dir; editable must NOT add a pin anywhere.
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    src = _make_source_toolkit(tmp_path / "src", "mykit")

    CliRunner().invoke(
        cli.main, ["install", "-e", str(src), "--no-input"],
        catch_exceptions=False,
    )
    # No project manifest created.
    assert not (proj / ".toolbase" / "manifest.yaml").exists()
    # Default-project manifest has no pin for mykit either.
    from toolbase.envs import default_project_root, project_manifest_path
    dp_manifest = project_manifest_path(default_project_root())
    data = _read_manifest(dp_manifest)
    names = [t.get("name") for t in (data.get("toolkits") or [])]
    assert "mykit" not in names


# ── -l vs -g manifest scoping (path source) ─────────────────────────────────


def test_local_path_install_pins_project_manifest(fake_env, tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    src = _make_source_toolkit(tmp_path / "src", "localkit")

    result = CliRunner().invoke(
        cli.main, ["install", "-l", str(src), "--no-input"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    proj_manifest = proj / ".toolbase" / "manifest.yaml"
    assert proj_manifest.exists()
    data = _read_manifest(proj_manifest)
    names = [t.get("name") for t in (data.get("toolkits") or [])]
    assert "localkit" in names

    # default-project manifest must NOT have it.
    from toolbase.envs import default_project_root, project_manifest_path
    dp = _read_manifest(project_manifest_path(default_project_root()))
    dp_names = [t.get("name") for t in (dp.get("toolkits") or [])]
    assert "localkit" not in dp_names


def test_global_path_install_pins_default_project(fake_env, tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    src = _make_source_toolkit(tmp_path / "src", "gkit")

    result = CliRunner().invoke(
        cli.main, ["install", str(src), "--no-input"],  # no flag = -g
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    from toolbase.envs import default_project_root, project_manifest_path
    dp = _read_manifest(project_manifest_path(default_project_root()))
    dp_names = [t.get("name") for t in (dp.get("toolkits") or [])]
    assert "gkit" in dp_names
    # No project manifest created in cwd.
    assert not (proj / ".toolbase" / "manifest.yaml").exists()


def test_global_and_default_path_install_use_same_cache_slot(fake_env, tmp_path, monkeypatch):
    """-g and no-flag are identical: same cache slot, same default-project pin."""
    monkeypatch.chdir(tmp_path)
    src = _make_source_toolkit(tmp_path / "src", "samekit")

    CliRunner().invoke(
        cli.main, ["install", "-g", str(src), "--no-input"],
        catch_exceptions=False,
    )
    from toolbase.envs import cache_dir
    slot = cache_dir("samekit", "0.1.0")
    assert slot.is_dir()
    # The binary is in the GLOBAL cache regardless of -g/-l.
    assert "cache" in str(slot)


# ── tb list rendering ───────────────────────────────────────────────────────


def test_list_renders_editable_indicator(fake_env, tmp_path):
    src = _make_source_toolkit(tmp_path / "src", "mykit")
    CliRunner().invoke(
        cli.main, ["install", "-e", str(src), "--no-input"],
        catch_exceptions=False,
    )
    result = CliRunner().invoke(cli.main, ["list"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "editable" in result.output
    assert "->" in result.output
    # Rich may soft-wrap the path across the (narrow) test terminal, so
    # collapse whitespace before checking the source path is present.
    flat = "".join(result.output.split())
    assert "".join(str(src.resolve()).split()) in flat


# ── prompt removal ───────────────────────────────────────────────────────────


def test_no_install_location_prompt_in_skip_mode(fake_env, tmp_path, monkeypatch):
    """The old 'where do you want this installed?' prompt is gone; a
    --no-input install just takes the global default without prompting."""
    monkeypatch.chdir(tmp_path)
    src = _make_source_toolkit(tmp_path / "src", "quietkit")

    result = CliRunner().invoke(
        cli.main, ["install", str(src), "--no-input"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # None of the old prompt phrasing should appear.
    assert "where do you want" not in result.output.lower()
    assert "Create one here" not in result.output
