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


def test_cli_harnesses_lists_codex():
    res = CliRunner().invoke(cli.main, ["connect", "--harnesses"])
    assert res.exit_code == 0, res.output
    assert "codex" in res.output


# ── skill surface ─────────────────────────────────────────────────────


def test_skill_target_is_the_native_skills_dir():
    """Codex reads $CODEX_HOME/skills/<name>/SKILL.md the way Claude Code
    reads ~/.claude/skills — a model-facing skill, not a slash command —
    so frontmatter (the description that makes it discoverable) is kept."""
    target = _adapter().skill_target()
    assert target is not None
    assert target.harness == "codex"
    assert target.root == Path.home() / ".codex" / "skills"
    assert target.layout == "dir"
    assert target.keep_frontmatter is True


def test_project_skill_target_sits_beside_the_project_config(tmp_path):
    """Codex scans a project's .codex for skills — and loads them even
    before the project is trusted, unlike the config.toml next to them."""
    target = _adapter().skill_target("project", tmp_path)
    assert target.root == tmp_path / ".codex" / "skills"
    assert target.layout == "dir"


def test_project_skill_target_needs_a_root():
    with pytest.raises(ValueError):
        _adapter().skill_target("project", None)


def test_unknown_skill_scope_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        _adapter().skill_target("local", tmp_path)


def test_codex_home_env_var_moves_both_config_and_skills(monkeypatch, tmp_path):
    """Codex resolves everything under $CODEX_HOME; splitting the two would
    write the server entry to one home and its skills to another. Project
    scope is rooted in the project, so $CODEX_HOME does not reach it."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))
    adapter = _adapter()
    assert adapter.config_path("user", None) == tmp_path / "elsewhere" / "config.toml"
    assert adapter.skill_target().root == tmp_path / "elsewhere" / "skills"
    assert adapter.skill_target("project", tmp_path / "proj").root == (
        tmp_path / "proj" / ".codex" / "skills"
    )


def test_legacy_prompt_surface_is_declared():
    """The flat prompt files an older toolbase wrote have to be cleared,
    not orphaned: Codex still reads them."""
    legacy = _adapter().legacy_skill_targets()
    assert [t.root for t in legacy] == [Path.home() / ".codex" / "prompts"]
    assert legacy[0].layout == "flat"


def _patch_codex_skill_surface(monkeypatch, tmp_path):
    """Patch connect's toolkit enumeration + Codex skill surfaces so a
    `tb connect codex` in an isolated fs writes under tmp_path.
    Returns (toolkit_dir, skills_dir, legacy_prompts_dir)."""
    from toolbase import skills as skills_mod
    tk = tmp_path / "tk"
    (tk / "skills").mkdir(parents=True)
    (tk / "skills" / "howto.md").write_text(
        "---\nname: Howto\ndescription: d.\n---\n\n# Howto\nSteps.\n"
    )
    skills_root = tmp_path / "codex-skills"
    prompts = tmp_path / "codex-prompts"
    monkeypatch.setattr(cli, "_activated_toolkit_dirs", lambda: {"tk": tk})
    monkeypatch.setattr(
        cli, "_available_bundles_for_surface", lambda name, slot: None
    )
    monkeypatch.setattr(
        CodexAdapter, "skill_target",
        lambda self, scope="user", project_root=None: skills_mod.SkillTarget(
            "codex", skills_root, layout="dir", keep_frontmatter=True,
        ),
    )
    monkeypatch.setattr(
        CodexAdapter, "legacy_skill_targets",
        lambda self: [skills_mod.SkillTarget(
            "codex", prompts, layout="flat", keep_frontmatter=False,
        )],
    )
    return tk, skills_root, prompts


def test_connect_surfaces_activated_toolkit_skills(tmp_path, monkeypatch):
    """`tb connect codex` writes each activated toolkit's skills into the
    native skills dir, frontmatter intact, and `--remove` clears them."""
    _tk, skills_root, _prompts = _patch_codex_skill_surface(monkeypatch, tmp_path)
    surfaced = skills_root / "tk__howto" / "SKILL.md"
    runner = CliRunner()
    with runner.isolated_filesystem():  # sandbox the .codex/config.toml write
        res = runner.invoke(
            cli.main, ["connect", "codex", "-p"], catch_exceptions=False,
        )
        assert res.exit_code == 0, res.output
        assert surfaced.read_text().startswith("---")  # frontmatter kept

        # Removing the server also unsurfaces the skills.
        res = runner.invoke(
            cli.main, ["connect", "codex", "-p", "--remove"],
            catch_exceptions=False,
        )
        assert res.exit_code == 0, res.output
        assert not surfaced.exists()


def test_connect_carries_a_dir_skills_supporting_files(tmp_path, monkeypatch):
    """The old flat layout dropped references/ silently, leaving guides
    pointing at files that weren't there."""
    tk, skills_root, _prompts = _patch_codex_skill_surface(monkeypatch, tmp_path)
    guide = tk / "skills" / "deep"
    (guide / "references").mkdir(parents=True)
    (guide / "SKILL.md").write_text(
        "---\nname: Deep\ndescription: d.\n---\n\nSee references/notes.md.\n"
    )
    (guide / "references" / "notes.md").write_text("notes\n")
    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(
            cli.main, ["connect", "codex", "-p"], catch_exceptions=False,
        )
        assert res.exit_code == 0, res.output
    dest = skills_root / "tk__deep"
    assert (dest / "SKILL.md").exists()
    assert (dest / "references" / "notes.md").read_text() == "notes\n"


def test_connect_clears_the_legacy_prompt_surface(tmp_path, monkeypatch):
    """Codex reads both dirs, so a skill left in prompts/ would show up a
    second time, stripped of the frontmatter that describes it."""
    _tk, skills_root, prompts = _patch_codex_skill_surface(monkeypatch, tmp_path)
    prompts.mkdir()
    stale = prompts / "tk__howto.md"
    stale.write_text("# Howto\nSteps.\n")
    (prompts / ".toolbase-managed.json").write_text('{"tk__howto.md": "tk"}\n')
    # A file we never wrote is not ours to delete.
    other = prompts / "mine.md"
    other.write_text("hand-written\n")

    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(
            cli.main, ["connect", "codex", "-p"], catch_exceptions=False,
        )
        assert res.exit_code == 0, res.output
    assert not stale.exists()
    assert other.exists()
    assert (skills_root / "tk__howto" / "SKILL.md").exists()


def test_connect_no_skills_flag_skips_surfacing(tmp_path, monkeypatch):
    _tk, skills_root, _prompts = _patch_codex_skill_surface(monkeypatch, tmp_path)
    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(
            cli.main, ["connect", "codex", "-p", "--no-skills"],
            catch_exceptions=False,
        )
        assert res.exit_code == 0, res.output
        assert not (skills_root / "tk__howto").exists()
