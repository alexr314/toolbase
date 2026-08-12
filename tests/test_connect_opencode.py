"""Tests for the OpenCode adapter (``tb connect opencode`` backend).

Mirrors ``test_connect_codex.py`` for OpenCode's JSON config: create when
absent, preserve other servers / top-level keys / $schema, overwrite a stale
entry, idempotency, refuse malformed JSON, JSONC-with-comments handling,
dry-run, env block, uninstall, and status reporting — plus the skill-target
(``~/.config/opencode/skills`` native skills, with the retired ``command/``
prompt surface declared as legacy so it gets cleared).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase.connect.opencode import OpenCodeAdapter, OpenCodeConfigError


def _adapter():
    return OpenCodeAdapter()


def _install(root: Path, **kw):
    return _adapter().install(
        scope="project", project_root=root, server_name="toolbase",
        command="toolbase", args=["serve"], **kw,
    )


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


# ── install / merge ───────────────────────────────────────────────────


def test_create_when_absent(tmp_path: Path):
    path = _install(tmp_path)
    assert path == tmp_path / "opencode.json"
    data = _load(path)
    assert data["mcp"]["toolbase"] == {
        "type": "local", "command": ["toolbase", "serve"], "enabled": True,
    }
    # A fresh file gets the schema line.
    assert data["$schema"] == "https://opencode.ai/config.json"


def test_command_is_a_single_array(tmp_path: Path):
    _install(tmp_path)
    entry = _load(tmp_path / "opencode.json")["mcp"]["toolbase"]
    assert entry["command"] == ["toolbase", "serve"]  # not command + args split


def test_preserves_other_servers_keys_and_schema(tmp_path: Path):
    path = tmp_path / "opencode.json"
    path.write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "theme": "dark",
        "mcp": {"other": {"type": "local", "command": ["x"], "enabled": True}},
    }))
    _install(tmp_path)
    data = _load(path)
    assert data["theme"] == "dark"
    assert "other" in data["mcp"]
    assert "toolbase" in data["mcp"]


def test_overwrites_stale_entry(tmp_path: Path):
    _install(tmp_path)
    _adapter().install(
        scope="project", project_root=tmp_path, server_name="toolbase",
        command="/abs/toolbase", args=["serve", "--x"],
    )
    entry = _load(tmp_path / "opencode.json")["mcp"]["toolbase"]
    assert entry["command"] == ["/abs/toolbase", "serve", "--x"]


def test_idempotent(tmp_path: Path):
    _install(tmp_path)
    _install(tmp_path)
    servers = _load(tmp_path / "opencode.json")["mcp"]
    assert list(servers) == ["toolbase"]


def test_env_block_written_as_environment(tmp_path: Path):
    _adapter().install(
        scope="project", project_root=tmp_path, server_name="toolbase",
        command="toolbase", args=["serve"], env={"TB_TOKEN": "abc"},
    )
    entry = _load(tmp_path / "opencode.json")["mcp"]["toolbase"]
    assert entry["environment"] == {"TB_TOKEN": "abc"}


def test_dry_run_writes_nothing(tmp_path: Path):
    path = _install(tmp_path, dry_run=True)
    assert not path.exists()


def test_refuses_malformed_json(tmp_path: Path):
    (tmp_path / "opencode.json").write_text("{ not json")
    with pytest.raises(OpenCodeConfigError):
        _install(tmp_path)


# ── JSONC handling ─────────────────────────────────────────────────────


def test_edits_existing_jsonc_in_place(tmp_path: Path):
    # A comment-free .jsonc is valid JSON; we edit it rather than make a .json.
    (tmp_path / "opencode.jsonc").write_text(
        '{\n  "$schema": "https://opencode.ai/config.json"\n}\n'
    )
    path = _install(tmp_path)
    assert path.name == "opencode.jsonc"
    assert not (tmp_path / "opencode.json").exists()
    assert "toolbase" in _load(path)["mcp"]


def test_refuses_jsonc_with_comments(tmp_path: Path):
    (tmp_path / "opencode.jsonc").write_text(
        '{\n  // my settings\n  "$schema": "x"\n}\n'
    )
    with pytest.raises(OpenCodeConfigError) as ei:
        _install(tmp_path)
    assert "JSONC" in str(ei.value)


# ── uninstall / status ─────────────────────────────────────────────────


def test_uninstall_removes_only_toolbase(tmp_path: Path):
    path = tmp_path / "opencode.json"
    path.write_text(json.dumps({
        "mcp": {
            "toolbase": {"type": "local", "command": ["toolbase", "serve"]},
            "other": {"type": "local", "command": ["x"]},
        },
    }))
    assert _adapter().uninstall(
        scope="project", project_root=tmp_path, server_name="toolbase",
    ) is True
    servers = _load(path)["mcp"]
    assert "toolbase" not in servers and "other" in servers


def test_uninstall_absent_is_noop(tmp_path: Path):
    assert _adapter().uninstall(
        scope="project", project_root=tmp_path, server_name="toolbase",
    ) is False


def test_status_reports_presence_and_splits_command(tmp_path: Path):
    _install(tmp_path)
    proj = next(e for e in _adapter().status(tmp_path) if e.scope == "project")
    assert proj.present is True
    assert proj.command == "toolbase"
    assert list(proj.args or []) == ["serve"]


def test_project_scope_requires_root():
    with pytest.raises(ValueError):
        _adapter().config_path("project", None)


def test_user_scope_uses_xdg(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert _adapter().config_path("user", None) == tmp_path / "opencode" / "opencode.json"


def test_has_project_scope_note():
    note = _adapter().project_scope_note()
    assert note and "merges" in note.lower()


# ── skill surface ──────────────────────────────────────────────────────


def test_skill_target_is_the_native_skills_dir(tmp_path: Path, monkeypatch):
    """OpenCode grew a real skill loader (`**/SKILL.md`, surfaced to the
    model by its description). We used to approximate it with flat
    `command/` prompt files, which are user-invoked slash commands only --
    the model never learned they existed."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    target = _adapter().skill_target()
    assert target is not None
    assert target.harness == "opencode"
    assert target.root == tmp_path / "opencode" / "skills"
    assert target.layout == "dir"
    assert target.keep_frontmatter is True


