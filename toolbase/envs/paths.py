"""
Pure-functional path computations for the 0.5.0 cache-plus-manifest model.

Layout (full target — Phase 2 implements ``cache_dir``; Phase 3 implements
project-side):

::

    ~/.toolbase/                         user-scope root
    ├── cache/                             NEW
    │   └── <name>/<version>/              one slot per (name, version)
    │       ├── venv/ or conda-env-ref/    binary content
    │       ├── tools/                     toolkit content
    │       ├── toolkit.yaml
    │       ├── .tb_meta.json              ownership marker (legacy carry)
    │       ├── .install_meta.yaml         NEW (schema_version + install meta)
    │       ├── .last_used                 NEW (ISO-8601 timestamp)
    │       └── .disk_size                 NEW (single integer, bytes)
    ├── config/<toolkit>.yaml              user-level config
    ├── default-project/                   NEW: implicit project fallback
    │   ├── manifest.yaml
    │   └── config/<toolkit>.yaml
    ├── logs/<toolkit>.log
    ├── serve.yaml                         user-level serve config
    └── config.json                        login state

    <project>/.toolbase/                 project-scope root
    ├── manifest.yaml                      pinned toolkit list
    └── config/<toolkit>.yaml              project-level overrides

Resolver-pattern discipline (HANDOFF gotcha #12): every function takes an
optional ``base`` (or equivalent) parameter defaulting to ``None`` and
re-reads ``toolbase.config.CONFIG_DIR`` at call time. This lets tests
redirect the entire substrate by monkeypatching one symbol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import config as _config_mod


# ── user-scope root ─────────────────────────────────────────────────


def _user_root(base: Optional[Path] = None) -> Path:
    """Return ``~/.toolbase/`` (or test override).

    Re-reads ``toolbase.config.CONFIG_DIR`` on every call so test
    monkeypatching works. See HANDOFF.md gotcha #12.
    """
    if base is not None:
        return base
    return _config_mod.CONFIG_DIR


# ── cache layout ────────────────────────────────────────────────────


def cache_root(*, base: Optional[Path] = None) -> Path:
    """``~/.toolbase/cache/`` — the binary cache root.

    Does NOT create the directory; callers do that when they're about
    to write. Reading from a non-existent cache root is the empty case
    (``tb list`` shows "no toolkits installed").
    """
    return _user_root(base) / "cache"


def cache_dir(name: str, version: str, *, base: Optional[Path] = None) -> Path:
    """Path to one toolkit's versioned cache slot.

    ``~/.toolbase/cache/<name>/<version>/``. Does NOT create the
    directory; install pipeline does that when extracting.
    """
    return cache_root(base=base) / name / version


# ── legacy carry — 0.4.x layout, for migration detection only ────────


def legacy_toolkits_dir(*, base: Optional[Path] = None) -> Path:
    """``~/.toolbase/toolkits/`` — the 0.4.x install dir.

    Returned for Phase 6's cutover messaging. Don't install into this
    in 0.5.0+; ``cache_dir(...)`` is the new home.
    """
    return _user_root(base) / "toolkits"


# ── user-level config ───────────────────────────────────────────────


def user_config_path(toolkit: str, *, base: Optional[Path] = None) -> Path:
    """``~/.toolbase/config/<toolkit>.yaml``.

    Same path Phase 3C wrote to; the new model just treats it as the
    user-level layer of a two-layer config stack.
    """
    return _user_root(base) / "config" / f"{toolkit}.yaml"


# ── default-project fallback ────────────────────────────────────────


def default_project_root(*, base: Optional[Path] = None) -> Path:
    """``~/.toolbase/default-project/`` — implicit project fallback.

    Returned when the discovery walk finds no project. Phase 3 creates
    it on demand on first write; Phase 1/2 just compute the path.
    """
    return _user_root(base) / "default-project"


# ── project-scope (works for both real projects and default-project) ─


def project_manifest_path(project_root: Path) -> Path:
    """``<project_root>/.toolbase/manifest.yaml``.

    Works for both real projects (where ``project_root`` is the
    discovered git-like directory) and the default-project (where
    ``project_root`` is ``~/.toolbase/default-project/``). The
    ``.toolbase/`` segment is appended uniformly; ``default-project``
    therefore lives at ``~/.toolbase/default-project/.toolbase/manifest.yaml``.

    NOTE: the brief specifies the default-project layout as
    ``~/.toolbase/default-project/manifest.yaml`` (no nested
    ``.toolbase/`` segment, because the parent dir is itself the
    "project"). We honor that by special-casing default-project below.
    """
    if _is_default_project(project_root):
        return project_root / "manifest.yaml"
    return project_root / ".toolbase" / "manifest.yaml"


def project_config_path(project_root: Path, toolkit: str) -> Path:
    """Path to ``<project>/.toolbase/config/<toolkit>.yaml``.

    Same default-project special-case as ``project_manifest_path``.
    """
    if _is_default_project(project_root):
        return project_root / "config" / f"{toolkit}.yaml"
    return project_root / ".toolbase" / "config" / f"{toolkit}.yaml"


def _is_default_project(project_root: Path) -> bool:
    """Return True if ``project_root`` IS the default-project dir.

    Tolerates monkeypatched ``CONFIG_DIR``. Comparison is on resolved
    parent + name to avoid false positives on symlinks.
    """
    try:
        resolved = project_root.resolve()
    except OSError:
        resolved = project_root
    try:
        dp = default_project_root().resolve()
    except OSError:
        dp = default_project_root()
    return resolved == dp
