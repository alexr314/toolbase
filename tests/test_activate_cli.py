"""CLI-level tests for ``tb activate`` / ``tb deactivate`` and the
install/uninstall profile hooks (nothing-active model).

Uses the standard CONFIG_DIR monkeypatch + a synthetic cache so the
commands see "installed" toolkits without a real install.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / ".toolbase"
    fake.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    # Pin cwd as well: profile resolution walks upward for a project, so
    # a run started inside a real toolbase project both reads that
    # project's profile and writes activations into it.
    workdir = tmp_path / "_cwd"
    workdir.mkdir(exist_ok=True)
    monkeypatch.chdir(workdir)
    return fake


def _fake_install(base: Path, name: str, version: str = "1.0.0") -> None:
    slot = base / "cache" / name / version
    slot.mkdir(parents=True, exist_ok=True)
    (slot / ".install_meta.yaml").write_text(
        "schema_version: 1\ninstall_method: venv\npython_version: '3.12'\n"
    )
    (slot / ".tb_meta.json").write_text(f'{{"name": "{name}"}}\n')


def _fake_skillpack_install(base: Path, name: str, version: str = "1.0.0") -> Path:
    """A skills-only toolkit: toolkit.yaml with no tools, plus skills/."""
    slot = base / "cache" / name / version
    (slot / "skills").mkdir(parents=True, exist_ok=True)
    (slot / ".install_meta.yaml").write_text(
        "schema_version: 1\ninstall_method: venv\npython_version: '3.12'\n"
    )
    (slot / ".tb_meta.json").write_text(f'{{"name": "{name}"}}\n')
    (slot / "toolkit.yaml").write_text(
        f"name: {name}\nversion: {version}\ndescription: A skill pack\n"
        f"author: t\ncategory: utils\n"
    )
    (slot / "skills" / "guide.md").write_text(
        "---\nname: Guide\ndescription: A guide.\n---\nBody\n"
    )
    return slot


def _default_profile(base: Path) -> dict:
    p = base / "profiles" / "default.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def test_activate_toolkit_writes_user_default(isolated: Path):
    _fake_install(isolated, "heptapod")
    r = CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    assert r.exit_code == 0, r.output
    data = _default_profile(isolated)
    assert "heptapod" in data["toolkits"]


def test_activate_bundle_narrows(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    r = CliRunner().invoke(cli.main, ["activate", "heptapod/pythia", "-u"])
    assert r.exit_code == 0, r.output
    data = _default_profile(isolated)
    assert data["toolkits"]["heptapod"]["bundles"] == ["pythia"]


def test_activate_not_installed_errors(isolated: Path):
    r = CliRunner().invoke(cli.main, ["activate", "ghost", "-u"])
    assert r.exit_code == 1
    assert "not installed" in r.output


def test_deactivate_removes_entry(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    r = CliRunner().invoke(cli.main, ["deactivate", "heptapod", "-u"])
    assert r.exit_code == 0, r.output
    data = _default_profile(isolated)
    assert "heptapod" not in (data.get("toolkits") or {})


def test_list_marks_active_and_inactive(isolated: Path):
    _fake_install(isolated, "heptapod")
    _fake_install(isolated, "aster")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    r = CliRunner().invoke(cli.main, ["list"])
    assert r.exit_code == 0, r.output
    # heptapod active, aster inactive
    assert "heptapod" in r.output and "aster" in r.output
    assert "Active profile" in r.output


def test_list_json_has_active_field(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    r = CliRunner().invoke(cli.main, ["list", "--json"])
    assert r.exit_code == 0, r.output
    import json
    payload = json.loads(r.output)
    entry = next(e for e in payload if e["name"] == "heptapod")
    assert entry["active"] is True


def test_post_install_activate_helper_uses_the_project(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`-a` follows `tb activate`'s own default — this project — because
    install has no scope of its own to inherit. For the user profile you
    run `tb activate -u` afterwards."""
    _fake_install(isolated, "heptapod")
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    cli._post_install_activate("heptapod")
    # User profile stays empty; the project profile gets the toolkit.
    assert "heptapod" not in (_default_profile(isolated).get("toolkits") or {})
    proj_profile = yaml.safe_load(
        (proj / ".toolbase" / "profiles" / "default.yaml").read_text()
    )
    assert "heptapod" in proj_profile["toolkits"]


