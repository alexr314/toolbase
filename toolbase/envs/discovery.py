"""
Project-root discovery walk.

Behavior (per the 0.5.0 brief):

1. Walk upward from ``cwd`` looking for ``.toolbase/manifest.yaml``.
   Stop at the first hit; return that directory as the project root.
2. If none found, fall back to ``default_project_root()``.
3. An explicit ``override`` (the ``--project-dir <path>`` CLI flag)
   short-circuits both above.

All-pure-function, with ``cwd`` and ``override`` injectable so the
discovery logic is testable without changing the real working directory.
Phase 1 ships this substrate; Phase 3 wires the CLI flag.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .paths import default_project_root


# Marker we look for during the upward walk.
_MARKER_DIR = ".toolbase"
_MARKER_FILE = "manifest.yaml"


def find_project_root(
    *,
    cwd: Optional[Path] = None,
    override: Optional[Path] = None,
) -> Optional[Path]:
    """Walk upward from ``cwd`` looking for a ``.toolbase/manifest.yaml``.

    Returns the directory containing the ``.toolbase/`` directory
    (NOT the ``.toolbase/`` directory itself). If the override is
    given, returns ``override.resolve()`` immediately. If no manifest
    is found anywhere up to the filesystem root, returns ``None``
    (caller decides whether to fall back to the default-project).

    The walk stops at the filesystem root (``Path("/")`` on POSIX,
    the drive root on Windows). It does NOT cross filesystem
    boundaries explicitly — symlinks are followed via ``resolve()``.

    Args:
        cwd: starting directory. Defaults to ``Path.cwd()`` if None.
            Tests pass an injected path.
        override: explicit project root to use. If a ``.toolbase/``
            directory doesn't exist there yet, that's fine — the path
            is returned anyway (the caller may create it on demand).
    """
    if override is not None:
        # Resolve the override path so downstream comparisons (e.g.
        # "are we in the default-project?") are deterministic. Use
        # ``Path(...)`` to coerce strs, then ``resolve(strict=False)``
        # so a path that doesn't exist yet is tolerated.
        return Path(override).resolve(strict=False)

    if cwd is None:
        cwd = Path.cwd()

    try:
        current = Path(cwd).resolve(strict=False)
    except OSError:
        current = Path(cwd)

    # Bound the walk by an absolute parent count to defend against
    # filesystem weirdness (symlink loops, infinite descent). On any
    # real filesystem this terminates in single-digit iterations.
    seen: set = set()
    while True:
        manifest = current / _MARKER_DIR / _MARKER_FILE
        if manifest.is_file():
            return current

        parent = current.parent
        if parent == current:
            # Reached filesystem root; bail out.
            return None
        key = str(current)
        if key in seen:  # pragma: no cover (defensive)
            return None
        seen.add(key)
        current = parent


def project_root_or_default(
    *,
    cwd: Optional[Path] = None,
    override: Optional[Path] = None,
    user_base: Optional[Path] = None,
) -> Path:
    """Find the active project root, falling back to default-project.

    This is the function callers should use 99% of the time. It
    composes ``find_project_root`` with the default-project fallback.

    Args:
        cwd: starting directory for the walk. Defaults to
            ``Path.cwd()``.
        override: explicit project root (``--project-dir``).
        user_base: test override for ``~/.toolbase/`` — passed
            through to ``default_project_root``.

    Returns:
        The discovered project root, or the default-project path if
        no manifest was found.
    """
    found = find_project_root(cwd=cwd, override=override)
    if found is not None:
        return found
    return default_project_root(base=user_base)
