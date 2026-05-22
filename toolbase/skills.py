"""Skills surfacing.

A toolkit's ``skills/*.md`` files are agent-facing how-to guides. On
install, we mirror them into ``~/.claude/skills/`` so Claude Code (which
watches that directory) picks them up automatically. On uninstall, we
remove them. On publish, we validate that they carry the frontmatter
Claude Code expects.

The mirror layout is:

    ~/.claude/skills/<toolkit>__<skill_name>/SKILL.md

Each skill gets its own directory. Namespacing the directory name with
``<toolkit>__`` mirrors the tool-namespacing convention and prevents
collisions with skills from other toolkits or unrelated installations.

This module deliberately uses *copies*, not symlinks, even though the
original spec said "symlink." Copies survive ``rm -rf`` of the toolkit
dir cleaner (orphan symlinks are confusing); they survive Windows; and
the source files are tiny markdown so the disk cost is negligible. We
still call the operation "surfacing" to match user-facing terminology.

If the user has manually placed something in
``~/.claude/skills/<toolkit>__<skill>/`` we won't blow it away on
install — we only overwrite ``SKILL.md`` itself.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import yaml


def _can_symlink() -> bool:
    """Symlinks are reliable on POSIX. On Windows they require admin or
    Developer Mode and we'd rather copy than fail mid-install. The
    install code path always uses copy-on-failure as a safety net too.
    """
    return os.name == "posix"


CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
# Marker file we drop into each surfaced skill dir so we know we own it
# and can remove it cleanly on uninstall without touching skills the user
# placed there themselves.
OWNED_MARKER = ".toolbase-managed"


@dataclass
class SkillFrontmatter:
    """Parsed frontmatter from a SKILL.md / skill markdown file."""

    name: Optional[str]
    description: Optional[str]
    raw: dict

    def is_complete(self) -> bool:
        return bool(self.name) and bool(self.description)


def parse_frontmatter(text: str) -> Tuple[Optional[SkillFrontmatter], str]:
    """Return (frontmatter, body). frontmatter is None if absent.

    Frontmatter is the standard YAML block delimited by ``---`` on its own
    line at the top of the file. Anything else is treated as body.
    """
    if not text.startswith("---"):
        return None, text
    # Find the closing fence on its own line.
    lines = text.split("\n")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, text
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])
    try:
        raw = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        # Malformed YAML in fence → treat as no frontmatter; the publish
        # validator will flag it via a separate warning.
        return None, text
    if not isinstance(raw, dict):
        return None, text
    return SkillFrontmatter(
        name=raw.get("name") if isinstance(raw.get("name"), str) else None,
        description=raw.get("description") if isinstance(raw.get("description"), str) else None,
        raw=raw,
    ), body


def discover_skills(toolkit_dir: Path) -> List[Path]:
    """Return the list of skill markdown files in a toolkit directory.

    Filters out macOS AppleDouble companions ("._foo.md").
    """
    skills_dir = toolkit_dir / "skills"
    if not skills_dir.exists():
        return []
    return sorted(
        p for p in skills_dir.glob("*.md")
        if not p.name.startswith("._")
    )


def _slug(stem: str) -> str:
    """Normalize a skill stem to a filesystem-safe slug.

    Skill files are named freely by authors (``getting_started.md``,
    ``Searching arXiv.md`` etc.). We keep alphanumerics, underscores, and
    hyphens; everything else collapses to underscore.
    """
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return s.lower() or "skill"


def install_skills_for_toolkit(
    toolkit_name: str,
    toolkit_dir: Path,
    *,
    skills_dir: Optional[Path] = None,
) -> List[str]:
    """Copy a toolkit's skills into ``~/.claude/skills/``.

    Returns the list of skill slugs that were surfaced (empty if the
    toolkit ships none). Idempotent: safe to call repeatedly; existing
    SKILL.md files we own will be overwritten.

    ``skills_dir`` defaults to the module-level ``CLAUDE_SKILLS_DIR`` at
    call time. We use a ``None`` sentinel rather than
    ``skills_dir: Path = CLAUDE_SKILLS_DIR`` because Python evaluates
    default-argument expressions exactly once, at function definition.
    A bound default would freeze the import-time value of
    ``CLAUDE_SKILLS_DIR``, defeating monkeypatching in tests and the
    e2e harness — which silently leaked synthetic skill files into the
    developer's real ``~/.claude/skills/`` until that bug was caught.
    Resolve at call time to keep tests honest.
    """
    if skills_dir is None:
        skills_dir = CLAUDE_SKILLS_DIR
    sources = discover_skills(toolkit_dir)
    if not sources:
        return []

    skills_dir.mkdir(parents=True, exist_ok=True)
    surfaced: List[str] = []
    use_symlinks = _can_symlink()
    for src in sources:
        slug = f"{toolkit_name}__{_slug(src.stem)}"
        dest_dir = skills_dir / slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Drop the marker so we know we own this directory.
        (dest_dir / OWNED_MARKER).write_text(toolkit_name + "\n")

        text = src.read_text(encoding="utf-8")
        fm, _body = parse_frontmatter(text)
        needs_synthesis = fm is None or not fm.is_complete()

        skill_path = dest_dir / "SKILL.md"
        # If a previous install left a file/symlink here, replace it.
        if skill_path.exists() or skill_path.is_symlink():
            skill_path.unlink()

        if needs_synthesis:
            # Synthesize frontmatter on disk because we can't safely
            # mutate the source file. Using the file stem as `name` and
            # the first descriptive line as `description` is a backward-
            # compat fallback for toolkits that predate the requirement.
            synthesized_name = src.stem.replace("_", " ").replace("-", " ").strip().title()
            description = _first_line_summary(text) or f"Guidance for {toolkit_name}."
            text = (
                "---\n"
                f"name: {synthesized_name}\n"
                f"description: {description}\n"
                "---\n\n"
                + text
            )
            skill_path.write_text(text, encoding="utf-8")
        elif use_symlinks:
            # Symlink to the source file. Edits to the source show up on
            # the next time Claude Code re-reads, which matters for
            # editable / dev installs where the toolkit dir is the
            # author's working copy.
            try:
                skill_path.symlink_to(src.resolve())
            except OSError:
                # Fall back to a copy (filesystem doesn't support symlinks,
                # e.g. some FUSE mounts).
                skill_path.write_text(text, encoding="utf-8")
        else:
            # Windows or non-symlinkable filesystem: write a copy.
            skill_path.write_text(text, encoding="utf-8")
        surfaced.append(slug)
    return surfaced


def uninstall_skills_for_toolkit(
    toolkit_name: str,
    *,
    skills_dir: Optional[Path] = None,
) -> List[str]:
    """Remove ``~/.claude/skills/<toolkit>__*`` directories we own.

    Only removes directories that carry the ``OWNED_MARKER`` written by
    ``install_skills_for_toolkit``. User-placed skills with the same
    name prefix are left alone.

    Returns the list of skill slugs removed.
    """
    if skills_dir is None:
        skills_dir = CLAUDE_SKILLS_DIR
    if not skills_dir.exists():
        return []
    prefix = f"{toolkit_name}__"
    removed: List[str] = []
    for entry in skills_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        marker = entry / OWNED_MARKER
        if not marker.exists():
            continue
        try:
            shutil.rmtree(entry)
            removed.append(entry.name)
        except OSError:
            # Leave it; user can clean up manually. Don't fail uninstall.
            pass
    return removed


def _first_line_summary(text: str, *, max_len: int = 120) -> Optional[str]:
    """Pick the first non-empty line that isn't a heading or YAML fence.

    Used to synthesize a description for a skill that has no frontmatter.
    Heading lines (``# ...``) are skipped because they're the title, not
    a description.
    """
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("---") or s.startswith("#"):
            continue
        if len(s) > max_len:
            s = s[: max_len - 1].rstrip() + "…"
        return s
    return None
