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
    assert [s.doc.name for s in found] == ["real.md"]


def test_discover_skills_no_dir(tmp_path: Path):
    assert skills.discover_skills(tmp_path) == []


# ── directory-form skills ───────────────────────────────────────────────────


def _mk_dir_skill(tmp_path: Path, name: str = "deep_dive") -> Path:
    """A dir-form skill with a reference file beside the guide."""
    d = tmp_path / "skills" / name
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: Deep Dive\ndescription: The long version.\n---\n\n"
        "See [the tables](references/tables.csv).\n"
    )
    (d / "references" / "tables.csv").write_text("a,b\n1,2\n")
    return d


def test_discover_finds_both_shapes(tmp_path: Path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "quickstart.md").write_text("---\nname: Q\ndescription: d\n---\n")
    _mk_dir_skill(tmp_path)
    found = skills.discover_skills(tmp_path)
    assert sorted(s.slug for s in found) == ["deep-dive", "quickstart"]
    by_slug = {s.slug: s for s in found}
    assert by_slug["deep-dive"].is_dir is True
    assert by_slug["deep-dive"].doc.name == "SKILL.md"
    assert by_slug["quickstart"].is_dir is False


def test_dir_without_skill_md_is_ignored_and_reported(tmp_path: Path):
    d = tmp_path / "skills" / "notaskill"
    d.mkdir(parents=True)
    (d / "notes.md").write_text("stray")
    assert skills.discover_skills(tmp_path) == []
    assert [p.name for p in skills.skill_dirs_without_doc(tmp_path)] == ["notaskill"]


def test_surface_dir_form_brings_supporting_files(tmp_path: Path):
    _mk_dir_skill(tmp_path)
    target = skills.SkillTarget(
        "claude-code", tmp_path / "out", layout="dir", keep_frontmatter=True,
    )
    assert skills.surface_skills("tk", tmp_path, target) == ["tk__deep-dive"]
    dest = target.root / "tk__deep-dive"
    assert (dest / "SKILL.md").read_text().startswith("---")
    # The reference travels, and at the path the guide's relative link uses.
    assert (dest / "references" / "tables.csv").read_text() == "a,b\n1,2\n"


def test_supporting_files_are_copied_not_linked(tmp_path: Path):
    """Same reason SKILL.md is a copy -- a surfaced skill dir has to be
    plain files a harness scanner will walk. Re-surfacing refreshes them."""
    d = _mk_dir_skill(tmp_path)
    target = skills.SkillTarget(
        "claude-code", tmp_path / "out", layout="dir", keep_frontmatter=True,
    )
    skills.surface_skills("tk", tmp_path, target)
    dest = target.root / "tk__deep-dive" / "references" / "tables.csv"
    assert not dest.is_symlink()

    (d / "references" / "tables.csv").write_text("changed\n")
    skills.surface_skills("tk", tmp_path, target)
    assert dest.read_text() == "changed\n"


def test_resurface_drops_removed_supporting_files(tmp_path: Path):
    d = _mk_dir_skill(tmp_path)
    target = skills.SkillTarget(
        "claude-code", tmp_path / "out", layout="dir", keep_frontmatter=True,
    )
    skills.surface_skills("tk", tmp_path, target)
    import shutil as _shutil
    _shutil.rmtree(d / "references")
    skills.surface_skills("tk", tmp_path, target)
    assert not (target.root / "tk__deep-dive" / "references").exists()
    assert (target.root / "tk__deep-dive" / "SKILL.md").exists()


def test_unsurface_removes_dir_form_without_touching_source(tmp_path: Path):
    d = _mk_dir_skill(tmp_path)
    target = skills.SkillTarget(
        "claude-code", tmp_path / "out", layout="dir", keep_frontmatter=True,
    )
    skills.surface_skills("tk", tmp_path, target)
    assert skills.unsurface_skills("tk", target) == ["tk__deep-dive"]
    assert not (target.root / "tk__deep-dive").exists()
    # The author's toolkit is untouched — no marker written into it, and the
    # supporting files survive.
    assert (d / "SKILL.md").exists()
    assert (d / "references" / "tables.csv").exists()
    assert not (d / skills.OWNED_MARKER).exists()


