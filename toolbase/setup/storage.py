"""
File-canonical YAML storage for toolkit config.

Two-layer (Phase 4, 0.5.0):

- *User layer* (the legacy, default): ``~/.toolbase/config/<toolkit>.yaml``.
  Per-machine identity (API keys, data paths).
- *Project layer* (new): ``<project>/.toolbase/config/<toolkit>.yaml``.
  Per-project overrides; keys override the user layer key-by-key.

Every read / write function takes an optional ``layer`` argument
(``"user"`` or ``"project"``). When ``layer="project"``, a
``project_root`` must be supplied — there is no implicit default.
Backward compatibility: omitting ``layer`` resolves to ``"user"`` (the
0.4.x behavior).

This module is the read/write layer. It uses ``ruamel.yaml`` so user
comments survive ``set`` / ``unset`` round-trips — without that, every
``toolbase config set`` would silently strip the user's notes.

Resolver pattern (HANDOFF gotcha #12): functions take ``base:
Optional[Path] = None`` and resolve in-body via ``_resolve_config_dir()``,
which re-reads from the ``config`` module each call. This is what makes
test fixtures work — patching ``toolbase.config.CONFIG_DIR`` to a
tmp dir is enough to redirect the entire config-storage surface.
Don't "simplify" back to a bound default; the naive form silently
writes tests' config into the developer's real ``~/.toolbase/``.

File mode is ``0600`` because the file may contain secrets. (Project-
layer files use the same mode for consistency, even though they're
intended to be checked into git — if a user writes a secret to the
project layer, the file permissions are still tight.)

The exposed surface:

- ``config_dir()`` — the directory ``~/.toolbase/config/``.
- ``config_path(name, *, layer="user", project_root=None)`` — full path
  for one toolkit's config in the given layer.
- ``load_config(name, *, layer="user", project_root=None)`` — read.
- ``save_config(name, data, *, layer="user", project_root=None)`` — write.
- ``set_config_value(name, key, value, *, layer="user", project_root=None)``.
- ``unset_config_value(name, key, *, layer="user", project_root=None)``.
- ``delete_config(name, *, layer="user", project_root=None)`` — remove file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .. import config as _config_mod


# Current schema version stamped on every saved config file (Phase 4,
# 0.5.0). Read-time refusal of newer files is delegated to
# ``envs.schema.read_versioned_yaml`` via the same constant.
_SCHEMA_VERSION = 1
_USER_FILE_TYPE = "toolkit_config"
_PROJECT_FILE_TYPE = "project_config"


def _file_type_for(layer: str) -> str:
    if layer == "user":
        return _USER_FILE_TYPE
    if layer in ("project", "local"):
        # The project-local layer shares the project file format — it's
        # the same shape of file, just gitignored machine state.
        return _PROJECT_FILE_TYPE
    raise ValueError(
        f"unknown config layer {layer!r} "
        "(expected 'user', 'project', or 'local')")


# ruamel YAML instance, configured for our use case.
#
# - typ="rt" is round-trip mode (preserves comments + ordering).
# - default_flow_style=False forces block style (one key per line),
#   which is what users expect to see and edit.
# - allow_unicode keeps non-ASCII strings legible.
def _new_yaml() -> YAML:
    y = YAML(typ="rt")
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 1000  # don't auto-wrap long values; they read worse wrapped
    y.preserve_quotes = True
    return y


_yaml = _new_yaml()


# ── path resolution ──────────────────────────────────────────────────


def _resolve_config_dir() -> Path:
    """Get the current ~/.toolbase/config/ — re-reading at call time.

    Honors test monkeypatching of ``toolbase.config.CONFIG_DIR``.
    See HANDOFF.md gotcha #12 for the full rationale.
    """
    return _config_mod.CONFIG_DIR / "config"


def config_dir(*, base: Optional[Path] = None) -> Path:
    """Return the per-toolkit user-level config directory.

    Pure path resolution — does NOT create the directory on disk. Writers
    (``save_config`` etc.) create parents lazily at write time. This keeps
    ``config_path``-style queries from leaving empty directories behind
    when no config has been written yet.

    Always resolves the *user* layer. Use ``project_config_dir`` (or
    ``config_path(..., layer='project', project_root=...)``) for the
    project layer.
    """
    if base is None:
        base = _resolve_config_dir()
    return base


def project_config_dir(project_root: Path) -> Path:
    """Return ``<project_root>/.toolbase/config/``.

    Pure path resolution — does NOT create the directory. Same rationale
    as ``config_dir``: callers that resolve paths shouldn't leave empty
    dirs behind; writers create parents themselves.

    Handles the default-project special-case via ``envs.paths``.
    """
    from ..envs.paths import _is_default_project
    if _is_default_project(project_root):
        return project_root / "config"
    return project_root / ".toolbase" / "config"


def config_path(
    toolkit_name: str,
    *,
    base: Optional[Path] = None,
    layer: str = "user",
    project_root: Optional[Path] = None,
) -> Path:
    """Path to ``<config_dir>/<toolkit>.yaml`` for the requested layer.

    - ``layer="user"`` (default; backward compatible): the
      ``~/.toolbase/config/<toolkit>.yaml`` path.
    - ``layer="project"``: the ``<project>/.toolbase/config/<toolkit>.yaml``
      path. Requires ``project_root`` to be supplied.

    Does not create the file.
    """
    if layer == "user":
        return config_dir(base=base) / f"{toolkit_name}.yaml"
    if layer in ("project", "local"):
        # "local" = <project>/.toolbase/config/<toolkit>.local.yaml —
        # project-scoped machine state (absolute tool paths and the
        # like), gitignored, merged over the committed project layer.
        if project_root is None:
            raise ValueError(
                f"config_path(layer={layer!r}) requires project_root"
            )
        suffix = ".local.yaml" if layer == "local" else ".yaml"
        return project_config_dir(project_root) / f"{toolkit_name}{suffix}"
    raise ValueError(
        f"unknown config layer {layer!r} "
        "(expected 'user', 'project', or 'local')")


# ── read / write ─────────────────────────────────────────────────────


def load_config(
    toolkit_name: str,
    *,
    base: Optional[Path] = None,
    layer: str = "user",
    project_root: Optional[Path] = None,
) -> CommentedMap:
    """Read ``<toolkit>.yaml`` into a ruamel ``CommentedMap``.

    Returns an empty ``CommentedMap`` if the file doesn't exist (so
    callers can ``data["foo"] = "bar"`` without checking existence
    first). Returns an empty ``CommentedMap`` for an empty file too.

    Raises ``OSError`` only on actual read failures (permission, etc.).
    Malformed YAML is converted to a clear ``ValueError`` so callers
    don't have to import ruamel exception classes.

    Also refuses files whose ``schema_version`` exceeds what this build
    understands (``envs.schema.SchemaTooNewError``).
    """
    path = config_path(
        toolkit_name, base=base, layer=layer, project_root=project_root,
    )
    if not path.exists():
        return CommentedMap()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _new_yaml().load(f)  # fresh instance: YAML() is not thread-safe
    except Exception as e:
        raise ValueError(
            f"failed to parse {path}: {e}"
        ) from e
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        # Could happen if the file's top-level is a list or scalar.
        # For toolkit config, that's a malformation — fields live at
        # the top level as a mapping.
        raise ValueError(
            f"{path}: expected a YAML mapping at the top level, got "
            f"{type(data).__name__}"
        )
    # Schema-version sanity check. Missing field → assume legacy v0,
    # accept silently. Newer than we know → refuse.
    from ..envs.schema import MAX_SCHEMA_VERSION, SchemaTooNewError
    file_type = _file_type_for(layer)
    max_known = MAX_SCHEMA_VERSION[file_type]
    sv = data.get("schema_version", 0)
    if isinstance(sv, int) and sv > max_known:
        raise SchemaTooNewError(path, file_type, sv, max_known)
    return data


def save_config(
    toolkit_name: str,
    data: Dict[str, Any],
    *,
    base: Optional[Path] = None,
    header_comment: Optional[str] = None,
    layer: str = "user",
    project_root: Optional[Path] = None,
) -> Path:
    """Write ``data`` to ``<toolkit>.yaml`` atomically with mode 0600.

    If ``data`` is a ``CommentedMap`` that came from ``load_config``,
    its comments and ordering are preserved through the round-trip.
    Plain dicts are written as fresh files (no comments to preserve;
    if the file existed, the previous comments are lost — this is the
    user's choice when they pass a plain dict).

    ``header_comment`` is prepended above the YAML body if the file
    doesn't already have a top-level comment. Useful for
    ``run_install_setup`` to seed a freshly-written file with a
    "this file is canonical, edit anytime" pointer.

    The current ``schema_version`` is stamped on every write (Phase 4,
    0.5.0). If the data lacks the field, it's inserted at the top.

    Atomic-write: write to ``<path>.tmp`` then ``os.replace``. Stops
    a Ctrl-C-interrupted write from leaving a partial file.
    """
    path = config_path(
        toolkit_name, base=base, layer=layer, project_root=project_root,
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    # If a header comment was requested and the data has no leading
    # comment, attach one. Convert plain dicts to CommentedMap first so
    # the comment-attachment hook is usable. We skip duplication if the
    # caller has already loaded-and-mutated a file that has the same
    # comment — checking the rendered string is cheaper than poking at
    # ruamel's comment internals across versions.
    if header_comment:
        if not isinstance(data, CommentedMap):
            converted = CommentedMap()
            for k, v in data.items():
                converted[k] = v
            data = converted
        # Strip trailing newline from the user-provided header so we
        # don't end up with a blank line between the comment and the
        # body.
        comment_text = header_comment.rstrip("\n")
        try:
            data.yaml_set_start_comment(comment_text)
        except Exception:
            # ruamel's start-comment API has been version-stable but
            # don't crash if a future major changes the signature.
            pass

    # Stamp schema_version: 1 if not already present. We keep it at the
    # top of the map for readability. ruamel preserves dict order, so
    # inserting at position 0 puts it first in the rendered file.
    if not isinstance(data, CommentedMap):
        converted = CommentedMap()
        if "schema_version" not in data:
            converted["schema_version"] = _SCHEMA_VERSION
        for k, v in data.items():
            converted[k] = v
        data = converted
    elif "schema_version" not in data:
        # CommentedMap supports insert(index, key, value) to put the
        # field at the top of the map.
        try:
            data.insert(0, "schema_version", _SCHEMA_VERSION)
        except Exception:
            data["schema_version"] = _SCHEMA_VERSION

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _new_yaml().dump(data, f)  # fresh instance: YAML() is not thread-safe
    os.replace(tmp, path)

    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):  # pragma: no cover (Windows)
        pass
    return path


def delete_config(
    toolkit_name: str,
    *,
    base: Optional[Path] = None,
    layer: str = "user",
    project_root: Optional[Path] = None,
) -> bool:
    """Delete the toolkit's config file. Returns True if a file was removed."""
    path = config_path(
        toolkit_name, base=base, layer=layer, project_root=project_root,
    )
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


# ── single-field mutators (used by `toolbase config set/unset`) ──


def set_config_value(
    toolkit_name: str,
    key: str,
    value: Any,
    *,
    base: Optional[Path] = None,
    layer: str = "user",
    project_root: Optional[Path] = None,
) -> Path:
    """Set one field; preserve every other field and all comments.

    Read → mutate → write, all in this function so the caller doesn't
    have to worry about losing state by re-saving stale data.
    """
    data = load_config(
        toolkit_name, base=base, layer=layer, project_root=project_root,
    )
    data[key] = value
    return save_config(
        toolkit_name, data, base=base, layer=layer, project_root=project_root,
    )


def unset_config_value(
    toolkit_name: str,
    key: str,
    *,
    base: Optional[Path] = None,
    layer: str = "user",
    project_root: Optional[Path] = None,
) -> bool:
    """Remove one field. Returns True if the key existed."""
    data = load_config(
        toolkit_name, base=base, layer=layer, project_root=project_root,
    )
    if key not in data:
        return False
    del data[key]
    save_config(
        toolkit_name, data, base=base, layer=layer, project_root=project_root,
    )
    return True
