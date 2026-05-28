"""Tests for the Claude Code adapter (``tb connect`` backend).

Covers the non-destructive merge / atomic write contract: create when
absent, preserve other servers and other top-level keys, overwrite a
stale toolbase entry, refuse malformed JSON, idempotency, uninstall, and
status reporting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolbase.connect.claude_code import ClaudeCodeAdapter, ClaudeCodeConfigError


def _adapter():
    return ClaudeCodeAdapter()


def _install(root: Path, **kw):
    return _adapter().install(
        scope="project", project_root=root, server_name="toolbase",
        command="toolbase", args=["serve"], **kw,
    )


def test_create_when_absent(tmp_path: Path):
    path = _install(tmp_path)
    assert path == tmp_path / ".mcp.json"
    data = json.loads(path.read_text())
    assert data["mcpServers"]["toolbase"] == {
        "type": "stdio", "command": "toolbase", "args": ["serve"],
    }


def test_preserves_other_servers_and_keys(tmp_path: Path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({
        "mcpServers": {"other": {"type": "stdio", "command": "x"}},
        "unrelatedTopKey": {"keep": True},
    }))
    _install(tmp_path)
    data = json.loads(p.read_text())
    assert "other" in data["mcpServers"]          # preserved
    assert data["unrelatedTopKey"] == {"keep": True}  # preserved
    assert "toolbase" in data["mcpServers"]        # added


def test_overwrites_stale_toolbase_entry(tmp_path: Path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({
        "mcpServers": {"toolbase": {"type": "stdio", "command": "/old/path"}}
    }))
    _install(tmp_path)
    data = json.loads(p.read_text())
    assert data["mcpServers"]["toolbase"]["command"] == "toolbase"


def test_idempotent(tmp_path: Path):
    _install(tmp_path)
    first = (tmp_path / ".mcp.json").read_text()
    _install(tmp_path)
    second = (tmp_path / ".mcp.json").read_text()
    assert first == second


def test_refuses_malformed_json(tmp_path: Path):
    p = tmp_path / ".mcp.json"
    p.write_text("{ not valid json ")
    with pytest.raises(ClaudeCodeConfigError):
        _install(tmp_path)
    # the bad file is left untouched (not clobbered)
    assert p.read_text() == "{ not valid json "


def test_dry_run_writes_nothing(tmp_path: Path):
    path = _install(tmp_path, dry_run=True)
    assert not path.exists()


def test_abspath_command_is_written(tmp_path: Path):
    _adapter().install(
        scope="project", project_root=tmp_path, server_name="toolbase",
        command="/abs/bin/toolbase", args=["serve"],
    )
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["toolbase"]["command"] == "/abs/bin/toolbase"


def test_env_block_written_when_given(tmp_path: Path):
    _adapter().install(
        scope="project", project_root=tmp_path, server_name="toolbase",
        command="toolbase", args=["serve"], env={"K": "v"},
    )
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["toolbase"]["env"] == {"K": "v"}


def test_uninstall_removes_only_toolbase(tmp_path: Path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({
        "mcpServers": {
            "toolbase": {"type": "stdio", "command": "toolbase"},
            "other": {"type": "stdio", "command": "x"},
        }
    }))
    removed = _adapter().uninstall(
        scope="project", project_root=tmp_path, server_name="toolbase",
    )
    assert removed is True
    data = json.loads(p.read_text())
    assert "toolbase" not in data["mcpServers"]
    assert "other" in data["mcpServers"]


def test_uninstall_absent_is_noop(tmp_path: Path):
    removed = _adapter().uninstall(
        scope="project", project_root=tmp_path, server_name="toolbase",
    )
    assert removed is False


def test_status_reports_presence(tmp_path: Path):
    _install(tmp_path)
    entries = _adapter().status(tmp_path)
    proj = next(e for e in entries if e.scope == "project")
    assert proj.present is True
    assert proj.command == "toolbase"