def test_flat_target_surfaces_the_guide_from_a_dir_skill(tmp_path: Path):
    _mk_dir_skill(tmp_path)
    target = skills.SkillTarget(
        "flat-harness", tmp_path / "prompts", layout="flat",
        keep_frontmatter=False,
    )
    assert skills.surface_skills("tk", tmp_path, target) == ["tk__deep-dive"]
    # Supporting files can't travel into a flat prompt dir; the guide still does.
    text = (target.root / "tk__deep-dive.md").read_text()
    assert "See [the tables]" in text
    assert not (target.root / "references").exists()


def test_dir_form_name_is_the_qualified_slug(tmp_path: Path):
    d = tmp_path / "skills" / "deep_dive"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Just a body, no frontmatter.\n")
    target = skills.SkillTarget(
        "claude-code", tmp_path / "out", layout="dir", keep_frontmatter=True,
    )
    skills.surface_skills("tk", tmp_path, target)
    text = (target.root / "tk__deep-dive" / "SKILL.md").read_text()
    assert "name: tk__deep-dive" in text  # the qualified slug, always


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
    # `name` is the qualified slug, not the author's -- it is what the
    # harness displays and what `tb deactivate` toggles, and the author's
    # was free-form prose that made the namespace invisible.
    assert "name: my-tk__intro" in text
    assert "description: Getting started." in text
    assert "Body." in text
    # Marker is present.
    assert (out / "my-tk__intro" / skills.OWNED_MARKER).exists()


def test_surfaced_skill_md_is_a_real_file_not_a_symlink(tmp_path: Path):
    """This used to symlink the source so an editable checkout's edits
    showed up without re-connecting. Codex's skill scanner does not follow
    a symlinked SKILL.md, so that convenience made the skill invisible
    there -- discovered by every other harness and by nothing that would
    say why. A copy is what every scanner agrees on."""
    tk = _make_toolkit(tmp_path, "my-tk", (
        "intro.md",
        "---\nname: Intro\ndescription: Getting started.\n---\nBody.\n",
    ))
    out = tmp_path / "claude-skills"
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    skill_md = out / "my-tk__intro" / "SKILL.md"
    assert not skill_md.is_symlink()
    assert "Body." in skill_md.read_text()


def test_resurfacing_picks_up_a_source_edit(tmp_path: Path):
    """The cost of copying: an edit lands on the next connect rather than
    immediately. That is the same step every other state change needs."""
    tk = _make_toolkit(tmp_path, "my-tk", (
        "intro.md",
        "---\nname: Intro\ndescription: Getting started.\n---\nBody.\n",
    ))
    out = tmp_path / "claude-skills"
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    (tk / "skills" / "intro.md").write_text(
        "---\nname: Intro\ndescription: Getting started.\n---\nNEW BODY.\n"
    )
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    assert "NEW BODY" in (out / "my-tk__intro" / "SKILL.md").read_text()


def test_install_writes_real_file_when_synthesizing_frontmatter(tmp_path: Path):
    """Synthesis means rewriting; we must not symlink (would mutate source)."""
    tk = _make_toolkit(tmp_path, "my-tk", (
        "no_fm.md",
        "# Heading\n\nFirst line.\n",
    ))
    out = tmp_path / "claude-skills"
    skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    skill_md = out / "my-tk__no-fm" / "SKILL.md"
    assert skill_md.exists()
    assert not skill_md.is_symlink()
    # Source must be unchanged.
    assert (tk / "skills" / "no_fm.md").read_text() == "# Heading\n\nFirst line.\n"


def test_install_synthesizes_a_description_when_missing(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "my-tk", (
        "searching_arxiv.md",
        "# Searching arXiv\n\nThis is the first descriptive line.\n",
    ))
    out = tmp_path / "claude-skills"
    surfaced = skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    assert surfaced == ["my-tk__searching-arxiv"]
    text = (out / "my-tk__searching-arxiv" / "SKILL.md").read_text()
    assert text.startswith("---\n")
    assert "name: my-tk__searching-arxiv" in text
    # A skill with no description is filtered out before it reaches the
    # model, so one is synthesized from the first descriptive line.
    assert "description: This is the first descriptive line." in text
    assert "This is the first descriptive line" in text


