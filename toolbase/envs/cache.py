"""
Cache-slot metadata: ``.install_meta.yaml``, ``.last_used``, ``.disk_size``.

Each toolkit-version cache slot at ``~/.toolbase/cache/<name>/<version>/``
carries three sidecar metadata files:

- ``.install_meta.yaml`` — the canonical "what is this install" file.
  Schema-versioned via ``envs.schema``. Carries ``name``, ``version``,
  ``install_method`` (venv / conda / docker), ``installed_at``,
  ``python_version``, environment-specific fields (``python_path`` for
  venv, ``env_name`` for conda).
- ``.last_used`` — single-line ISO-8601 timestamp. Touched on every
  ``tb serve`` spawn. Missing → "never" in ``tb list``.
- ``.disk_size`` — single-line integer (bytes). Written once at install
  time. Missing → "—" in ``tb list``.

This module also provides a cache walker that returns ``CacheEntry``
records for the install / list / serve paths. The walker filters out
non-directories (so the legacy 3C downloads cache can coexist
gracefully) and entries missing ``.install_meta.yaml`` (broken / partial
installs).

NB: ``.install_meta.yaml`` is the new canonical metadata file. The
0.4.x ``.tb_meta.json`` (JSON, no schema_version) is left around for
backward compat *within* the version slot — the existing
``setup/runner.py`` reads it for ``python_path`` / ``env_name``. Phase 2
writes both so serve / setup keep working without touching their
metadata-parsing code; later phases can drop the JSON sidecar.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import cache_dir, cache_root
from .schema import read_versioned_yaml, write_versioned_yaml


INSTALL_META_FILE = ".install_meta.yaml"
LAST_USED_FILE = ".last_used"
DISK_SIZE_FILE = ".disk_size"
LEGACY_META_FILE = ".tb_meta.json"  # Carried forward for the runner / serve

# Soft cap on the disk-size walk at install time. If sizing a fresh
# install takes longer than this, drop the feature for that entry
# (per the brief: "tb list MUST be fast; drop disk_size before
# slowing list down").
DISK_SIZE_BUDGET_SECONDS = 2.0


@dataclass
class CacheEntry:
    """One walked cache slot."""

    name: str
    version: str
    path: Path
    install_meta: Dict[str, Any] = field(default_factory=dict)
    # Legacy 0.4.x metadata, parsed from ``.tb_meta.json`` if present.
    # Carries ``python_path``, ``env_name``, etc. that the serve and
    # setup runner still consume directly. Phase 5+ can fold these
    # into ``install_meta`` if we want.
    legacy_meta: Dict[str, Any] = field(default_factory=dict)
    last_used_iso: Optional[str] = None
    disk_size_bytes: Optional[int] = None


# ── install-meta read/write ─────────────────────────────────────────


def write_install_meta(
    slot_dir: Path,
    *,
    name: str,
    version: str,
    install_method: str,
    python_version: str,
    extras: Optional[Dict[str, Any]] = None,
    source_tarball_sha256: str = "",
) -> Path:
    """Write the canonical ``.install_meta.yaml`` for a fresh install.

    Args:
        slot_dir: the cache slot at ``cache/<name>/<version>/``.
        name: toolkit name.
        version: toolkit version.
        install_method: one of ``"venv"``, ``"conda"``, ``"docker"``.
        python_version: e.g. ``"3.12"``.
        extras: install-method-specific fields. For venv:
            ``{"python_path": "..."}``. For conda: ``{"env_name": "..."}``.
        source_tarball_sha256: hex digest of the tarball we extracted
            (or empty string if unknown — Phase 2 tarball-download path
            doesn't compute this yet; can fill in later).

    Returns the path written.
    """
    payload: Dict[str, Any] = {
        "name": name,
        "version": version,
        "install_method": install_method,
        "python_version": python_version,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "source_tarball_sha256": source_tarball_sha256,
    }
    if extras:
        payload.update(extras)
    path = slot_dir / INSTALL_META_FILE
    return write_versioned_yaml(
        path, "install_meta", payload,
        # install_meta is not secret; the venv next to it isn't 0o600.
        mode=0o644,
    )


def read_install_meta(slot_dir: Path) -> Optional[Dict[str, Any]]:
    """Read ``.install_meta.yaml`` for a slot. Returns None if absent."""
    path = slot_dir / INSTALL_META_FILE
    if not path.exists():
        return None
    raw = read_versioned_yaml(path, "install_meta")
    return {k: v for k, v in raw.items() if k != "schema_version"}


def installed_bundles(slot_dir: Path) -> Optional[List[str]]:
    """Return the list of installed bundles for a slot, or None for "all".

    Reads ``bundles`` from ``.install_meta.yaml``. Returns:
    - ``None`` if no bundles field is recorded (legacy installs, or
      installs that brought in every declared bundle — both treated
      identically as "the whole toolkit").
    - a list (possibly empty — "base only, no optional bundles") when
      the install was scoped to a specific subset.

    The orchestrator uses this to gate at serve time: a tool whose
    bundles are all outside the installed set is skipped with a clear
    log line. Empty list means "every tool with a declared bundle is
    skipped" — only bundle-less tools are served.
    """
    meta = read_install_meta(slot_dir)
    if not meta:
        return None
    raw = meta.get("bundles")
    if raw is None:
        return None
    if isinstance(raw, list):
        return [b for b in raw if isinstance(b, str)]
    return None


def update_install_meta_bundles(slot_dir: Path, bundles: List[str]) -> None:
    """Rewrite ``.install_meta.yaml`` with an updated ``bundles`` list.

    Preserves every other field; used by additive installs that add new
    bundles to an existing slot without touching python_path / install
    method / etc. Idempotent; safe to call when no meta exists (no-op).
    """
    path = slot_dir / INSTALL_META_FILE
    if not path.exists():
        return
    raw = read_versioned_yaml(path, "install_meta")
    # Strip the schema_version envelope so we re-stamp it on write.
    payload = {k: v for k, v in raw.items() if k != "schema_version"}
    payload["bundles"] = sorted(set(bundles))
    write_versioned_yaml(path, "install_meta", payload, mode=0o644)


def write_legacy_meta(slot_dir: Path, meta: Dict[str, Any]) -> Path:
    """Write the ``.tb_meta.json`` carry-along file.

    Serve and the setup runner both still read this; until those move
    onto ``.install_meta.yaml`` we maintain both. The legacy file is
    plain JSON, no schema_version envelope.
    """
    path = slot_dir / LEGACY_META_FILE
    path.write_text(json.dumps(meta, indent=2))
    return path


def read_legacy_meta(slot_dir: Path) -> Dict[str, Any]:
    """Read ``.tb_meta.json``. Returns ``{}`` if absent or malformed."""
    path = slot_dir / LEGACY_META_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# ── last-used touch ─────────────────────────────────────────────────


def touch_last_used(slot_dir: Path, *, when: Optional[datetime] = None) -> None:
    """Atomic-rewrite the ``.last_used`` file to a current ISO-8601 stamp.

    Tolerant of write failures — last_used is a UX nicety, not a correctness
    requirement. Never raise.
    """
    if when is None:
        when = datetime.now()
    stamp = when.isoformat(timespec="seconds")
    path = slot_dir / LAST_USED_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(stamp + "\n")
        os.replace(tmp, path)
    except OSError:
        # Best-effort; don't fail spawn over a missing timestamp.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def read_last_used(slot_dir: Path) -> Optional[str]:
    """Return the ISO-8601 timestamp string from ``.last_used``, or None."""
    path = slot_dir / LAST_USED_FILE
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ── disk size ──────────────────────────────────────────────────────


def compute_and_write_disk_size(
    slot_dir: Path,
    *,
    budget_seconds: float = DISK_SIZE_BUDGET_SECONDS,
) -> Optional[int]:
    """Walk ``slot_dir``, sum ``stat().st_size``, write ``.disk_size``.

    Returns the byte count, or ``None`` if the walk exceeded
    ``budget_seconds`` (in which case ``.disk_size`` is NOT written;
    ``tb list`` will show "—" for this entry — per the brief, list
    MUST stay fast, so we drop the feature for slow entries rather
    than recompute on demand).

    Pure-Python walk; no shelling out to ``du`` (portability).
    """
    deadline = time.monotonic() + budget_seconds
    total = 0
    try:
        for root, dirs, files in os.walk(slot_dir, followlinks=False):
            if time.monotonic() > deadline:
                return None
            for f in files:
                if time.monotonic() > deadline:
                    return None
                fp = Path(root) / f
                try:
                    total += fp.stat().st_size
                except OSError:
                    # Race against deletion or permission flake; ignore.
                    continue
    except OSError:
        return None

    path = slot_dir / DISK_SIZE_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{total}\n")
        os.replace(tmp, path)
    except OSError:
        return None
    return total


def read_disk_size(slot_dir: Path) -> Optional[int]:
    """Return the cached byte count from ``.disk_size``, or ``None``."""
    path = slot_dir / DISK_SIZE_FILE
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


# ── cache walker ───────────────────────────────────────────────────


def walk_cache(
    *,
    user_base: Optional[Path] = None,
) -> List[CacheEntry]:
    """Walk ``~/.toolbase/cache/<name>/<version>/`` and return all entries.

    Each entry has ``install_meta`` + ``legacy_meta`` populated when
    those files exist on disk. Non-directory siblings (such as the
    legacy 3C ``_setup_validate.json`` or content-addressed download
    files under ``cache/<toolkit>/``) are skipped.

    Sorted by (name, version) for deterministic output.

    Filters: a version sub-directory is recognized as an install only
    if it contains a non-empty ``.install_meta.yaml`` OR a legacy
    ``.tb_meta.json``. Partial installs (post-extract pre-meta) are
    invisible to ``tb list``; that's intentional — they'd otherwise
    show up with all fields empty.
    """
    root = cache_root(base=user_base)
    if not root.exists():
        return []

    entries: List[CacheEntry] = []
    for name_dir in sorted(root.iterdir()):
        if not name_dir.is_dir():
            continue
        # Toolkit names can't start with ``_`` (registry-validated
        # ``[a-z][a-z0-9-]*``). Defensive: skip anything starting with
        # underscore or dot so the Phase 3C ``_setup_validate.json``
        # file and any future namespacing-collision dirs are ignored.
        if name_dir.name.startswith("_") or name_dir.name.startswith("."):
            continue
        for version_dir in sorted(name_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            # Skip dot-prefixed siblings.
            if version_dir.name.startswith("."):
                continue
            install_meta = read_install_meta(version_dir) or {}
            legacy_meta = read_legacy_meta(version_dir)
            if not install_meta and not legacy_meta:
                # Partial install or random subdir; skip.
                continue
            entries.append(CacheEntry(
                name=name_dir.name,
                version=version_dir.name,
                path=version_dir,
                install_meta=install_meta,
                legacy_meta=legacy_meta,
                last_used_iso=read_last_used(version_dir),
                disk_size_bytes=read_disk_size(version_dir),
            ))
    return entries


def list_versions(name: str, *, user_base: Optional[Path] = None) -> List[str]:
    """Return all installed versions of ``name`` from the cache."""
    return [
        e.version for e in walk_cache(user_base=user_base) if e.name == name
    ]


def find_slot(
    name: str,
    version: str,
    *,
    user_base: Optional[Path] = None,
) -> Optional[CacheEntry]:
    """Return the cache entry for ``(name, version)``, or None."""
    for e in walk_cache(user_base=user_base):
        if e.name == name and e.version == version:
            return e
    return None
