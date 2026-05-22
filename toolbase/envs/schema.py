"""
Schema versioning plumbing for every YAML file Toolbase writes from 0.5.0
forward.

Every file we write carries ``schema_version: <int>`` at the top. On read:

- If a file's ``schema_version`` is missing, we assume ``0`` (legacy
  layer, pre-0.5.0 files like Phase 3C's ``~/.toolbase/config/<toolkit>.yaml``).
- If ``schema_version`` is in range ``[0, MAX_SCHEMA_VERSION[file_type]]``,
  we read the file, then run any registered migrations from that version
  forward to the current ``MAX`` for that file type.
- If ``schema_version > MAX``, we refuse with ``SchemaTooNewError`` —
  "your toolbase is older than this config file."

The migration framework ships **empty** in 0.5.0. The pattern is in
place so 0.6.0 (or later) can register the first non-trivial migration
without rewriting how files are read.

File modes are NOT set here; ``write_versioned_yaml`` writes atomically
and the caller's storage layer (the existing ``setup/storage.py``
pattern) is the right place to chmod 0600.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


# Tracked file types. Each entry's value is the *current* maximum
# version this build of Toolbase knows how to read. Any file
# carrying a higher ``schema_version`` is refused at read time.
#
# Bumping a number here is the "I just shipped a migration" gesture.
# Don't bump without also calling ``register_migration`` for the
# new version transition (or a unit test will catch the gap).
MAX_SCHEMA_VERSION: Dict[str, int] = {
    "toolkit_config": 1,
    "project_manifest": 1,
    "project_config": 1,
    "install_meta": 1,
}


class SchemaTooNewError(Exception):
    """Raised when a file's ``schema_version`` exceeds what we can read.

    Message format is user-facing — tests assert on its shape.
    """

    def __init__(self, path: Path, file_type: str, file_version: int, max_known: int):
        self.path = path
        self.file_type = file_type
        self.file_version = file_version
        self.max_known = max_known
        super().__init__(
            f"{path}: schema_version={file_version} (file_type={file_type!r}) "
            f"exceeds this toolbase's max known version ({max_known}). "
            "Your toolbase is older than this config file; upgrade or "
            "re-run from a machine with the right version."
        )


# ── migration registry ─────────────────────────────────────────────


# Keyed by (file_type, from_version). Each value is a callable taking
# the parsed dict and returning a transformed dict for ``from_version + 1``.
# Chain at read time: keep applying migrations until we reach
# ``MAX_SCHEMA_VERSION[file_type]``.
_MigrationFn = Callable[[Dict[str, Any]], Dict[str, Any]]
_MIGRATIONS: Dict[Tuple[str, int], _MigrationFn] = {}


def register_migration(
    file_type: str,
    from_v: int,
    to_v: int,
    fn: _MigrationFn,
) -> None:
    """Register a dict-transform migration for a file type.

    ``from_v`` and ``to_v`` must satisfy ``to_v == from_v + 1`` —
    migrations are chained one step at a time. This is intentional;
    multi-version skip-migrations would let bugs in one migration
    silently bypass intermediate fix-ups.

    The framework ships **empty** in 0.5.0; this function exists so
    later versions can register the first real migration without
    touching the read path.
    """
    if to_v != from_v + 1:
        raise ValueError(
            f"register_migration: to_v must be from_v+1 "
            f"(got from_v={from_v}, to_v={to_v})"
        )
    if from_v < 0:
        raise ValueError(f"register_migration: from_v must be >= 0 (got {from_v})")
    key = (file_type, from_v)
    if key in _MIGRATIONS:
        raise ValueError(
            f"register_migration: {file_type!r} v{from_v}→v{to_v} already "
            "registered. Migrations are global and one-shot per (type, from)."
        )
    _MIGRATIONS[key] = fn


def clear_registry() -> None:
    """Remove all registered migrations. Test-only — production never calls."""
    _MIGRATIONS.clear()


# ── YAML helpers ────────────────────────────────────────────────────


def _new_yaml() -> YAML:
    """Round-trip ruamel YAML loader configured for Toolbase defaults.

    Round-trip mode preserves comments and ordering through
    ``set/unset`` cycles. Block style is enforced because that's
    what users edit by hand.
    """
    y = YAML(typ="rt")
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 1000
    y.preserve_quotes = True
    return y


_yaml = _new_yaml()


# ── read path ───────────────────────────────────────────────────────


def read_versioned_yaml(
    path: Path,
    file_type: str,
    *,
    default: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read a YAML file, run migrations, return a dict at the current version.

    Args:
        path: file on disk.
        file_type: one of the keys in ``MAX_SCHEMA_VERSION``. Used both
            to dispatch migrations and to refuse files newer than this
            build understands.
        default: dict to return if the file doesn't exist. ``None``
            (the default) returns ``{"schema_version": MAX_SCHEMA_VERSION[file_type]}``.

    Raises:
        ``SchemaTooNewError`` if the file's ``schema_version`` exceeds
        ``MAX_SCHEMA_VERSION[file_type]``.
        ``ValueError`` if the file is malformed (parse failed, top-level
        wasn't a mapping).
        ``KeyError`` if ``file_type`` isn't in ``MAX_SCHEMA_VERSION``.

    Notes:
        - Missing ``schema_version`` field → assumed 0 (legacy).
        - The returned dict carries ``schema_version`` set to the
          post-migration value. Callers that don't care about the
          version can ignore it; callers that do care can use it as
          the field's source of truth.
    """
    if file_type not in MAX_SCHEMA_VERSION:
        raise KeyError(
            f"unknown file_type {file_type!r}; expected one of "
            f"{sorted(MAX_SCHEMA_VERSION)}"
        )
    max_known = MAX_SCHEMA_VERSION[file_type]

    if not path.exists():
        if default is None:
            return {"schema_version": max_known}
        out = dict(default)
        out.setdefault("schema_version", max_known)
        return out

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.load(f)
    except Exception as e:
        raise ValueError(f"failed to parse {path}: {e}") from e

    if data is None:
        # Empty file; treat as "no fields set yet."
        return {"schema_version": max_known}

    if not isinstance(data, dict):
        raise ValueError(
            f"{path}: expected a YAML mapping at the top level, got "
            f"{type(data).__name__}"
        )

    file_version = data.get("schema_version", 0)
    if not isinstance(file_version, int):
        raise ValueError(
            f"{path}: schema_version must be an integer, got "
            f"{type(file_version).__name__} ({file_version!r})"
        )
    if file_version < 0:
        raise ValueError(
            f"{path}: schema_version must be >= 0 (got {file_version})"
        )
    if file_version > max_known:
        raise SchemaTooNewError(path, file_type, file_version, max_known)

    # Convert the ruamel CommentedMap (or whatever ruamel returned) to
    # a plain dict for downstream callers. Migrations operate on plain
    # dicts; the round-trip layer in storage.py handles comment
    # preservation separately at write time.
    plain: Dict[str, Any] = {}
    for k, v in data.items():
        plain[k] = v

    # Chain registered migrations up to max_known. Missing migrations
    # at a given step are an identity transform (the field shape is
    # assumed unchanged) — this is what the brief calls the
    # "empty migration framework" behavior. Once a real migration is
    # registered, the chain runs it.
    current = file_version
    while current < max_known:
        fn = _MIGRATIONS.get((file_type, current))
        if fn is not None:
            plain = fn(plain)
        current += 1

    plain["schema_version"] = max_known
    return plain