def test_install_filename_with_spaces_is_slugged(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "my-tk", (
        "Searching ArXiv.md",
        "ok",
    ))
    out = tmp_path / "claude-skills"
    surfaced = skills.install_skills_for_toolkit("my-tk", tk, skills_dir=out)
    assert surfaced == ["my-tk__searching-arxiv"]


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


# ── bundle scoping ───────────────────────────────────────────────────────────


def test_parse_frontmatter_bundle():
    text = "---\nname: N\ndescription: D\nbundle: symbolic\n---\nbody\n"
    fm, _ = skills.parse_frontmatter(text)
    assert fm is not None
    assert fm.bundle == "symbolic"


def test_parse_frontmatter_no_bundle_defaults_none():
    text = "---\nname: N\ndescription: D\n---\nbody\n"
    fm, _ = skills.parse_frontmatter(text)
    assert fm is not None
    assert fm.bundle is None


def test_install_skips_bundle_skill_when_bundle_unavailable(tmp_path: Path):
    tk = _make_toolkit(
        tmp_path, "tk",
        ("core.md", "---\nname: Core\ndescription: d.\n---\nbody\n"),
        ("pro.md", "---\nname: Pro\ndescription: d.\nbundle: pro\n---\nbody\n"),
    )
    out = tmp_path / "claude"
    surfaced = skills.install_skills_for_toolkit(
        "tk", tk, skills_dir=out, available_bundles={"basic"},
    )
    # Unbundled skill surfaces; the 'pro'-scoped skill is gated out.
    assert surfaced == ["tk__core"]
    assert (out / "tk__core" / "SKILL.md").exists()
    assert not (out / "tk__pro").exists()


def test_install_surfaces_bundle_skill_when_bundle_available(tmp_path: Path):
    tk = _make_toolkit(
        tmp_path, "tk",
        ("pro.md", "---\nname: Pro\ndescription: d.\nbundle: pro\n---\nbody\n"),
    )
    out = tmp_path / "claude"
    surfaced = skills.install_skills_for_toolkit(
        "tk", tk, skills_dir=out, available_bundles={"pro"},
    )
    assert surfaced == ["tk__pro"]


def test_install_no_gating_surfaces_bundle_skill(tmp_path: Path):
    # available_bundles=None (default) disables gating — back-compat.
    tk = _make_toolkit(
        tmp_path, "tk",
        ("pro.md", "---\nname: Pro\ndescription: d.\nbundle: pro\n---\nbody\n"),
    )
    out = tmp_path / "claude"
    surfaced = skills.install_skills_for_toolkit("tk", tk, skills_dir=out)
    assert surfaced == ["tk__pro"]


def test_validate_errors_on_skill_bundle_not_declared(tmp_path: Path):
    tk = _make_minimal_valid_toolkit(tmp_path, "demo-tk")  # no bundles: block
    (tk / "skills" / "pro.md").write_text(
        "---\nname: Pro\ndescription: d.\nbundle: pro\n---\nbody\n"
    )
    result = validation.validate_toolkit(tk)
    assert not result.is_valid
    assert any("pro.md" in e and "pro" in e for e in result.errors)


def test_validate_ok_when_skill_bundle_declared(tmp_path: Path):
    tk = _make_minimal_valid_toolkit(tmp_path, "demo-tk")
    (tk / "toolkit.yaml").write_text(
        "name: demo-tk\n"
        "version: 0.1.0\n"
        "description: A toolkit\n"
        "author: Test\n"
        "category: utils\n"
        "bundles:\n"
        "  pro: {}\n"
        "tools:\n"
        "  - name: example\n"
        "    function: tools.example\n"
        "    description: Example tool.\n"
        "    bundle: pro\n"
    )
    (tk / "skills" / "pro.md").write_text(
        "---\nname: Pro\ndescription: d.\nbundle: pro\n---\nbody\n"
    )
    result = validation.validate_toolkit(tk)
    assert not any(
        "pro.md" in e and "not" in e for e in result.errors
    ), result.errors


