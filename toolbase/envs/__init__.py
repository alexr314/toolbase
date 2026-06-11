"""
Environments and scoping for Toolbase (0.5.0).

Implements the cache-plus-manifest model described in
``docs/ENVIRONMENTS_DESIGN.md``:

- A *cache* at ``~/.toolbase/cache/<name>/<version>/`` holds installed
  toolkit binaries (venv / conda env / Docker refs). Multi-version
  side-by-side. Regenerable from a project manifest.

- *Projects* own the manifest at ``<project>/.toolbase/manifest.yaml``.
  Pinned toolkit list (name + version + pinned_at). Small, checked into
  git. Two-layer per-toolkit config layers user-level under project-level.

- A *default-project* fallback at ``~/.toolbase/default-project/``
  catches users who run ``tb install`` from a directory with no
  ``.toolbase/`` anywhere upward.

This package is the substrate. Phase 1 ships pure-functional paths,
discovery walk, schema-versioning plumbing, manifest read/write, and
two-layer config resolution. Phase 2 wires install/uninstall/serve/list
onto the new cache layout. Phase 3 wires real project discovery.

Public surface (subset; full list in submodules):

- ``cache_dir(name, version)``
- ``user_config_path(toolkit)``
- ``project_root_or_default(cwd=..., override=...)``
- ``project_manifest_path(project_root)``
- ``project_config_path(project_root, toolkit)``
- ``default_project_root()``
- ``read_versioned_yaml(path, file_type)``
- ``write_versioned_yaml(path, file_type, data, current_version=None)``
- ``register_migration(file_type, from_v, to_v, fn)``
- ``SchemaTooNewError``
- ``Manifest``, ``ManifestEntry``
- ``load_manifest(path)`` / ``save_manifest(path, manifest)``
- ``add_pin``, ``remove_pin``, ``get_pin``
- ``resolve_toolkit_config(toolkit, project_root, user_base=..., ...)``
"""

from __future__ import annotations

from .paths import (
    cache_dir,
    cache_root,
    user_config_path,
    project_manifest_path,
    project_config_path,
    default_project_root,
    legacy_toolkits_dir,
)
from .discovery import (
    project_root_or_default,
    find_project_root,
)
from .schema import (
    SchemaTooNewError,
    MAX_SCHEMA_VERSION,
    read_versioned_yaml,
    write_versioned_yaml,
    register_migration,
    clear_registry,
)
from .manifest import (
    Manifest,
    ManifestEntry,
    load_manifest,
    local_manifest_path,
    load_merged_pins,
    save_manifest,
    add_pin,
    remove_pin,
    get_pin,
)
from .config import (
    resolve_toolkit_config,
    load_user_config_layer,
    load_project_config_layer,
)
from .cache import (
    CacheEntry,
    INSTALL_META_FILE,
    LAST_USED_FILE,
    DISK_SIZE_FILE,
    LEGACY_META_FILE,
    write_install_meta,
    read_install_meta,
    write_legacy_meta,
    read_legacy_meta,
    touch_last_used,
    read_last_used,
    compute_and_write_disk_size,
    read_disk_size,
    walk_cache,
    list_versions,
    find_slot,
)


__all__ = [
    # paths
    "cache_dir",
    "cache_root",
    "user_config_path",
    "project_manifest_path",
    "project_config_path",
    "default_project_root",
    "legacy_toolkits_dir",
    # discovery
    "project_root_or_default",
    "find_project_root",
    # schema
    "SchemaTooNewError",
    "MAX_SCHEMA_VERSION",
    "read_versioned_yaml",
    "write_versioned_yaml",
    "register_migration",
    "clear_registry",
    # manifest
    "Manifest",
    "ManifestEntry",
    "load_manifest",
    "save_manifest",
    "add_pin",
    "remove_pin",
    "get_pin",
    # config layers
    "resolve_toolkit_config",
    "load_user_config_layer",
    "load_project_config_layer",
    # cache slot metadata
    "CacheEntry",
    "INSTALL_META_FILE",
    "LAST_USED_FILE",
    "DISK_SIZE_FILE",
    "LEGACY_META_FILE",
    "write_install_meta",
    "read_install_meta",
    "write_legacy_meta",
    "read_legacy_meta",
    "touch_last_used",
    "read_last_used",
    "compute_and_write_disk_size",
    "read_disk_size",
    "walk_cache",
    "list_versions",
    "find_slot",
]