# ── write path ──────────────────────────────────────────────────────


def write_versioned_yaml(
    path: Path,
    file_type: str,
    data: Dict[str, Any],
    *,
    current_version: Optional[int] = None,
    header_comment: Optional[str] = None,
    mode: int = 0o600,
) -> Path:
    """Atomically write ``data`` to ``path`` with a ``schema_version`` field.

    Args:
        path: file to write.
        file_type: one of the keys in ``MAX_SCHEMA_VERSION``.
        data: dict to serialize. May or may not already carry
            ``schema_version``; the function sets it to
            ``current_version`` (or ``MAX_SCHEMA_VERSION[file_type]``)
            before writing.
        current_version: which schema version to stamp on the file.
            Defaults to ``MAX_SCHEMA_VERSION[file_type]``. Tests can
            pass an explicit value to write a back-versioned file
            (e.g. to exercise the migration path).
        header_comment: optional comment prepended above the YAML
            body. Useful for "this file is canonical, edit anytime"
            pointers.
        mode: filesystem permission bits. Defaults to 0o600 because
            config files may carry secrets. Pass 0o644 explicitly for
            non-sensitive files (manifests in a project's git tree).

    Returns:
        The path that was written (same as the ``path`` argument; for
        chaining).

    Atomicity:
        Write to ``<path>.tmp`` then ``os.replace``. A Ctrl-C between
        steps leaves either the old file or no file, never a partial one.

    Comment preservation:
        If ``data`` is a ruamel ``CommentedMap`` carrying comments
        (e.g. from a previous ``read_versioned_yaml`` round-trip), the
        comments survive the write. Plain dicts are written fresh.
    """
    if file_type not in MAX_SCHEMA_VERSION:
        raise KeyError(
            f"unknown file_type {file_type!r}; expected one of "
            f"{sorted(MAX_SCHEMA_VERSION)}"
        )

    if current_version is None:
        current_version = MAX_SCHEMA_VERSION[file_type]
    if current_version < 0:
        raise ValueError(
            f"current_version must be >= 0 (got {current_version})"
        )
    if current_version > MAX_SCHEMA_VERSION[file_type]:
        raise ValueError(
            f"current_version={current_version} exceeds "
            f"MAX_SCHEMA_VERSION[{file_type!r}]={MAX_SCHEMA_VERSION[file_type]}; "
            "bump the registry first"
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    # Preserve CommentedMap-ness if the caller passed one in (so
    # comments survive a load → mutate → save round trip).
    if isinstance(data, CommentedMap):
        to_write = data
    else:
        to_write = CommentedMap()
        for k, v in data.items():
            to_write[k] = v

    # Stamp schema_version at the top. ruamel preserves insertion
    # order, so re-inserting puts it where it landed on first write
    # for new files (top); for round-tripped files it stays wherever
    # it was, which is fine.
    to_write["schema_version"] = current_version

    if header_comment:
        try:
            to_write.yaml_set_start_comment(header_comment.rstrip("\n"))
        except Exception:
            # ruamel's start-comment API has been version-stable but
            # don't crash if a future major changes the signature.
            pass

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _yaml.dump(to_write, f)
    os.replace(tmp, path)

    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):  # pragma: no cover (Windows)
        pass
    return path


# Type alias for the migrations dict, exposed for tests.
__all__ = [
    "SchemaTooNewError",
    "MAX_SCHEMA_VERSION",
    "register_migration",
    "clear_registry",
    "read_versioned_yaml",
    "write_versioned_yaml",
]