# ── skill packs (skills-only toolkits) ───────────────────────────────────


def _make_skillpack(tmp_path: Path, name: str, *, tools_line: str = "") -> Path:
    tk = tmp_path / name
    (tk / "skills").mkdir(parents=True)
    (tk / "skills" / "guide.md").write_text(
        "---\nname: Guide\ndescription: A guide.\n---\nBody\n"
    )
    (tk / "README.md").write_text(f"# {name}\n")
    (tk / "requirements.txt").write_text("")  # no orchestral-ai needed
    (tk / "toolkit.yaml").write_text(
        f"name: {name}\nversion: 0.1.0\ndescription: A skill pack\n"
        f"author: t\ncategory: utils\n" + tools_line
    )
    return tk


def test_validate_skillpack_no_tools_key(tmp_path: Path):
    result = validation.validate_toolkit(_make_skillpack(tmp_path, "skillpack"))
    assert result.is_valid, result.errors
    assert result.metadata.tools == []


def test_validate_skillpack_empty_tools_list(tmp_path: Path):
    result = validation.validate_toolkit(
        _make_skillpack(tmp_path, "skillpack", tools_line="tools: []\n")
    )
    assert result.is_valid, result.errors


def test_validate_skillpack_does_not_require_orchestral_ai(tmp_path: Path):
    result = validation.validate_toolkit(_make_skillpack(tmp_path, "skillpack"))
    assert not any("orchestral-ai" in e for e in result.errors), result.errors


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


# ── flat layout (slash-command prompts, e.g. OpenCode) ──────────────────────


def _flat_target(root: Path) -> "skills.SkillTarget":
    return skills.SkillTarget("flat-harness", root, layout="flat",
                              keep_frontmatter=False)


def _dir_target(root: Path) -> "skills.SkillTarget":
    return skills.SkillTarget("claude-code", root, layout="dir", keep_frontmatter=True)


def test_surface_flat_strips_frontmatter(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", (
        "intro.md",
        "---\nname: Intro\ndescription: d.\n---\n\n# Body\nDo the thing.\n",
    ))
    out = tmp_path / "flat-prompts"
    surfaced = skills.surface_skills("tk", tk, _flat_target(out))
    assert surfaced == ["tk__intro"]
    f = out / "tk__intro.md"
    assert f.exists() and not f.is_dir()
    text = f.read_text()
    # Frontmatter stripped; body is the prompt.
    assert not text.startswith("---")
    assert text.startswith("# Body")
    # Ownership is tracked in the manifest, not a per-dir marker.
    assert skills._read_manifest(out) == {"tk__intro.md": "tk"}


def test_surface_flat_keeps_body_when_no_frontmatter(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("raw.md", "# Raw\nJust text.\n"))
    out = tmp_path / "flat-prompts"
    skills.surface_skills("tk", tk, _flat_target(out))
    assert (out / "tk__raw.md").read_text().startswith("# Raw")


def test_unsurface_flat_removes_only_manifest_files(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("a.md", "body a"))
    out = tmp_path / "flat-prompts"
    skills.surface_skills("tk", tk, _flat_target(out))
    # A user-authored prompt with the same prefix must survive.
    (out / "tk__mine.md").write_text("mine")
    removed = skills.unsurface_skills("tk", _flat_target(out))
    assert removed == ["tk__a"]
    assert not (out / "tk__a.md").exists()
    assert (out / "tk__mine.md").exists()  # not in manifest → untouched
    assert skills._read_manifest(out) == {}


def test_unsurface_flat_only_targets_named_toolkit(tmp_path: Path):
    out = tmp_path / "flat-prompts"
    skills.surface_skills("tk", _make_toolkit(tmp_path, "tk", ("a.md", "a")),
                          _flat_target(out))
    skills.surface_skills("other", _make_toolkit(tmp_path, "other", ("b.md", "b")),
                          _flat_target(out))
    skills.unsurface_skills("tk", _flat_target(out))
    assert not (out / "tk__a.md").exists()
    assert (out / "other__b.md").exists()
    assert skills._read_manifest(out) == {"other__b.md": "other"}


