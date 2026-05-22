"""Integration tests for the publish-time version pre-flight check.

The pre-flight check fetches the toolkit's metadata from the registry and
blocks publish when:
  - the proposed version already exists, or
  - the proposed version is *less than* the latest already on the registry
    (unless the user passes ``--allow-version-decrease``).

We test the CLI command end-to-end via Click's CliRunner, with a
synthetic toolkit on disk and the registry HTTP call mocked.
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


def _make_minimal_toolkit(tmp_path: Path, name: str, version: str) -> Path:
    """Build a directory that passes ``validate_toolkit``."""
    tk = tmp_path / name
    tk.mkdir()
    (tk / "toolkit.yaml").write_text(
        yaml.safe_dump({
            "name": name,
            "version": version,
            "description": "test",
            "author": "tester",
            "category": "utils",
            "tools": [
                {"name": "noop", "function": "tools.noop", "description": "x"},
            ],
        })
    )
    (tk / "tools").mkdir()
    (tk / "tools" / "__init__.py").write_text(
        "from .noop import noop\n"
    )
    (tk / "tools" / "noop.py").write_text("def noop():\n    return '{}'\n")
    (tk / "mcp").mkdir()
    (tk / "mcp" / "__init__.py").write_text("")
    (tk / "mcp" / "server_stdio.py").write_text("")
    (tk / "requirements.txt").write_text("orchestral-ai>=1.0.0\n")
    return tk


def _fake_meta_response(versions: list, status: int = 200):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = {
        "name": "demo",
        "latest_version": versions[-1] if versions else None,
        "versions": [{"version": v} for v in versions],
    }
    return resp


# ── tests ──────────────────────────────────────────────────────────────────


def test_publish_blocks_when_version_already_exists(tmp_path: Path, monkeypatch):
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tk)

    with mock.patch.object(
        requests, "get", return_value=_fake_meta_response(["0.0.9", "0.1.0"]),
    ):
        result = CliRunner().invoke(cli.main, ["publish"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "0.1.0 already exists" in result.output
    # Suggests a bump.
    assert "0.1.1" in result.output


def test_publish_blocks_on_version_decrease(tmp_path: Path, monkeypatch):
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tk)

    with mock.patch.object(
        requests, "get", return_value=_fake_meta_response(["0.0.9", "0.2.0"]),
    ):
        result = CliRunner().invoke(cli.main, ["publish"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "not greater than" in result.output
    assert "0.2.0" in result.output
    assert "--allow-version-decrease" in result.output


def test_publish_allow_decrease_bypasses_check(tmp_path: Path, monkeypatch):
    """With --allow-version-decrease, the regression check is skipped.

    We don't actually publish here — the test stops at the network call
    by monkeypatching the second requests.get to fail loudly. We only
    care that the pre-flight didn't *block* on version decrease.
    """
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tk)

    # 1st GET: registry meta (returns higher version than ours).
    # Subsequent calls: shouldn't matter because we abort with no token.
    meta_resp = _fake_meta_response(["0.2.0"])
    monkeypatch.setattr(requests, "get", lambda *a, **kw: meta_resp)

    # No token configured → publish should fail at the auth step, not the
    # version-decrease step. That confirms the pre-flight bypassed.
    from toolbase import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / ".toolbase-fake")

    result = CliRunner().invoke(
        cli.main, ["publish", "--allow-version-decrease"], catch_exceptions=False,
    )
    assert "not greater than" not in result.output
    # We expect to fail later — at "No authentication token found".
    assert "authentication token" in result.output.lower()
    assert result.exit_code == 1


def test_publish_passes_pre_flight_on_strict_increase(tmp_path: Path, monkeypatch):
    """0.2.0 is strictly greater than the registry's 0.1.0 — pre-flight passes."""
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.2.0")
    monkeypatch.chdir(tk)

    monkeypatch.setattr(
        requests, "get",
        lambda *a, **kw: _fake_meta_response(["0.1.0"]),
    )

    from toolbase import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / ".toolbase-fake")

    result = CliRunner().invoke(cli.main, ["publish"], catch_exceptions=False)
    # Pre-flight passes; we abort later at the auth step.
    assert "not greater than" not in result.output
    assert "already exists" not in result.output
    assert "authentication token" in result.output.lower()


def test_publish_dry_run_skips_pre_flight(tmp_path: Path, monkeypatch):
    """--dry-run is offline; pre-flight must not run."""
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tk)

    # If the test made a network call, this would crash.
    def _fail(*a, **kw):
        raise AssertionError("dry-run should not hit the registry")

    monkeypatch.setattr(requests, "get", _fail)

    result = CliRunner().invoke(cli.main, ["publish", "--dry-run"], catch_exceptions=False)
    assert result.exit_code == 0


def test_publish_silent_when_registry_unreachable(tmp_path: Path, monkeypatch):
    """Network errors fall through silently; the registry has the final word at upload."""
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tk)

    def _network_err(*a, **kw):
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", _network_err)

    from toolbase import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / ".toolbase-fake")

    result = CliRunner().invoke(cli.main, ["publish"], catch_exceptions=False)
    # Pre-flight didn't block; we abort later at auth.
    assert "authentication token" in result.output.lower()
