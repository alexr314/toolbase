"""Tests for the Antigravity adapter (``tb connect antigravity`` backend).

Mirrors ``test_connect_opencode.py`` for Antigravity's ``mcp_config.json``:
create when absent, preserve other servers / top-level keys, overwrite a stale
entry, idempotency, refuse malformed JSON, dry-run, env block, uninstall, and
status reporting — plus the two customization roots (global
``~/.gemini/config``, workspace ``.agents/``), the zero-byte file the IDE
ships, and the skill target (dir layout with frontmatter kept).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase.connect.antigravity import AntigravityAdapter, AntigravityConfigError


def _adapter():
    return AntigravityAdapter()


def _install(root: Path, **kw):
    return _adapter().install(
        scope="project", project_root=root, server_name="toolbase",
        command="toolbase", args=["serve"], **kw,
    )


def _cfg(root: Path) -> Path:
    return root / ".agents" / "mcp_config.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


# ── install / merge ───────────────────────────────────────────────────


def test_create_when_absent(tmp_path: Path):
    path = _install(tmp_path)
    assert path == _cfg(tmp_path)
    assert _load(path)["mcpServers"]["toolbase"] == {
        "command": "toolbase", "args": ["serve"],
    }


def test_preserves_other_servers_and_keys(tmp_path: Path):
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "mcpServers": {"other": {"command": "x", "args": []}},
        "somethingElse": {"keep": True},
    }))
    _install(tmp_path)
    data = _load(path)
    assert data["somethingElse"] == {"keep": True}
    assert "other" in data["mcpServers"] and "toolbase" in data["mcpServers"]


def test_overwrites_stale_entry(tmp_path: Path):
    _install(tmp_path)
    _adapter().install(
        scope="project", project_root=tmp_path, server_name="toolbase",
        command="/abs/toolbase", args=["serve", "--x"],
    )
    entry = _load(_cfg(tmp_path))["mcpServers"]["toolbase"]
    assert entry == {"command": "/abs/toolbase", "args": ["serve", "--x"]}


def test_idempotent(tmp_path: Path):
    _install(tmp_path)
    _install(tmp_path)
    assert list(_load(_cfg(tmp_path))["mcpServers"]) == ["toolbase"]


def test_env_block(tmp_path: Path):
    _adapter().install(
        scope="project", project_root=tmp_path, server_name="toolbase",
        command="toolbase", args=["serve"], env={"TB_TOKEN": "abc"},
    )
    entry = _load(_cfg(tmp_path))["mcpServers"]["toolbase"]
    assert entry["env"] == {"TB_TOKEN": "abc"}


def test_dry_run_writes_nothing(tmp_path: Path):
    path = _install(tmp_path, dry_run=True)
    assert not path.exists()


def test_refuses_malformed_json(tmp_path: Path):
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    with pytest.raises(AntigravityConfigError):
        _install(tmp_path)


def test_refuses_non_object_servers(tmp_path: Path):
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": []}))
    with pytest.raises(AntigravityConfigError):
        _install(tmp_path)


def test_comment_hint_on_jsonc(tmp_path: Path):
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{\n  // mine\n  "mcpServers": {}\n}\n')
    with pytest.raises(AntigravityConfigError) as ei:
        _install(tmp_path)
    assert "comments" in str(ei.value)


def test_empty_file_is_an_empty_config(tmp_path: Path):
    # The Antigravity IDE creates a zero-byte mcp_config.json; treat it as {}
    # rather than refusing to write over it.
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("")
    _install(tmp_path)
    assert "toolbase" in _load(path)["mcpServers"]


# ── scopes / paths ────────────────────────────────────────────────────


def test_user_scope_is_global_customization_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert _adapter().config_path("user", None) == (
        tmp_path / ".gemini" / "config" / "mcp_config.json"
    )


def test_project_scope_requires_root():
    with pytest.raises(ValueError):
        _adapter().config_path("project", None)


def test_unknown_scope_rejected():
    with pytest.raises(ValueError):
        _adapter().config_path("nope", None)


def test_supported_scopes_use_native_names():
    assert _adapter().supported_scopes() == {
        "user": "global", "project": "workspace",
    }


def test_project_scope_note_warns_about_the_cli():
    note = _adapter().project_scope_note()
    assert note and "agy CLI" in note


# ── detection ─────────────────────────────────────────────────────────


def test_not_available_with_bare_gemini_dir(tmp_path: Path, monkeypatch):
    # ~/.gemini alone is the Gemini CLI, not Antigravity.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: None)
    (tmp_path / ".gemini").mkdir()
    assert _adapter().is_available().detected is False


def test_available_from_mcp_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: None)
    cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("")
    assert _adapter().is_available().detected is True


def test_available_from_cli_state_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: None)
    (tmp_path / ".gemini" / "antigravity-cli").mkdir(parents=True)
    avail = _adapter().is_available()
    assert avail.detected is True and "antigravity-cli" in avail.detail


# ── uninstall / status ─────────────────────────────────────────────────


def test_uninstall_removes_only_toolbase(tmp_path: Path):
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "mcpServers": {
            "toolbase": {"command": "toolbase", "args": ["serve"]},
            "other": {"command": "x", "args": []},
        },
    }))
    assert _adapter().uninstall(
        scope="project", project_root=tmp_path, server_name="toolbase",
    ) is True
    servers = _load(path)["mcpServers"]
    assert "toolbase" not in servers and "other" in servers


def test_uninstall_absent_is_noop(tmp_path: Path):
    assert _adapter().uninstall(
        scope="project", project_root=tmp_path, server_name="toolbase",
    ) is False


def test_status_reports_presence_and_command(tmp_path: Path):
    _install(tmp_path)
    proj = next(e for e in _adapter().status(tmp_path) if e.scope == "project")
    assert proj.present is True
    assert proj.command == "toolbase"
    assert list(proj.args or []) == ["serve"]


def test_status_absent_is_not_present(tmp_path: Path):
    proj = next(e for e in _adapter().status(tmp_path) if e.scope == "project")
    assert proj.present is False
    assert proj.path == _cfg(tmp_path)


# ── skill surface ──────────────────────────────────────────────────────


def test_skill_target_is_dir_layout_in_global_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    target = _adapter().skill_target()
    assert target is not None
    assert target.harness == "antigravity"
    assert target.root == tmp_path / ".gemini" / "config" / "skills"
    assert target.layout == "dir"
    assert target.keep_frontmatter is True


def test_project_skill_target_is_the_workspace_root(tmp_path: Path):
    """``skills/`` lives in the customization root beside mcp_config.json,
    and there are two of those roots — so the scope map is the same one."""
    from toolbase.connect.antigravity import WORKSPACE_ROOT_DIR
    target = _adapter().skill_target("project", tmp_path)
    assert target.root == tmp_path / WORKSPACE_ROOT_DIR / "skills"
    assert target.root.parent == _adapter().config_path("project", tmp_path).parent
    assert target.layout == "dir"


def test_project_skill_target_needs_a_root():
    with pytest.raises(ValueError):
        _adapter().skill_target("project", None)


def test_unknown_skill_scope_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        _adapter().skill_target("workspace", tmp_path)


def test_surface_writes_skill_md_with_frontmatter(tmp_path: Path):
    from toolbase import skills as sk
    tk = tmp_path / "tk"
    (tk / "skills").mkdir(parents=True)
    (tk / "skills" / "guide.md").write_text(
        "---\nname: Guide\ndescription: How to X.\n---\n\n# Guide\nBody.\n"
    )
    target = sk.SkillTarget(
        "antigravity", tmp_path / "skills", layout="dir", keep_frontmatter=True,
    )
    assert sk.surface_skills("tk", tk, target) == ["tk__guide"]
    text = (target.root / "tk__guide" / "SKILL.md").read_text()
    assert "description: How to X." in text and "# Guide" in text


# ── CLI surface ────────────────────────────────────────────────────────


def test_cli_harnesses_lists_antigravity():
    res = CliRunner().invoke(cli.main, ["connect", "--harnesses"])
    assert res.exit_code == 0, res.output
    assert "antigravity" in res.output