def test_unsurface_all_flat_clears_manifest(tmp_path: Path):
    out = tmp_path / "flat-prompts"
    skills.surface_skills("tk", _make_toolkit(tmp_path, "tk", ("a.md", "a")),
                          _flat_target(out))
    removed = skills.unsurface_all(_flat_target(out))
    assert removed == ["tk__a"]
    assert skills._read_manifest(out) == {}


def test_unsurface_all_dir_removes_all_owned(tmp_path: Path):
    out = tmp_path / "claude"
    skills.surface_skills(
        "tk", _make_toolkit(tmp_path, "tk", ("a.md", "a"), ("b.md", "b")),
        _dir_target(out),
    )
    # A user dir without a marker must survive.
    (out / "tk__user").mkdir()
    removed = skills.unsurface_all(_dir_target(out))
    assert sorted(removed) == ["tk__a", "tk__b"]
    assert (out / "tk__user").exists()


def test_surface_respects_disabled_slugs(tmp_path: Path):
    tk = _make_toolkit(
        tmp_path, "tk",
        ("keep.md", "keep body"),
        ("drop.md", "drop body"),
    )
    out = tmp_path / "flat-prompts"
    surfaced = skills.surface_skills(
        "tk", tk, _flat_target(out), disabled_slugs={"drop"},
    )
    assert surfaced == ["tk__keep"]
    assert (out / "tk__keep.md").exists()
    assert not (out / "tk__drop.md").exists()


def test_surface_disabled_slugs_dir_layout(tmp_path: Path):
    tk = _make_toolkit(
        tmp_path, "tk", ("keep.md", "a"), ("drop.md", "b"),
    )
    out = tmp_path / "claude"
    surfaced = skills.surface_skills(
        "tk", tk, _dir_target(out), disabled_slugs={"drop"},
    )
    assert surfaced == ["tk__keep"]
    assert not (out / "tk__drop").exists()


def test_flat_bundle_gating(tmp_path: Path):
    tk = _make_toolkit(
        tmp_path, "tk",
        ("core.md", "core body"),
        ("pro.md", "---\nname: Pro\ndescription: d.\nbundle: pro\n---\nbody\n"),
    )
    out = tmp_path / "flat-prompts"
    surfaced = skills.surface_skills(
        "tk", tk, _flat_target(out), available_bundles={"basic"},
    )
    assert surfaced == ["tk__core"]
    assert not (out / "tk__pro.md").exists()


# ── prune: a connect is a sync, not an append ───────────────────────────────
#
# Surfacing only ever adds. Everything that stops a skill from being
# surfaced -- its toolkit leaving the loadout, a `tb deactivate`, a bundle
# gate closing, the author deleting the guide -- left the last copy in
# place, still read by the harness and contradicting every read command.


def test_prune_removes_an_owned_entry_not_in_keep(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("keep.md", "a"), ("drop.md", "b"))
    out = tmp_path / "claude"
    target = _dir_target(out)
    skills.surface_skills("tk", tk, target)

    removed = skills.prune_skills(target, keep={"tk__keep"})
    assert removed == ["tk__drop"]
    assert (out / "tk__keep").exists()
    assert not (out / "tk__drop").exists()


def test_prune_leaves_entries_it_does_not_own(tmp_path: Path):
    """A user's own skill dir has no OWNED_MARKER; pruning is not a licence
    to clear the harness's skills directory."""
    out = tmp_path / "claude"
    (out / "mine").mkdir(parents=True)
    (out / "mine" / "SKILL.md").write_text("hand-written\n")

    assert skills.prune_skills(_dir_target(out), keep=set()) == []
    assert (out / "mine" / "SKILL.md").exists()