def test_project_skill_target_is_the_project_skills_dir(tmp_path: Path):
    target = _adapter().skill_target("project", tmp_path)
    assert target.root == tmp_path / ".opencode" / "skills"
    assert target.layout == "dir"


def test_legacy_command_surface_is_declared(tmp_path: Path, monkeypatch):
    """OpenCode still reads command/, so a skill left there would be the
    same guide a second time, stripped to a prompt."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = _adapter().legacy_skill_targets()
    assert [t.root for t in legacy] == [tmp_path / "opencode" / "command"]
    assert legacy[0].layout == "flat"


def test_project_skill_target_needs_a_root():
    with pytest.raises(ValueError):
        _adapter().skill_target("project", None)


def test_unknown_skill_scope_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        _adapter().skill_target("global", tmp_path)


def test_surface_keeps_only_description(tmp_path: Path):
    from toolbase import skills as sk
    tk = tmp_path / "tk"
    (tk / "skills").mkdir(parents=True)
    (tk / "skills" / "guide.md").write_text(
        "---\nname: Guide\ndescription: How to X.\nbundle: pro\n---\n\n# Guide\nBody.\n"
    )
    target = sk.SkillTarget(
        "opencode", tmp_path / "command", layout="flat",
        keep_frontmatter=False, frontmatter_keys=["description"],
    )
    surfaced = sk.surface_skills("tk", tk, target)
    assert surfaced == ["tk__guide"]
    text = (target.root / "tk__guide.md").read_text()
    assert "description: How to X." in text
    assert "name:" not in text and "bundle:" not in text
    assert "# Guide" in text


# ── CLI surface ────────────────────────────────────────────────────────


def test_cli_harnesses_lists_opencode():
    res = CliRunner().invoke(cli.main, ["connect", "--harnesses"])
    assert res.exit_code == 0, res.output
    assert "opencode" in res.output
