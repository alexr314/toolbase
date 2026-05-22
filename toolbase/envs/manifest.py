"""
Project manifest model — the pin list a project commits to git.

A project's manifest lives at ``<project>/.toolbase/manifest.yaml``
(or, for the default-project, ``~/.toolbase/default-project/manifest.yaml``).
It carries:

::

    schema_version: 1
    toolkits:
      - name: heptapod
        version: 0.3.0
        pinned_at: 2026-05-12T10:23:00
      - name: arxiv-search
        version: 0.2.0
        pinned_at: 2026-05-08T14:01:00

This module owns parse, validate, round-trip-with-comments. ``add_pin`` /
``remove_pin`` / ``get_pin`` are small helpers that read → mutate → write
in one shot so callers don't have to juggle stale dict copies.

Atomic-write + schema-versioned read goes through ``schema.py``; this
module is just the data model + helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .schema import read_versioned_yaml, write_versioned_yaml


_FILE_TYPE = "project_manifest"


@dataclass
class ManifestEntry:
    """One pinned toolkit in a project's manifest."""

    name: str
    version: str
    pinned_at: str = ""  # ISO-8601; "" sentinel for "not yet stamped"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "pinned_at": self.pinned_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ManifestEntry":
        if not isinstance(data, dict):
            raise ValueError(f"manifest entry must be a mapping, got {type(data).__name__}")
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"manifest entry missing valid 'name': {data!r}")
        version = data.get("version")
        if not isinstance(version, str) or not version:
            raise ValueError(f"manifest entry {name!r} missing valid 'version': {data!r}")
        pinned_at = data.get("pinned_at", "")
        if pinned_at is None:
            pinned_at = ""
        if not isinstance(pinned_at, str):
            pinned_at = str(pinned_at)
        return cls(name=name, version=version, pinned_at=pinned_at)


@dataclass
class Manifest:
    """A project's pinned-toolkit list.

    Comparisons treat entries as a set (ordering is presentation, not
    semantics). When writing back, ``save_manifest`` sorts entries by
    name so git diffs are stable across machines.
    """

    toolkits: List[ManifestEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"toolkits": [e.to_dict() for e in self.toolkits]}

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        if not isinstance(data, dict):
            raise ValueError(
                f"manifest must be a mapping, got {type(data).__name__}"
            )
        toolkits_raw = data.get("toolkits", [])
        if toolkits_raw is None:
            toolkits_raw = []
        if not isinstance(toolkits_raw, list):
            raise ValueError(
                f"manifest 'toolkits' must be a list, got "
                f"{type(toolkits_raw).__name__}"
            )
        entries = [ManifestEntry.from_dict(e) for e in toolkits_raw]
        return cls(toolkits=entries)

    def find(self, name: str) -> Optional[ManifestEntry]:
        """Return the entry for ``name``, or None if not pinned."""
        for e in self.toolkits:
            if e.name == name:
                return e
        return None


# ── read / write ────────────────────────────────────────────────────


_MANIFEST_HEADER_COMMENT = (
    "toolbase project manifest — checked into git.\n"
    "Pin a toolkit with: tb install <name>@<version>\n"
    "See https://tool-base.org/docs/environments\n"
)


def load_manifest(path: Path) -> Manifest:
    """Read a manifest from disk, returning an empty ``Manifest`` if absent.

    Raises ``SchemaTooNewError`` from ``schema.py`` if the file's
    ``schema_version`` exceeds what this build understands.
    """
    raw = read_versioned_yaml(path, _FILE_TYPE, default={"toolkits": []})
    # Strip the schema_version key before constructing the model; it's
    # an envelope concern, not data.
    payload = {k: v for k, v in raw.items() if k != "schema_version"}
    return Manifest.from_dict(payload)


def save_manifest(path: Path, manifest: Manifest) -> Path:
    """Write a manifest to disk atomically.

    Entries are sorted by name for stable git diffs. ``schema_version``
    is stamped via ``write_versioned_yaml``. Mode is 0o644 (not 0o600)
    because the manifest is checked into git and not secret-bearing.
    """
    sorted_entries = sorted(manifest.toolkits, key=lambda e: e.name)
    payload = {"toolkits": [e.to_dict() for e in sorted_entries]}
    return write_versioned_yaml(
        path,
        _FILE_TYPE,
        payload,
        header_comment=_MANIFEST_HEADER_COMMENT,
        mode=0o644,
    )


# ── one-shot helpers (used by install / uninstall) ─────────────────


def add_pin(
    path: Path,
    name: str,
    version: str,
    *,
    pinned_at: Optional[str] = None,
) -> Manifest:
    """Add or update a pin in the manifest at ``path``.

    Read → mutate → write. If ``name`` is already pinned, replaces the
    version (and refreshes ``pinned_at``). Returns the post-write
    manifest object so callers can inspect it.

    ``pinned_at`` defaults to the current local ISO-8601 timestamp.
    """
    manifest = load_manifest(path)
    stamp = pinned_at if pinned_at is not None else datetime.now().isoformat(
        timespec="seconds"
    )
    existing = manifest.find(name)
    if existing is not None:
        existing.version = version
        existing.pinned_at = stamp
    else:
        manifest.toolkits.append(
            ManifestEntry(name=name, version=version, pinned_at=stamp)
        )
    save_manifest(path, manifest)
    return manifest


def remove_pin(path: Path, name: str) -> bool:
    """Remove a pin from the manifest. Returns True if a pin was removed.

    If the manifest file doesn't exist, returns False (nothing to do;
    not an error).
    """
    if not path.exists():
        return False
    manifest = load_manifest(path)
    before = len(manifest.toolkits)
    manifest.toolkits = [e for e in manifest.toolkits if e.name != name]
    if len(manifest.toolkits) == before:
        return False
    save_manifest(path, manifest)
    return True


def get_pin(path: Path, name: str) -> Optional[ManifestEntry]:
    """Return the pin for ``name``, or ``None`` if not pinned (or file absent)."""
    if not path.exists():
        return None
    manifest = load_manifest(path)
    return manifest.find(name)