def test_prune_skips_owners_that_failed_to_surface(tmp_path: Path):
    """Surfacing is best-effort per toolkit. One that raised contributed no
    slugs to ``keep``, so pruning it would delete working skills because of
    an unrelated error."""
    a = _make_toolkit(tmp_path / "a", "tk-a", ("guide.md", "a"))
    b = _make_toolkit(tmp_path / "b", "tk-b", ("guide.md", "b"))
    out = tmp_path / "claude"
    target = _dir_target(out)
    skills.surface_skills("tk-a", a, target)
    skills.surface_skills("tk-b", b, target)

    removed = skills.prune_skills(
        target, keep={"tk-a__guide"}, skip_owners={"tk-b"},
    )
    assert removed == []
    assert (out / "tk-b__guide").exists()


def test_prune_falls_back_to_the_name_prefix_for_ownership(tmp_path: Path):
    """An unreadable marker must not cost a toolkit the protection
    ``skip_owners`` is giving it."""
    tk = _make_toolkit(tmp_path, "tk", ("guide.md", "a"))
    out = tmp_path / "claude"
    target = _dir_target(out)
    skills.surface_skills("tk", tk, target)
    (out / "tk__guide" / skills.OWNED_MARKER).write_text("")

    assert skills.prune_skills(target, keep=set(), skip_owners={"tk"}) == []
    assert (out / "tk__guide").exists()


def test_prune_is_a_no_op_when_everything_is_kept(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("guide.md", "a"))
    out = tmp_path / "claude"
    target = _dir_target(out)
    surfaced = skills.surface_skills("tk", tk, target)

    assert skills.prune_skills(target, keep=set(surfaced)) == []
    assert (out / "tk__guide").exists()


def test_prune_on_a_missing_root_is_a_no_op(tmp_path: Path):
    assert skills.prune_skills(_dir_target(tmp_path / "nope"), keep=set()) == []


def test_prune_flat_removes_the_file_and_its_manifest_entry(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("keep.md", "a"), ("drop.md", "b"))
    out = tmp_path / "flat-prompts"
    target = _flat_target(out)
    skills.surface_skills("tk", tk, target)

    removed = skills.prune_skills(target, keep={"tk__keep"})
    assert removed == ["tk__drop"]
    assert not (out / "tk__drop.md").exists()
    # Left in the manifest it would never be reaped again.
    assert "tk__drop.md" not in (out / skills.MANIFEST_NAME).read_text()
    assert (out / "tk__keep.md").exists()


def test_prune_flat_leaves_a_file_that_is_not_in_the_manifest(tmp_path: Path):
    """The manifest is the only ownership record a flat file has, so a
    same-prefix file we never wrote is not ours to delete."""
    out = tmp_path / "flat-prompts"
    out.mkdir()
    (out / "tk__mine.md").write_text("hand-written\n")

    assert skills.prune_skills(_flat_target(out), keep=set()) == []
    assert (out / "tk__mine.md").exists()


def test_prune_flat_skips_protected_owners(tmp_path: Path):
    a = _make_toolkit(tmp_path / "a", "tk-a", ("guide.md", "a"))
    b = _make_toolkit(tmp_path / "b", "tk-b", ("guide.md", "b"))
    out = tmp_path / "flat-prompts"
    target = _flat_target(out)
    skills.surface_skills("tk-a", a, target)
    skills.surface_skills("tk-b", b, target)

    assert skills.prune_skills(target, keep=set(), skip_owners={"tk-b"}) == [
        "tk-a__guide"]
    assert (out / "tk-b__guide.md").exists()


# ── owned_slugs ─────────────────────────────────────────────────────────────


def test_owned_slugs_reports_what_a_dir_surface_holds(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("a.md", "x"), ("b.md", "y"))
    out = tmp_path / "claude"
    target = _dir_target(out)
    skills.surface_skills("tk", tk, target)
    (out / "theirs").mkdir()  # not ours

    assert skills.owned_slugs(target) == ["tk__a", "tk__b"]


def test_owned_slugs_reports_what_a_flat_surface_holds(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("a.md", "x"))
    out = tmp_path / "flat-prompts"
    target = _flat_target(out)
    skills.surface_skills("tk", tk, target)
    (out / "theirs.md").write_text("not ours\n")

    assert skills.owned_slugs(target) == ["tk__a"]


