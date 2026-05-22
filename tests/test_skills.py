"""Tests for the skills surfacing module.

Covers:
- ``parse_frontmatter`` on present, missing, and malformed inputs
- ``discover_skills`` filters AppleDouble files
- ``install_skills_for_toolkit`` writes SKILL.md per skill, namespaced
- ``install_skills_for_toolkit`` synthesizes frontmatter for skills
  without it (backward compat)
- ``install_skills_for_toolkit`` is idempotent
- ``uninstall_skills_for_toolkit`` removes only managed dirs
- the validation helper warns on missing/incomplete frontmatter
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase import skills, validation


# ── parse_frontmatter ───────────────────────────────────────────────────────


def test_parse_frontmatter_present():
    text = (
        "---\n"
        "name: Searching arXiv\n"
        "description: How to use the search tool.\n"
        "---\n\n"
        "# body\n"
    )
    fm, body = skills.parse_frontmatter(text)
    assert fm is not None
    assert fm.name == "Searching arXiv"
    assert fm.description == "How to use the search tool."
    assert "body" in body
    assert fm.is_complete()


def test_parse_frontmatter_missing():
    fm, body = skills.parse_frontmatter("# Just a heading\n\nText.")
    assert fm is None
    assert body == "# Just a heading\n\nText."


def test_parse_frontmatter_malformed_yaml():
    fm, body = skills.parse_frontmatter("---\nname: : :\n---\nbody")
    # Malformed YAML is treated as no frontmatter.
    assert fm is None


def test_parse_frontmatter_unclosed_fence():
    fm, body = skills.parse_frontmatter("---\nname: foo\nbody never closes")
    assert fm is None


def test_parse_frontmatter_partial_fields():
    text = "---\nname: foo\n---\nbody"
    fm, _ = skills.parse_frontmatter(text)
    assert fm is not None
    assert fm.name == "foo"
    assert fm.description is None
    assert not fm.is_complete()


# ── discover_skills ─────────────────────────────────────────────────────────


def test_discover_skills_filters_appledouble(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "real.md").write_text("ok")
    (skills_dir / "._real.md").write_text("appledouble")
    found = skills.discover_skills(tmp_path)
    assert [p.name for p in found] == ["real.md"]


def test_discover_skills_no_dir(tmp_path: Path):
    assert skills.discover_skills(tmp_path) == []


# ── install_skills_for_toolkit ──────────────────────────────────────────────


def _make_toolkit(tmp_path: Path, name: str, *files: tuple) -> Path:
    """Helper: build a fake toolkit dir with skills/."""
    tk = tmp_path / name
    (tk / "skills").mkdir(parents=True)
    for fname, content in files:
        (tk / "skills" / fname).write_text(content, encoding="utf-8")
    return tk


def test_install_writes_skill_md(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "my-tk", (
        "intro.md",
        "---\nname: Intro\ndescription: Getting started.\n---\nBody.\n",
    ))
    out = tmp_path / "claude-skills"
    surfaced = skills.install_skills_for_toolkit(
        "my-tk", tk, skills_dir=out,
    )
    assert surfaced == ["my-tk__intro"]
    skill_md = out / "my-tk__intro" / "SKILL.md"
    assert skill_md.exists()
    text = skill_md.read_text()
    assert "name: Intro" in text
    assert "Body." in text
    # Marker is present.
    assert (out / "my-tk__intro" / skills.OWNED_MARKER).exists()


def test_install_uses_symlink_on_posix_for_complete_frontmatter(tmp_path: Path):
    """When frontmatter is complete and we're on POSIX, prefer symlinks
    so author edits propagate without reinstalling."""
    if not skills._can_symlink():
        pytest.skip("symlinks not used on this platform")
    tk = _make_toolkit(tmp_path, "my-tk", (
        "intro.md",
        "---\nname: Intro\ndescription: Getting started.\n---\nBody.\n",
    ))
    out = tmp_path / "claude-skills"
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    skill_md = out / "my-tk__intro" / "SKILL.md"
    assert skill_md.is_symlink()
    # Edit the source — the surfaced file should reflect it without reinstall.
    (tk / "skills" / "intro.md").write_text(
        "---\nname: Intro\ndescription: Getting started.\n---\nNEW BODY.\n"
    )
    assert "NEW BODY" in skill_md.read_text()


def test_install_writes_real_file_when_synthesizing_frontmatter(tmp_path: Path):
    """Synthesis means rewriting; we must not symlink (would mutate source)."""
    tk = _make_toolkit(tmp_path, "my-tk", (
        "no_fm.md",
        "# Heading\n\nFirst line.\n",
    ))
    out = tmp_path / "claude-skills"
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    skill_md = out / "my-tk__no_fm" / "SKILL.md"
    assert skill_md.exists()
    assert not skill_md.is_symlink()
    # Source must be unchanged.
    assert (tk / "skills" / "no_fm.md").read_text() == "# Heading\n\nFirst line.\n"


def test_install_synthesizes_frontmatter_when_missing(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "my-tk", (
        "searching_arxiv.md",
        "# Searching arXiv\n\nThis is the first descriptive line.\n",
    ))
    out = tmp_path / "claude-skills"
    surfaced = skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    assert surfaced == ["my-tk__searching_arxiv"]
    text = (out / "my-tk__searching_arxiv" / "SKILL.md").read_text()
    assert text.startswith("---\n")
    assert "name: Searching Arxiv" in text
    assert "This is the first descriptive line" in text


def test_install_filename_with_spaces_is_slugged(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "my-tk", (
        "Searching ArXiv.md",
        "ok",
    ))
    out = tmp_path / "claude-skills"
    surfaced = skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    assert surfaced == ["my-tk__searching_arxiv"]


def test_install_is_idempotent(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "my-tk", ("x.md", "ok"))
    out = tmp_path / "claude-skills"
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    # Still exactly one entry, still owned.
    assert (out / "my-tk__x" / "SKILL.md").exists()
    assert (out / "my-tk__x" / skills.OWNED_MARKER).exists()


def test_install_returns_empty_list_when_no_skills(tmp_path: Path):
    tk = tmp_path / "my-tk"
    tk.mkdir()
    surfaced = skills.install_skills_for_toolkit(
        "my-tk", tk, skills_dir=tmp_path / "claude",
    )
    assert surfaced == []


# ── uninstall_skills_for_toolkit ────────────────────────────────────────────


def test_uninstall_removes_only_managed_dirs(tmp_path: Path):
    out = tmp_path / "claude-skills"
    out.mkdir()
    # Two managed dirs for our toolkit
    (out / "tk__a").mkdir()
    (out / "tk__a" / skills.OWNED_MARKER).write_text("tk")
    (out / "tk__a" / "SKILL.md").write_text("x")
    (out / "tk__b").mkdir()
    (out / "tk__b" / skills.OWNED_MARKER).write_text("tk")
    # One unmanaged dir with the same prefix (user-placed, no marker)
    (out / "tk__user").mkdir()
    (out / "tk__user" / "SKILL.md").write_text("user-skill")
    # And one totally unrelated dir
    (out / "other-toolkit__something").mkdir()
    (out / "other-toolkit__something" / skills.OWNED_MARKER).write_text("other")

    removed = skills.uninstall_skills_for_toolkit("tk", skills_dir=out)
    assert sorted(removed) == ["tk__a", "tk__b"]
    assert not (out / "tk__a").exists()
    assert not (out / "tk__b").exists()
    # User-placed and unrelated survive.
    assert (out / "tk__user").exists()
    assert (out / "other-toolkit__something").exists()


def test_uninstall_no_skills_dir_returns_empty(tmp_path: Path):
    assert skills.uninstall_skills_for_toolkit(
        "tk", skills_dir=tmp_path / "nonexistent",
    ) == []


# ── validation: skill frontmatter warnings ──────────────────────────────────


def test_validate_warns_on_missing_skill_frontmatter(tmp_path: Path):
    tk = _make_minimal_valid_toolkit(tmp_path, "my-tk")
    (tk / "skills" / "no_fm.md").write_text("# Just text, no frontmatter\n")
    result = validation.validate_toolkit(tk)
    assert result.is_valid  # warning only, not error
    assert any("no_fm.md" in w and "frontmatter" in w for w in result.warnings)


def test_validate_warns_on_incomplete_frontmatter(tmp_path: Path):
    tk = _make_minimal_valid_toolkit(tmp_path, "my-tk")
    (tk / "skills" / "partial.md").write_text(
        "---\nname: only-name\n---\nbody\n"
    )
    result = validation.validate_toolkit(tk)
    assert result.is_valid
    assert any("partial.md" in w and "description" in w for w in result.warnings)


def test_validate_no_warning_for_complete_frontmatter(tmp_path: Path):
    tk = _make_minimal_valid_toolkit(tmp_path, "my-tk")
    (tk / "skills" / "good.md").write_text(
        "---\nname: Good\ndescription: A complete skill.\n---\nbody\n"
    )
    result = validation.validate_toolkit(tk)
    assert not any("good.md" in w and "frontmatter" in w for w in result.warnings)


def _make_minimal_valid_toolkit(tmp_path: Path, name: str) -> Path:
    """Build a toolkit dir that satisfies the rest of validate_toolkit
    so we can isolate the skills-frontmatter warnings.
    """
    tk = tmp_path / name
    tk.mkdir()
    (tk / "toolkit.yaml").write_text(
        f"name: {name}\n"
        f"version: 0.1.0\n"
        f"description: A toolkit\n"
        f"author: Test\n"
        f"category: utils\n"
        f"tools:\n"
        f"  - name: example\n"
        f"    function: tools.example\n"
        f"    description: Example tool.\n"
    )
    (tk / "tools").mkdir()
    (tk / "tools" / "__init__.py").write_text(
        "from .example import example\n"
    )
    (tk / "tools" / "example.py").write_text(
        "def example():\n    return '{}'\n"
    )
    (tk / "mcp").mkdir()
    (tk / "mcp" / "__init__.py").write_text("")
    (tk / "mcp" / "server_stdio.py").write_text("")
    (tk / "requirements.txt").write_text("orchestral-ai>=1.0.0\n")
    (tk / "skills").mkdir()
    return tk
