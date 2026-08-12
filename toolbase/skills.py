"""Skills surfacing.

A toolkit's ``skills/`` entries are agent-facing how-to guides, shipped either
as a flat ``skills/<name>.md`` or as ``skills/<name>/SKILL.md`` — a directory
that can carry ``references/``, ``scripts/`` and assets beside the guide (see
:class:`SkillSource`). Surfacing
is a per-harness, per-scope ``tb connect`` concern (each harness has its own
skill location and format, at a user and a project root), driven through a
:class:`SkillTarget` an adapter returns for the scope being wired.
``tb connect <harness>`` surfaces the activated toolkits' skills into that
target; ``tb disconnect`` (and uninstalling the toolkit) clears them. On
publish we validate that skills carry the frontmatter Claude Code expects.

Every supported harness now has a real skill concept, and they agree on the
layout — a directory per skill holding ``SKILL.md``:

    Claude Code   ~/.claude/skills/<toolkit>__<skill>/SKILL.md
    Antigravity   ~/.gemini/config/skills/<toolkit>__<skill>/SKILL.md
    Codex         $CODEX_HOME/skills/<toolkit>__<skill>/SKILL.md
    OpenCode      ~/.config/opencode/skills/<toolkit>__<skill>/SKILL.md

Each scans its root and loads a guide on demand; frontmatter is required —
the description is what decides when a skill applies — so we preserve it,
synthesizing when missing. The ``<toolkit>__`` namespace mirrors the
tool-namespacing convention and prevents collisions.

The ``flat`` layout is what we used to approximate skills with before those
concepts existed (Codex prompts, OpenCode commands): one markdown file per
skill, a user-invoked ``/<name>`` slash command the model never sees. No
adapter surfaces into it now; it survives because those directories still
hold files an older toolbase wrote, and ``legacy_skill_targets`` has to be
able to clear them.

Ownership so we never clobber a user's own file: the dir layout drops an
``OWNED_MARKER`` in each ``<toolkit>__<skill>/`` dir; the flat layout (no
dir to mark) records each file in a ``MANIFEST_NAME`` JSON manifest at the
target root. Unsurfacing only removes what we own.

A connect is a *sync*, not an append: :func:`surface_skills` writes what
should be there and :func:`prune_skills` removes the toolbase-owned entries
that shouldn't, so a surface converges on the current answer rather than
accumulating every answer it has ever given.

New code should call :func:`surface_skills` / :func:`prune_skills` /
:func:`unsurface_skills` with an explicit :class:`SkillTarget` (typically
``adapter.skill_target(scope, project_root)``).
``install_skills_for_toolkit`` / ``uninstall_skills_for_toolkit`` remain as
Claude-dir back-compat wrappers.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


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
    # Optional bundle this skill is scoped to. None means toolkit-wide
    # (surfaced whenever the toolkit is installed). A named bundle ties
    # the skill to that bundle's availability -- see
    # ``install_skills_for_toolkit``.
    bundle: Optional[str] = None

    def is_complete(self) -> bool:
        return bool(self.name) and bool(self.description)


# Manifest that records which flat-layout files toolbase owns (a flat file
# has no directory to drop OWNED_MARKER in). Maps ``<filename> -> <toolkit>``.
MANIFEST_NAME = ".toolbase-managed.json"


@dataclass
class SkillTarget:
    """Where and how one harness surfaces a toolkit's skills.

    A harness's ``connect`` adapter returns a target for the scope being
    wired (or ``None`` if it has no skill surface there). Two layouts:

    - ``"dir"`` — a directory per skill holding ``SKILL.md``. What every
      supported harness reads, and what every adapter surfaces into today.
      Frontmatter is preserved (synthesized when missing) and a dir-form
      source's ``references/`` come along. ``SKILL.md`` is written as a real
      file, never a symlink: Codex's scanner does not follow one, and a
      surfaced skill that no scanner can see is worse than a stale copy.
      Ownership is tracked by an ``OWNED_MARKER`` file in each dir.
    - ``"flat"`` — a single markdown file per skill, a ``/<name>``
      slash-command prompt the user invokes and the model never sees. Only
      ``legacy_skill_targets`` returns these now, to clear what an older
      toolbase wrote into Codex's ``prompts/`` and OpenCode's ``command/``.
      A flat file has no dir to mark, so ownership is tracked by a JSON
      manifest at ``<root>/<MANIFEST_NAME>``, and supporting files have
      nowhere to go.

    ``frontmatter_keys`` (flat layout only) narrows the emitted frontmatter to
    the listed keys when ``keep_frontmatter`` is False: ``None`` strips the
    block entirely, a list rewrites it to just those keys pulled from the
    source. Ignored when ``keep_frontmatter`` is True.
    """

    harness: str
    root: Path
    layout: str  # "dir" | "flat"
    keep_frontmatter: bool
    frontmatter_keys: Optional[List[str]] = None


def _read_manifest(root: Path) -> Dict[str, str]:
    path = root / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(root: Path, data: Dict[str, str]) -> None:
    path = root / MANIFEST_NAME
    if data:
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    elif path.exists():
        try:
            path.unlink()
        except OSError:
            pass


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
        bundle=raw.get("bundle") if isinstance(raw.get("bundle"), str) else None,
    ), body


SKILL_DOC = "SKILL.md"


@dataclass
class SkillSource:
    """One skill as an author ships it, in either of two shapes.

    - **file** — ``skills/<name>.md``. The guide is the whole skill.
    - **directory** — ``skills/<name>/SKILL.md``, which can carry
      ``references/``, ``scripts/`` and assets beside the guide. This is the
      layout Claude Code and Antigravity use natively, so a skill written for
      either drops into a toolkit unchanged.

    ``doc`` is the markdown to read in both shapes; ``root`` is what the
    author named the skill (the file, or the directory), and is what the
    slug comes from.
    """

    doc: Path
    root: Path
    slug: str  # bare slug, no ``<toolkit>__`` prefix

    @property
    def is_dir(self) -> bool:
        return self.root != self.doc


def discover_skills(toolkit_dir: Path) -> List[SkillSource]:
    """Return a toolkit's skills, both file- and directory-form.

    A directory only counts as a skill if it holds a ``SKILL.md``; one that
    doesn't is ignored here and warned about at publish time. Dotfiles and
    macOS AppleDouble companions ("._foo.md") are filtered out.
    """
    skills_dir = toolkit_dir / "skills"
    if not skills_dir.exists():
        return []
    found: List[SkillSource] = []
    for p in sorted(skills_dir.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            doc = p / SKILL_DOC
            if doc.is_file():
                found.append(SkillSource(doc=doc, root=p, slug=_slug(p.name)))
        elif p.suffix == ".md":
            found.append(SkillSource(doc=p, root=p, slug=_slug(p.stem)))
    return found


def skill_dirs_without_doc(toolkit_dir: Path) -> List[Path]:
    """Directories under ``skills/`` that hold no ``SKILL.md``.

    These are silently invisible to discovery, which is the kind of thing an
    author should hear about before publishing rather than after.
    """
    skills_dir = toolkit_dir / "skills"
    if not skills_dir.exists():
        return []
    return sorted(
        p for p in skills_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
        and not (p / SKILL_DOC).is_file()
    )


def normalize_slug(value: str) -> str:
    """Canonical form of a skill slug: lowercase, words separated by ``-``.

    Skill files are named freely by authors (``getting_started.md``,
    ``Searching arXiv.md``, ``pythia-cards/``), and the slug is what every
    harness *displays* — so it should look like every other skill in the
    ecosystem rather than like a Python identifier. Codex ships
    ``openai-docs`` and ``skill-installer``; Claude Code ``code-review``;
    OpenCode documents "lowercase hyphen-separated" outright. Underscores
    were the odd one out.

    Every run of non-alphanumerics collapses to a single ``-``. Case is not
    split on: ``PythiaCards`` is one word here, because nothing can tell it
    apart from ``ArXiv``, where splitting would be wrong.

    Idempotent, which is what lets it double as the comparison key for a
    slug written in an older spelling — see ``slugs_match``.
    """
    s = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return s.lower() or "skill"


def slugs_match(a: str, b: str) -> bool:
    """Whether two skill slugs name the same skill.

    Compared canonically so a loadout written before slugs were
    hyphenated still matches: ``tb deactivate tk__my_guide`` recorded
    ``my_guide``, and the skill is now ``my-guide``. A literal comparison
    would silently stop matching and put a deactivated guide back in front
    of the agent -- the exact failure the prune was added to end.
    """
    return normalize_slug(a) == normalize_slug(b)


def _slug(stem: str) -> str:
    """Slug for a skill as the author named it on disk."""
    return normalize_slug(stem)


def surface_skills(
    toolkit_name: str,
    toolkit_dir: Path,
    target: SkillTarget,
    *,
    available_bundles: Optional[set] = None,
    disabled_slugs: Optional[set] = None,
) -> List[str]:
    """Surface a toolkit's skills into a harness's ``SkillTarget``.

    Returns the list of skill slugs that were surfaced (empty if the
    toolkit ships none). Idempotent: safe to call repeatedly; entries we
    own are overwritten.

    ``available_bundles`` gates bundle-scoped skills. A skill declares a
    bundle via ``bundle:`` in its frontmatter. When ``available_bundles``
    is provided, a skill scoped to a bundle not in the set is skipped
    (its bundle's config requirements aren't met, so the matching tools
    aren't served either — surfacing the guide would be misleading).
    Skills with no ``bundle:`` are always surfaced. ``None`` (the
    default) disables gating and surfaces every skill.

    ``disabled_slugs`` is a per-toolkit blocklist of bare skill slugs
    (from the active loadout's ``skills.disabled``, set by ``tb
    deactivate <toolkit>__<skill>``). A source whose slug is in the set is
    skipped — the per-skill analog of ``available_bundles``.
    """
    sources = discover_skills(toolkit_dir)
    if not sources:
        return []

    # Canonicalised, so a loadout written before slugs were hyphenated
    # still matches the skill it was meant to turn off.
    disabled_keys = (
        {normalize_slug(s) for s in disabled_slugs}
        if disabled_slugs is not None else None
    )

    target.root.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(target.root) if target.layout == "flat" else None
    surfaced: List[str] = []
    for src in sources:
        bare_slug = src.slug
        if disabled_keys is not None and normalize_slug(bare_slug) in disabled_keys:
            # Individually deactivated in the active loadout.
            continue

        text = src.doc.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        bundle = fm.bundle if fm else None
        if (
            bundle is not None
            and available_bundles is not None
            and bundle not in available_bundles
        ):
            # Bundle unavailable (config requirements unmet) — skip its
            # skill, mirroring how the orchestrator drops the bundle's
            # tools.
            continue

        slug = f"{toolkit_name}__{bare_slug}"
        if target.layout == "dir":
            _surface_dir(target.root, toolkit_name, slug, src, text, body, fm)
        else:
            _surface_flat(
                target.root, slug, text, body, fm,
                keep_frontmatter=target.keep_frontmatter,
                frontmatter_keys=target.frontmatter_keys,
                manifest=manifest, toolkit_name=toolkit_name,
            )
        surfaced.append(slug)

    if target.layout == "flat" and manifest is not None:
        _write_manifest(target.root, manifest)
    return surfaced


# Frontmatter keys that mean something to toolbase and nothing to a
# harness. They are curation inputs, not part of the guide, and a harness
# that validates its frontmatter has no reason to accept them.
_INTERNAL_FM_KEYS = {"bundle"}


def _emit_frontmatter(slug: str, toolkit_name: str, text: str,
                      fm: Optional[SkillFrontmatter]) -> str:
    """The frontmatter block a surfaced skill carries.

    ``name`` is always the qualified slug — the directory name, the thing
    ``tb activate <toolkit>__<skill>`` toggles, and what every harness
    displays. Passing the author's ``name`` through instead meant the
    ``<toolkit>__`` namespace existed only on disk: the UI showed whatever
    prose the author wrote, two toolkits shipping a ``mg5`` guide were
    indistinguishable, and the name you saw was not the name you could
    deactivate. Every harness documents the same convention (``name``
    matches the folder), so this is also what they expect.

    ``description`` is the author's, untouched — it is the trigger text the
    model reads, and the one field we have no business rewriting. It is
    synthesized only when absent, since a skill without one is filtered out
    before it reaches the model. Any other key the author set is preserved;
    toolbase's own are dropped.
    """
    raw = dict(fm.raw) if fm is not None else {}
    description = (fm.description if fm else None) or _first_line_summary(text)
    emitted = {
        "name": slug,
        "description": description or f"Guidance for {toolkit_name}.",
    }
    for key, value in raw.items():
        if key in emitted or key in _INTERNAL_FM_KEYS:
            continue
        emitted[key] = value
    block = yaml.safe_dump(
        emitted, default_flow_style=False, allow_unicode=True, sort_keys=False,
        # One key per line. A description is routinely a few hundred
        # characters and the default 80-column wrap folds it across lines --
        # valid YAML, but it reads as a broken file and only a real parser
        # puts it back together.
        width=10 ** 6,
    )
    return f"---\n{block}---\n"


def _surface_dir(
    root: Path, toolkit_name: str, slug: str, src: "SkillSource", text: str,
    body: str, fm: Optional[SkillFrontmatter],
) -> None:
    """Write one skill as ``<root>/<slug>/SKILL.md`` (the dir layout).

    A directory-form source also gets its supporting files mirrored beside
    the guide, so ``references/`` and friends resolve as the author wrote
    them.
    """
    dest_dir = root / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Drop the marker so we know we own this directory.
    (dest_dir / OWNED_MARKER).write_text(toolkit_name + "\n")

    skill_path = dest_dir / "SKILL.md"
    # If a previous surface left a file/symlink here, replace it.
    if skill_path.exists() or skill_path.is_symlink():
        skill_path.unlink()

    # A real file, never a symlink. This used to link the source so edits
    # to an editable checkout showed up without re-connecting, and that
    # convenience cost the whole feature on Codex: its skill scanner does
    # not follow a symlinked SKILL.md, so a surfaced skill was silently
    # invisible -- discovered by every other harness and by nothing that
    # would tell you why. A copy is what every scanner agrees on, and
    # re-connecting to pick up an edit is the same step every other state
    # change already needs. Writing our own frontmatter is free once we
    # are copying anyway.
    guide = body if fm is not None else text
    skill_path.write_text(
        _emit_frontmatter(slug, toolkit_name, text, fm)
        + "\n" + guide.lstrip("\n"),
        encoding="utf-8",
    )

    if src.is_dir:
        _mirror_supporting_files(dest_dir, src.root)


def _mirror_supporting_files(dest_dir: Path, src_dir: Path) -> None:
    """Mirror a dir-form skill's non-SKILL.md contents into ``dest_dir``.

    Per-child copies rather than a copy of the whole directory: the
    surfaced dir has to stay ours to write ``OWNED_MARKER`` into, and
    mirroring the top level would mean writing that marker into the
    author's toolkit. Real files for the same reason ``SKILL.md`` is one —
    see ``_surface_dir``. Stale children from an earlier surface are
    cleared first, so a removed reference file doesn't linger.
    """
    keep = {OWNED_MARKER, SKILL_DOC}
    for child in dest_dir.iterdir():
        if child.name in keep:
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child, ignore_errors=True)

    for child in sorted(src_dir.iterdir()):
        if child.name == SKILL_DOC or child.name.startswith("."):
            continue
        dest = dest_dir / child.name
        if child.is_dir():
            shutil.copytree(child, dest)
        else:
            shutil.copy2(child, dest)


def _surface_flat(
    root: Path, slug: str, text: str, body: str,
    fm: Optional[SkillFrontmatter], *, keep_frontmatter: bool,
    frontmatter_keys: Optional[List[str]] = None,
    manifest: Dict[str, str], toolkit_name: str,
) -> None:
    """Write one skill as ``<root>/<slug>.md`` (flat prompt/command layout).

    The file becomes the text of a ``/<slug>`` slash-command prompt. What we
    keep of the source frontmatter depends on the harness:

    - ``keep_frontmatter`` True — keep the block verbatim.
    - ``frontmatter_keys`` set — rewrite the block to just those keys pulled
      from the source (OpenCode honors ``description`` in its command files).
    - otherwise — strip the block, writing only the body (Codex renders a
      YAML block as prose).

    We always (re)write a copy (never symlink): the on-disk content differs
    from the source once frontmatter is transformed, and the harness reads it
    at invocation rather than watching for edits.
    """
    if keep_frontmatter:
        content = text
    elif frontmatter_keys and fm is not None:
        kept = {
            k: fm.raw[k] for k in frontmatter_keys
            if fm.raw.get(k) is not None
        }
        if kept:
            fm_block = yaml.safe_dump(
                kept, default_flow_style=False, allow_unicode=True,
                sort_keys=False,
            )
            content = f"---\n{fm_block}---\n\n" + body.lstrip("\n")
        else:
            content = body
    else:
        content = body
    content = content.lstrip("\n")
    if not content.endswith("\n"):
        content += "\n"
    dest = root / f"{slug}.md"
    dest.write_text(content, encoding="utf-8")
    manifest[dest.name] = toolkit_name


def unsurface_skills(toolkit_name: str, target: SkillTarget) -> List[str]:
    """Remove a single toolkit's surfaced skills from ``target``.

    Only removes entries toolbase owns (an ``OWNED_MARKER`` for the ``dir``
    layout, a manifest entry for the ``flat`` layout). User-placed files
    with the same name prefix are left alone. Returns removed slugs.
    """
    if not target.root.exists():
        return []
    if target.layout == "dir":
        return _unsurface_dir(target.root, toolkit_name=toolkit_name)
    return _unsurface_flat(target.root, toolkit_name=toolkit_name)


def unsurface_all(target: SkillTarget) -> List[str]:
    """Remove every toolbase-owned skill from ``target`` (used on disconnect)."""
    if not target.root.exists():
        return []
    if target.layout == "dir":
        return _unsurface_dir(target.root, toolkit_name=None)
    return _unsurface_flat(target.root, toolkit_name=None)


def owned_slugs(target: SkillTarget) -> List[str]:
    """Qualified slugs toolbase currently owns in ``target``.

    A read-only inventory of what a surface holds, by the same ownership
    evidence the removal paths use. Both scopes of a harness are read at
    once, so this is how one connect can tell the user the other scope
    still has skills in front of the agent.
    """
    if not target.root.exists():
        return []
    if target.layout == "dir":
        return sorted(
            e.name for e in target.root.iterdir()
            if e.is_dir() and (e / OWNED_MARKER).exists()
        )
    return sorted(
        f[:-3] if f.endswith(".md") else f
        for f in _read_manifest(target.root)
    )


def prune_skills(
    target: SkillTarget,
    *,
    keep: set,
    skip_owners: Optional[set] = None,
) -> List[str]:
    """Remove toolbase-owned entries from ``target`` that aren't in ``keep``.

    Surfacing alone only ever adds. Everything that *stops* a skill from
    being surfaced — its toolkit dropping out of the active loadout, a ``tb
    deactivate <toolkit>__<skill>``, a bundle gate closing, the author
    deleting the guide in a new version — leaves the last-written copy in
    place, still in front of the agent and contradicting what ``tb list``
    reports. Pruning against the set just surfaced is what makes a connect
    converge on the current answer instead of accumulating every answer
    it has ever given.

    ``keep`` is the qualified slugs (``<toolkit>__<skill>``) that were just
    surfaced. ``skip_owners`` names toolkits to leave entirely alone —
    surfacing is best-effort per toolkit, and one that failed contributed
    no slugs to ``keep``, so pruning it would delete a working skill
    because of an unrelated error.

    Only entries toolbase owns are considered, by the same evidence
    ``unsurface_skills`` uses: an ``OWNED_MARKER`` for the dir layout, a
    manifest entry for the flat one. Returns the removed slugs.
    """
    if not target.root.exists():
        return []
    skip = skip_owners or set()
    if target.layout == "dir":
        return _prune_dir(target.root, keep=keep, skip_owners=skip)
    return _prune_flat(target.root, keep=keep, skip_owners=skip)


def _prune_dir(root: Path, *, keep: set, skip_owners: set) -> List[str]:
    removed: List[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in keep:
            continue
        if not (entry / OWNED_MARKER).exists():
            continue  # not ours
        if _dir_owner(entry) in skip_owners:
            continue
        try:
            shutil.rmtree(entry)
        except OSError:
            continue  # leave it; the user can clean up manually
        removed.append(entry.name)
    return removed


def _dir_owner(entry: Path) -> Optional[str]:
    """The toolkit that owns a surfaced skill dir.

    ``OWNED_MARKER`` records it. Falling back to the ``<toolkit>__`` prefix
    covers a marker we can't read, so an unreadable file can't quietly cost
    a toolkit the protection ``skip_owners`` is giving it.
    """
    try:
        owner = (entry / OWNED_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        owner = ""
    if owner:
        return owner
    return entry.name.split("__", 1)[0] if "__" in entry.name else None


def _prune_flat(root: Path, *, keep: set, skip_owners: set) -> List[str]:
    manifest = _read_manifest(root)
    removed: List[str] = []
    for fname, owner in list(manifest.items()):
        slug = fname[:-3] if fname.endswith(".md") else fname
        if slug in keep or owner in skip_owners:
            continue
        fpath = root / fname
        try:
            if fpath.exists():
                fpath.unlink()
        except OSError:
            # Keep the manifest entry so a later run retries.
            continue
        removed.append(slug)
        del manifest[fname]
    _write_manifest(root, manifest)
    return removed


def _unsurface_dir(root: Path, *, toolkit_name: Optional[str]) -> List[str]:
    prefix = f"{toolkit_name}__" if toolkit_name is not None else None
    removed: List[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if prefix is not None and not entry.name.startswith(prefix):
            continue
        if not (entry / OWNED_MARKER).exists():
            continue
        try:
            shutil.rmtree(entry)
            removed.append(entry.name)
        except OSError:
            # Leave it; user can clean up manually. Don't fail the caller.
            pass
    return removed


def _unsurface_flat(root: Path, *, toolkit_name: Optional[str]) -> List[str]:
    manifest = _read_manifest(root)
    removed: List[str] = []
    for fname, owner in list(manifest.items()):
        if toolkit_name is not None and owner != toolkit_name:
            continue
        fpath = root / fname
        try:
            if fpath.exists():
                fpath.unlink()
        except OSError:
            # Leave it and keep the manifest entry so a later run retries.
            continue
        removed.append(fname[:-3] if fname.endswith(".md") else fname)
        del manifest[fname]
    _write_manifest(root, manifest)
    return removed


# ── back-compat: the Claude-Code dir layout, kept for existing callers ──


def _claude_dir_target(skills_dir: Optional[Path]) -> SkillTarget:
    # Resolve ``CLAUDE_SKILLS_DIR`` at call time (not as a default-arg
    # expression, which Python binds once at definition) so tests and the
    # e2e harness can monkeypatch it — otherwise synthetic skills leak into
    # the developer's real ``~/.claude/skills/``.
    root = skills_dir if skills_dir is not None else CLAUDE_SKILLS_DIR
    return SkillTarget("claude-code", root, layout="dir", keep_frontmatter=True)


def install_skills_for_toolkit(
    toolkit_name: str,
    toolkit_dir: Path,
    *,
    skills_dir: Optional[Path] = None,
    available_bundles: Optional[set] = None,
) -> List[str]:
    """Back-compat wrapper: surface into the Claude Code ``~/.claude/skills/``
    dir layout. New code should call :func:`surface_skills` with an explicit
    :class:`SkillTarget` (typically ``adapter.skill_target(scope, root)``)."""
    return surface_skills(
        toolkit_name, toolkit_dir, _claude_dir_target(skills_dir),
        available_bundles=available_bundles,
    )


def uninstall_skills_for_toolkit(
    toolkit_name: str,
    *,
    skills_dir: Optional[Path] = None,
) -> List[str]:
    """Back-compat wrapper: remove a toolkit's skills from the Claude Code
    ``~/.claude/skills/`` dir layout. See :func:`unsurface_skills`."""
    return unsurface_skills(toolkit_name, _claude_dir_target(skills_dir))


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