def test_uninstall_cleanup_profiles_helper(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    assert "heptapod" in _default_profile(isolated)["toolkits"]
    cli._uninstall_cleanup_profiles("heptapod")
    assert "heptapod" not in (_default_profile(isolated).get("toolkits") or {})


def test_profile_list_and_set_default(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    CliRunner().invoke(cli.main, ["profile", "create", "paper", "-u", "--empty"])
    r = CliRunner().invoke(cli.main, ["profile", "set-default", "paper", "-u"])
    assert r.exit_code == 0, r.output
    serve_yaml = yaml.safe_load((isolated / "serve.yaml").read_text())
    assert serve_yaml["default"]["profile"] == "paper"


# ── per-skill activate / deactivate (activation grammar reused) ──────────


def _route_as_skill(monkeypatch, *, skills: set, tools: set = frozenset()):
    """Force the CLI's skill/tool resolver: pretend the toolkit ships
    `skills` and declares `tools` (for collision tests)."""
    monkeypatch.setattr(cli, "_toolkit_skill_slugs", lambda name: set(skills))
    monkeypatch.setattr(cli, "_toolkit_declared_tool_names", lambda name: set(tools))


def test_deactivate_routes_to_skill(isolated: Path, monkeypatch):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    _route_as_skill(monkeypatch, skills={"debug_guide"})
    r = CliRunner().invoke(
        cli.main, ["deactivate", "heptapod__debug_guide", "-u"]
    )
    assert r.exit_code == 0, r.output
    entry = _default_profile(isolated)["toolkits"]["heptapod"]
    assert entry["skills"]["disabled"] == ["debug_guide"]
    # It must NOT land in tools.disabled.
    assert "debug_guide" not in ((entry.get("tools") or {}).get("disabled") or [])


def test_activate_clears_disabled_skill(isolated: Path, monkeypatch):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    _route_as_skill(monkeypatch, skills={"debug_guide"})
    CliRunner().invoke(cli.main, ["deactivate", "heptapod__debug_guide", "-u"])
    r = CliRunner().invoke(cli.main, ["activate", "heptapod__debug_guide", "-u"])
    assert r.exit_code == 0, r.output
    entry = _default_profile(isolated)["toolkits"]["heptapod"]
    # The empty skills block is cleaned up.
    assert "skills" not in entry


def test_skill_deactivate_requires_active_toolkit(isolated: Path, monkeypatch):
    _fake_install(isolated, "heptapod")
    _route_as_skill(monkeypatch, skills={"debug_guide"})
    r = CliRunner().invoke(
        cli.main, ["deactivate", "heptapod__debug_guide", "-u"]
    )
    # Toolkit not active → nothing surfaced → no-op with guidance.
    assert r.exit_code == 0, r.output
    assert "not active" in r.output


def test_name_collision_prefers_tool(isolated: Path, monkeypatch):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    # 'foo' is both a tool and a skill → tool wins, skill untouched.
    _route_as_skill(monkeypatch, skills={"foo"}, tools={"foo"})
    r = CliRunner().invoke(cli.main, ["deactivate", "heptapod__foo", "-u"])
    assert r.exit_code == 0, r.output
    assert "both a tool and a skill" in r.output
    entry = _default_profile(isolated)["toolkits"]["heptapod"]
    assert entry["tools"]["disabled"] == ["foo"]
    assert "skills" not in entry


def test_unknown_name_stays_tool(isolated: Path, monkeypatch):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-u"])
    # Name matches neither a skill nor a declared tool → shallow tool path.
    _route_as_skill(monkeypatch, skills=set(), tools=set())
    r = CliRunner().invoke(cli.main, ["deactivate", "heptapod__mystery", "-u"])
    assert r.exit_code == 0, r.output
    entry = _default_profile(isolated)["toolkits"]["heptapod"]
    assert entry["tools"]["disabled"] == ["mystery"]
    assert "skills" not in entry


# ── skill packs (skills-only toolkits) ───────────────────────────────────


def test_skillpack_is_installable_and_activatable(isolated: Path):
    _fake_skillpack_install(isolated, "mypack")
    # It shows up as installed and can be activated like any toolkit.
    r = CliRunner().invoke(cli.main, ["activate", "mypack", "-u"])
    assert r.exit_code == 0, r.output
    assert "mypack" in _default_profile(isolated)["toolkits"]


def test_skillpack_is_discoverable_without_skip(isolated: Path):
    _fake_skillpack_install(isolated, "mypack")
    from toolbase.serve.orchestrator import discover_toolkits, _toolkit_is_skills_only
    disc = {d.name: d for d in discover_toolkits()}
    assert "mypack" in disc
    # Discoverable (so connect can surface its skills) — not skipped here.
    assert disc["mypack"].skip_reason is None
    # But recognized as a skill pack (serve will skip launching it).
    assert _toolkit_is_skills_only(disc["mypack"].path)


def test_skillpack_skills_surface_via_activated_dirs(isolated: Path, tmp_path: Path):
    _fake_skillpack_install(isolated, "mypack")
    CliRunner().invoke(cli.main, ["activate", "mypack", "-u"])
    dirs = cli._activated_toolkit_dirs()
    assert "mypack" in dirs, dirs
    # Surfacing that dir yields the guide (flat/Codex layout here).
    from toolbase import skills as skills_mod
    target = skills_mod.SkillTarget(
        "codex", tmp_path / "prompts", layout="flat", keep_frontmatter=False,
    )
    surfaced = skills_mod.surface_skills("mypack", dirs["mypack"], target)
    assert surfaced == ["mypack__guide"]
    assert (tmp_path / "prompts" / "mypack__guide.md").exists()


def test_tool_toolkit_is_not_flagged_skill_pack(tmp_path: Path):
    """An implicit-form toolkit (tools via tools/__init__.py, empty yaml
    spec) must NOT be mistaken for a skill pack."""
    from toolbase.serve.orchestrator import _toolkit_is_skills_only
    tk = tmp_path / "tk"
    (tk / "tools").mkdir(parents=True)
    (tk / "tools" / "__init__.py").write_text("from .x import x\n")
    (tk / "toolkit.yaml").write_text("name: tk\nversion: 0.1.0\n")  # no tools:
    assert _toolkit_is_skills_only(tk) is False
