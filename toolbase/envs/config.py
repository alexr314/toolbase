"""
Two-layer per-toolkit config resolution.

Each toolkit's effective config is the merge of:

- *User layer*: ``~/.toolbase/config/<toolkit>.yaml``. User-facts that
  follow them across projects (API keys, data paths on this machine).
- *Project layer*: ``<project>/.toolbase/config/<toolkit>.yaml``.
  Project-scoped overrides (different opacity table per analysis, etc.).

Project wins key-by-key. Keys only in user survive. Keys only in project
are merged in. The ``schema_version`` envelope from either file is
stripped before the merge — it's a file-format concern, not a config
value, and serve/setup never inject it as state.

Phase 1 ships the resolver; Phase 4 wires it into the orchestrator's
``_resolve_state_config`` flow alongside the existing 3C contract
(NEEDS_VALUE sentinel, etc.). No CLI behavior changes here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .paths import project_config_path, user_config_path
from .schema import read_versioned_yaml


def load_user_config_layer(
    toolkit: str,
    *,
    user_base: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return the user-level config for ``toolkit`` as a plain dict.

    Returns ``{}`` if the file doesn't exist. ``schema_version`` is
    stripped (callers want only the actual fields).

    Reading is version-aware via ``read_versioned_yaml`` so legacy
    Phase 3C files (no schema_version) are accepted as v0 and migrated
    upward — currently identity, but the plumbing is in place.
    """
    path = user_config_path(toolkit, base=user_base)
    return _read_layer(path, "toolkit_config")


def load_project_config_layer(
    toolkit: str,
    project_root: Path,
) -> Dict[str, Any]:
    """Return the project-level config for ``toolkit`` as a plain dict.

    Returns ``{}`` if the project doesn't have a config file for this
    toolkit. ``schema_version`` is stripped.
    """
    path = project_config_path(project_root, toolkit)
    return _read_layer(path, "project_config")


def load_project_local_config_layer(
    toolkit: str,
    project_root: Path,
) -> Dict[str, Any]:
    # The project-local layer: config/<toolkit>.local.yaml next to the
    # committed project file. Project-scoped machine state (absolute
    # tool paths and the like), gitignored, highest precedence.
    # Returns {} if absent.
    path = project_config_path(project_root, toolkit)
    path = path.with_name(f"{toolkit}.local.yaml")
    return _read_layer(path, "project_config")


def resolve_toolkit_config(
    toolkit: str,
    project_root: Path,
    *,
    user_base: Optional[Path] = None,
) -> Dict[str, Any]:
    """Resolve a toolkit's effective config: user → project, project wins.

    Args:
        toolkit: toolkit name.
        project_root: discovered project root (real project or
            default-project — same data shape either way).
        user_base: test override for ``~/.toolbase/``.

    Returns:
        Merged dict of effective config values. Empty dict if neither
        layer has a file. ``schema_version`` is not included in the
        returned dict.

    Merge semantics:
        - Shallow per-key. Nested mappings under a single key get
          replaced wholesale, not deep-merged. This matches the
          existing Phase 3C config shape (flat ``{field: value}``).
          If a future field requires nested structure with
          per-subkey override semantics, that's a per-field decision
          to surface explicitly; not assumed here.
        - Project values override user values key-by-key.
        - Keys only in one layer survive intact.

    Note on Phase 3C ``<NEEDS VALUE>`` sentinels:
        The sentinel is just a string. If the user layer has
        ``api_key: "<NEEDS VALUE>"`` and the project layer has
        ``api_key: "real-key"``, the project wins and the sentinel
        is overridden. If both layers have the sentinel, the
        sentinel propagates and serve's existing 3C "config
        incomplete" gate fires. This is correct: the sentinel is
        data, just structured data, and the resolver doesn't need
        to special-case it.
    """
    user_data = load_user_config_layer(toolkit, user_base=user_base)
    project_data = load_project_config_layer(toolkit, project_root)
    local_data = load_project_local_config_layer(toolkit, project_root)

    merged: Dict[str, Any] = {}
    merged.update(user_data)
    merged.update(project_data)  # project wins over user key-by-key
    merged.update(local_data)    # project-local (machine state) wins overall
    return merged


# ── internals ───────────────────────────────────────────────────────


def _read_layer(path: Path, file_type: str) -> Dict[str, Any]:
    """Read one config layer, strip ``schema_version``, return plain dict.

    Missing file → ``{}``. Malformed file lets the underlying
    ``ValueError`` propagate (the resolver shouldn't paper over real
    parse failures; the user should see what's wrong).
    """
    if not path.exists():
        return {}
    raw = read_versioned_yaml(path, file_type, default={})
    return {k: v for k, v in raw.items() if k != "schema_version"}