def test_owned_slugs_on_a_missing_root_is_empty(tmp_path: Path):
    assert skills.owned_slugs(_dir_target(tmp_path / "nope")) == []


# ── the surfaced skill's identity is toolbase's, not the author's ───────────
#
# The `<toolkit>__` namespace used to exist only as a directory name.
# Harnesses display the frontmatter `name`, so a guide whose author wrote
# prose there showed up as that prose -- the namespace invisible, two
# toolkits shipping a `mg5` guide indistinguishable, and the name on screen
# not the name `tb deactivate` accepts.


def test_prose_name_is_replaced_by_the_slug(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "heptapod", (
        "pythia_cards.md",
        "---\nname: Writing Pythia 8 run cards for forward production\n"
        "description: Process selection and normalisation.\n---\n\nBody.\n",
    ))
    target = _dir_target(tmp_path / "out")
    skills.surface_skills("heptapod", tk, target)
    text = (target.root / "heptapod__pythia-cards" / "SKILL.md").read_text()
    assert "name: heptapod__pythia-cards" in text
    assert "Writing Pythia 8 run cards" not in text.split("---")[1]


def test_the_authors_description_is_never_rewritten(tmp_path: Path):
    """It is the trigger text the model reads to decide when a skill
    applies -- the one field we have no business touching."""
    desc = "Use when the user mentions Pythia, forward production, or LLPs."
    tk = _make_toolkit(tmp_path, "tk", (
        "guide.md", f"---\nname: whatever\ndescription: {desc}\n---\n\nBody.\n",
    ))
    target = _dir_target(tmp_path / "out")
    skills.surface_skills("tk", tk, target)
    text = (target.root / "tk__guide" / "SKILL.md").read_text()
    assert f"description: {desc}" in text


def test_the_name_matches_the_directory_it_is_written_into(tmp_path: Path):
    """Every harness documents the same convention, and it is what makes
    `tb deactivate <toolkit>__<skill>` name the thing you see."""
    tk = _make_toolkit(tmp_path, "tk", (
        "guide.md", "---\nname: Something Else\ndescription: d.\n---\n\nB.\n",
    ))
    target = _dir_target(tmp_path / "out")
    surfaced = skills.surface_skills("tk", tk, target)
    for slug in surfaced:
        fm, _body = skills.parse_frontmatter(
            (target.root / slug / "SKILL.md").read_text())
        assert fm.name == slug == (target.root / slug).name


def test_toolbase_internal_keys_are_dropped(tmp_path: Path):
    """`bundle:` is a curation input, not part of the guide; a harness that
    validates its frontmatter has no reason to accept it."""
    tk = _make_toolkit(tmp_path, "tk", (
        "guide.md",
        "---\nname: g\ndescription: d.\nbundle: llp\n---\n\nBody.\n",
    ))
    target = _dir_target(tmp_path / "out")
    skills.surface_skills("tk", tk, target, available_bundles={"llp"})
    text = (target.root / "tk__guide" / "SKILL.md").read_text()
    assert "bundle:" not in text


def test_other_author_keys_survive(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", (
        "guide.md",
        "---\nname: g\ndescription: d.\nlicense: MIT\n"
        "metadata:\n  short-description: hi\n---\n\nBody.\n",
    ))
    target = _dir_target(tmp_path / "out")
    skills.surface_skills("tk", tk, target)
    fm, _ = skills.parse_frontmatter(
        (target.root / "tk__guide" / "SKILL.md").read_text())
    assert fm.raw["license"] == "MIT"
    assert fm.raw["metadata"] == {"short-description": "hi"}


def test_the_body_is_not_duplicated_or_lost(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", (
        "guide.md", "---\nname: g\ndescription: d.\n---\n\n# Title\n\nBody.\n",
    ))
    target = _dir_target(tmp_path / "out")
    skills.surface_skills("tk", tk, target)
    text = (target.root / "tk__guide" / "SKILL.md").read_text()
    assert text.count("# Title") == 1
    assert text.count("Body.") == 1
    assert text.count("---") == 2  # one fence pair, no leftover second block


