"""Tests for the ``expected_toolkits`` field.

Covers:
- schema validation (shape, naming rules)
- the install command's companion-prompt flow:
  - non-TTY/skip mode prints the install command without prompting
  - already-installed companions are filtered out
  - recursive install paths a companion through with --no-input
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
import requests
import yaml
from click.testing import CliRunner

from toolbase import cli, validation


# ── schema ─────────────────────────────────────────────────────────────────


def _meta(**overrides) -> dict:
    base = {
        "name": "demo",
        "version": "0.1.0",
        "description": "x",
        "author": "a",
        "category": "utils",
        "tools": [
            {"name": "noop", "function": "tools.noop", "description": "x"},
        ],
    }
    base.update(overrides)
    return base


def test_expected_toolkits_field_accepted():
    m = validation.ToolkitMetadata(**_meta(expected_toolkits=["arxiv-search"]))
    assert m.expected_toolkits == ["arxiv-search"]


def test_expected_toolkits_default_empty():
    m = validation.ToolkitMetadata(**_meta())
    assert m.expected_toolkits == []


def test_expected_toolkits_lowercases():
    m = validation.ToolkitMetadata(**_meta(expected_toolkits=["ArXiV-Search"]))
    assert m.expected_toolkits == ["arxiv-search"]


def test_expected_toolkits_rejects_bad_shape():
    with pytest.raises(Exception):
        validation.ToolkitMetadata(**_meta(expected_toolkits=[123]))


def test_expected_toolkits_rejects_invalid_name():
    with pytest.raises(Exception) as ei:
        validation.ToolkitMetadata(
            **_meta(expected_toolkits=["bad name with spaces"])
        )
    assert "alphanumeric" in str(ei.value)


def test_expected_toolkits_rejects_too_short():
    with pytest.raises(Exception) as ei:
        validation.ToolkitMetadata(**_meta(expected_toolkits=["xy"]))
    assert "too short" in str(ei.value)


# ── install-time companion flow ────────────────────────────────────────────


class _FakeMetaResp:
    status_code = 200
    def __init__(self, body):
        self._body = body
    def json(self):
        return self._body


class _FakeTarballResp:
    status_code = 200
    def __init__(self, path: Path):
        self.headers = {"content-length": str(path.stat().st_size)}
        self._path = path
    def iter_content(self, chunk_size=8192):
        with open(self._path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk


def _build_synth_toolkit(work: Path, name: str, expected: list) -> Path:
    """Create a tiny installable toolkit on disk and tarball it."""
    src = work / f"{name}-src"
    src.mkdir()
    (src / "toolkit.yaml").write_text(yaml.safe_dump({
        "name": name,
        "version": "0.1.0",
        "description": "x",
        "author": "a",
        "category": "utils",
        "expected_toolkits": expected,
        "tools": [
            {"name": "noop", "function": "tools.noop", "description": "x"},
        ],
    }))
    (src / "tools").mkdir()
    (src / "tools" / "__init__.py").write_text("")
    (src / "tools" / "noop.py").write_text("def noop():\n    return '{}'\n")
    (src / "requirements.txt").write_text("")
    import tarfile
    tar_path = work / f"{name}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in src.rglob("*"):
            if item.is_file():
                tar.add(item, arcname=item.relative_to(src), recursive=False)
    return tar_path


def test_install_skip_mode_lists_companions_without_prompting(tmp_path: Path, monkeypatch):
    """When stdin is not a TTY (or --no-input), surface the companion list
    as a message + the install command to run, but don't prompt."""
    fake_home = tmp_path / "_home" / ".toolbase"
    fake_home.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    fake_claude = tmp_path / "claude-skills"

    tarball = _build_synth_toolkit(work, "primary", ["companion"])

    def fake_get(url, *a, **kw):
        if url.endswith("/api/toolkits/primary"):
            return _FakeMetaResp({
                "name": "primary",
                "latest_version": "0.1.0",
                "versions": [{"version": "0.1.0", "tarball_url": "x"}],
            })
        return _FakeTarballResp(tarball)

    from toolbase import config as cfg
    from toolbase import skills as skills_mod

    with mock.patch.object(cfg, "CONFIG_DIR", fake_home), \
         mock.patch.object(skills_mod, "CLAUDE_SKILLS_DIR", fake_claude), \
         mock.patch.object(requests, "get", side_effect=fake_get):
        result = CliRunner().invoke(
            cli.main, ["install", "primary", "--no-input"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "designed to work with" in result.output
    assert "companion" in result.output
    # In skip mode we *don't* prompt — we just suggest the command.
    assert "toolbase install companion" in result.output


def test_install_filters_already_installed_companions(tmp_path: Path, monkeypatch):
    """A companion that's already in the cache shouldn't be mentioned."""
    fake_home = tmp_path / "_home" / ".toolbase"
    fake_home.mkdir(parents=True)
    # Pretend `companion` is already installed at any version.
    companion_slot = fake_home / "cache" / "companion" / "0.1.0"
    companion_slot.mkdir(parents=True)
    (companion_slot / ".tb_meta.json").write_text(
        json.dumps({"name": "companion", "version": "0.1.0", "environment": "venv"})
    )

    work = tmp_path / "work"
    work.mkdir()
    tarball = _build_synth_toolkit(work, "primary", ["companion"])
    fake_claude = tmp_path / "claude-skills"

    def fake_get(url, *a, **kw):
        if url.endswith("/api/toolkits/primary"):
            return _FakeMetaResp({
                "name": "primary",
                "latest_version": "0.1.0",
                "versions": [{"version": "0.1.0", "tarball_url": "x"}],
            })
        return _FakeTarballResp(tarball)

    from toolbase import config as cfg
    from toolbase import skills as skills_mod

    with mock.patch.object(cfg, "CONFIG_DIR", fake_home), \
         mock.patch.object(skills_mod, "CLAUDE_SKILLS_DIR", fake_claude), \
         mock.patch.object(requests, "get", side_effect=fake_get):
        result = CliRunner().invoke(
            cli.main, ["install", "primary", "--no-input"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    # Since the only expected companion is already installed, we shouldn't
    # see the "designed to work with" message at all.
    assert "designed to work with" not in result.output
