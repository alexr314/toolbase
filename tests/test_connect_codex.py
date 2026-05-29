"""Tests for the Codex adapter (``tb connect codex`` backend).

Mirrors ``test_connect_claude_code.py`` for the TOML config (``tomlkit``
round-trip): create when absent, preserve other servers / top-level keys /
comments, overwrite a stale entry, idempotency, refuse malformed TOML,
dry-run, env block, uninstall, and status reporting.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase.connect.codex import CodexAdapter, CodexConfigError


def _adapter():
    return CodexAdapter()


def _install(root: Path, **kw):
    return _adapter().install(
        scope="project", project_root=root, server_name="toolbase",
        command="toolbase", args=["serve"], **kw,
    )


def _load(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def test_create_when_absent(tmp_path: Path):
    path = _install(tmp_path)
    assert path == tmp_path / ".codex" / "config.toml"
    data = _load(path)
    assert data["mcp_servers"]["toolbase"] == {
        "command": "toolbase", "args": ["serve"],
    }


def test_renders_dotted_table_header(tmp_path: Path):
    # The fresh entry should render as `[mcp_servers.toolbase]`, not an empty
    # `[mcp_servers]` header followed by the sub-table.
    path = _install(tmp_path)
    assert "[mcp_servers.toolbase]" in path.read_text()


def test_preserves_other_servers_keys_and_comments(tmp_path: Path):
    p = tmp_path / ".codex" / "config.toml"
    p.parent.mkdir(parents=True)
    p.write_text(
        "# my codex config\n"
        'model = "gpt-5.5"\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "other-server"\n'
        'args = ["--flag"]\n'
    )
    _install(tmp_path)
    text = p.read_text()
    assert "# my codex config" in text          # comment preserved
    assert 'model = "gpt-5.5"' in text           # top-level key preserved
    data = _load(p)
    assert "other" in data["mcp_servers"]        # other server preserved
    assert "toolbase" in data["mcp_servers"]     # toolbase added


def test_overwrites_stale_entry(tmp_path: Path):
    p = tmp_path / ".codex" / "config.toml"
    p.parent.mkdir(parents=True)
    p.write_text(
        "[mcp_servers.toolbase]\n"
        'command = "/old/path"\n'
        'args = ["serve"]\n'
    )
    _install(tmp_path)
    assert _load(p)["mcp_servers"]["toolbase"]["command"] == "toolbase"


def test_idempotent(tmp_path: Path):
    _install(tmp_path)
    first = (tmp_path / ".codex" / "config.toml").read_text()
    _install(tmp_path)
    second = (tmp_path / ".codex" / "config.toml").read_text()
    assert first == second


def test_refuses_malformed_toml(tmp_path: Path):
    p = tmp_path / ".codex" / "config.toml"
    p.parent.mkdir(parents=True)
    p.write_text("this = = not toml")
    with pytest.raises(CodexConfigError):
        _install(tmp_path)
    assert p.read_text() == "this = = not toml"  # left untouched


def test_dry_run_writes_nothing(tmp_path: Path):
    path = _install(tmp_path, dry_run=True)
    assert not path.exists()


def test_env_block_written_when_given(tmp_path: Path):
    _adapter().install(
        scope="project", project_root=tmp_path, server_name="toolbase",
        command="toolbase", args=["serve"], env={"K": "v"},
    )
    data = _load(tmp_path / ".codex" / "config.toml")
    assert data["mcp_servers"]["toolbase"]["env"] == {"K": "v"}


def test_uninstall_removes_only_toolbase(tmp_path: Path):
    p = tmp_path / ".codex" / "config.toml"
    p.parent.mkdir(parents=True)
    p.write_text(
        "[mcp_servers.toolbase]\n"
        'command = "toolbase"\n'
        'args = ["serve"]\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "x"\n'
    )
    removed = _adapter().uninstall(
        scope="project", project_root=tmp_path, server_name="toolbase",
    )
    assert removed is True
    data = _load(p)
    assert "toolbase" not in data["mcp_servers"]
    assert "other" in data["mcp_servers"]


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
    assert list(proj.args or []) == ["serve"]


def test_user_scope_path():
    path = _adapter().config_path("user", None)
    assert path == Path.home() / ".codex" / "config.toml"


def test_project_scope_requires_root():
    with pytest.raises(ValueError):
        _adapter().config_path("project", None)


def test_has_project_scope_note():
    note = _adapter().project_scope_note()
    assert note and "trust" in note.lower()


# ── CLI surface ───────────────────────────────────────────────────────


def test_cli_clients_lists_codex():
    res = CliRunner().invoke(cli.main, ["connect", "--clients"])
    assert res.exit_code == 0, res.output
    assert "codex" in res.output