def test_a_body_with_no_frontmatter_survives_whole(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("guide.md", "# Title\n\nBody line.\n"))
    target = _dir_target(tmp_path / "out")
    skills.surface_skills("tk", tk, target)
    text = (target.root / "tk__guide" / "SKILL.md").read_text()
    assert "# Title" in text and "Body line." in text
    assert text.count("# Title") == 1


def test_a_long_description_stays_on_one_line(tmp_path: Path):
    """YAML's default 80-column wrap folds a long description across lines.
    It parses, but it reads as a broken file and only a real parser puts it
    back together."""
    desc = ("Use when the user mentions Pythia, forward production, beam "
            "dumps, far detectors, minimum bias, or any small-angle yield "
            "estimate that depends on process selection and normalisation.")
    tk = _make_toolkit(tmp_path, "tk", (
        "guide.md", f"---\nname: g\ndescription: {desc}\n---\n\nBody.\n",
    ))
    target = _dir_target(tmp_path / "out")
    skills.surface_skills("tk", tk, target)
    text = (target.root / "tk__guide" / "SKILL.md").read_text()
    assert f"description: {desc}\n" in text
    fm, _ = skills.parse_frontmatter(text)
    assert fm.description == desc


# ── slugs are lowercase-dash, and old spellings still match ────────────────
#
# The slug is what every harness *displays*, so it should look like every
# other skill in the ecosystem (Codex ships `openai-docs`, Claude Code
# `code-review`, OpenCode documents "lowercase hyphen-separated"). toolbase
# kept underscores, which made it the odd one out.


@pytest.mark.parametrize("stem, expected", [
    ("pythia_forward_run_cards", "pythia-forward-run-cards"),
    ("pythia-forward-run-cards", "pythia-forward-run-cards"),
    ("Searching arXiv", "searching-arxiv"),
    ("getting  started", "getting-started"),
    ("__leading_and_trailing__", "leading-and-trailing"),
    ("mixed_-_separators", "mixed-separators"),
    ("MG5", "mg5"),
    ("", "skill"),
    ("!!!", "skill"),
])
def test_normalize_slug(stem, expected):
    assert skills.normalize_slug(stem) == expected


def test_normalize_slug_is_idempotent():
    """What lets it double as the comparison key for an older spelling."""
    for stem in ("a_b", "a-b", "A B", "Searching arXiv"):
        once = skills.normalize_slug(stem)
        assert skills.normalize_slug(once) == once


def test_case_is_not_split_on():
    """`PythiaCards` is one word here because nothing distinguishes it from
    `ArXiv`, where splitting would be wrong."""
    assert skills.normalize_slug("PythiaCards") == "pythiacards"
    assert skills.normalize_slug("ArXiv") == "arxiv"


def test_an_underscored_source_surfaces_with_dashes(tmp_path: Path):
    tk = _make_toolkit(tmp_path, "tk", ("run_cards.md", "---\nname: g\ndescription: d.\n---\n\nB.\n"))
    target = _dir_target(tmp_path / "out")
    assert skills.surface_skills("tk", tk, target) == ["tk__run-cards"]


def test_slugs_match_across_spellings():
    assert skills.slugs_match("my_guide", "my-guide")
    assert skills.slugs_match("My Guide", "my-guide")
    assert not skills.slugs_match("my-guide", "other-guide")


def test_a_deactivation_written_before_hyphenation_still_matches(tmp_path: Path):
    """A loadout recorded `run_cards`; the skill is now `run-cards`. A
    literal comparison would silently stop matching and put a deactivated
    guide back in front of the agent -- the exact failure the prune ended."""
    tk = _make_toolkit(tmp_path, "tk", ("run_cards.md", "---\nname: g\ndescription: d.\n---\n\nB.\n"))
    target = _dir_target(tmp_path / "out")
    surfaced = skills.surface_skills(
        "tk", tk, target, disabled_slugs={"run_cards"},
    )
    assert surfaced == []
    assert not (target.root / "tk__run-cards").exists()
