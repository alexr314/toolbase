"""Serve defaults: ``~/.toolbase/serve.yaml`` (user) and
``<project>/.toolbase/serve.yaml`` (project).

This file is intentionally small. It carries only two things:

1. ``default.loadout`` — the name of the active loadout (which curated
   tool set ``tb serve`` exposes). This is the canonical way to choose
   the active loadout; ``tb loadout set-default`` and ``tb connect
   --loadout`` are conveniences that write it. The loadout *bodies*
   live one-file-per-loadout under ``loadouts/`` (see
   ``toolbase.serve.loadouts``), NOT here.

2. ``default.disabled`` — an absolute blocklist applied on top of any
   active loadout. Toolkits / tools listed here are never served, no
   matter what the active loadout says.

Two-layer resolution: the project-level ``serve.yaml`` (if present)
overrides the user-level one. ``default.loadout`` is project-wins;
the ``default.disabled`` lists are unioned (both layers block).

The loadout resolution chain and per-toolkit curation live in
``toolbase.serve.loadouts``; this module is just the serve.yaml I/O
plus the two-layer merge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from ..envs.paths import user_serve_config_path


# Back-compat alias for the user-level serve.yaml path. Production code
# should prefer ``user_serve_config_path()`` / ``project_serve_config_path()``
# from ``envs.paths``; this constant is kept for the ``tb serve config``
# command and tests that reference it directly.
SERVE_CONFIG_PATH = user_serve_config_path()


class ServeConfigError(Exception):
    """User-facing error during serve config load / resolution. Caller is
    expected to catch and render with the file path."""


@dataclass
class DefaultBlock:
    """Serve defaults: which loadout is active + absolute blocklists."""

    loadout: Optional[str] = None
    disabled_toolkits: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)  # "toolkit__tool"
    bare: bool = False  # serve un-namespaced <tool> names (default: <toolkit>__<tool>)

    def to_yaml_dict(self) -> dict:
        out: dict = {}
        if self.loadout:
            out["loadout"] = self.loadout
        if self.bare:
            out["bare"] = True
        disabled: dict = {}
        if self.disabled_toolkits:
            disabled["toolkits"] = list(self.disabled_toolkits)
        if self.disabled_tools:
            disabled["tools"] = list(self.disabled_tools)
        if disabled:
            out["disabled"] = disabled
        return out


@dataclass
class ServeConfig:
    """Top-level ``serve.yaml`` shape (defaults only)."""

    default: DefaultBlock = field(default_factory=DefaultBlock)

    def to_yaml_dict(self) -> dict:
        out: dict = {}
        d = self.default.to_yaml_dict()
        if d:
            out["default"] = d
        return out


def load_serve_config(path: Path = SERVE_CONFIG_PATH) -> ServeConfig:
    """Load a ``serve.yaml``. Returns an empty config if the file is missing.

    Raises ``ServeConfigError`` with a clear message (and path) if the file
    exists but is malformed. The caller should catch and surface; we never
    throw a yaml stack trace at the user.

    Rejects the retired ``groups:`` block with a pointer to the per-file
    loadout layout — clean cutover, no silent ignore.
    """
    if not path.exists():
        return ServeConfig()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ServeConfigError(f"could not parse {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ServeConfigError(f"{path} must be a YAML mapping at the top level")

    if "groups" in raw:
        raise ServeConfigError(
            f"{path}: the 'groups:' block was removed. Curated tool sets "
            "are now per-file loadouts under 'loadouts/<name>.yaml'. "
            "Set 'default.loadout:' here to choose the active one."
        )

    cfg = ServeConfig()

    default_raw = raw.get("default") or {}
    if not isinstance(default_raw, dict):
        raise ServeConfigError(f"{path}: 'default' must be a mapping")

    # ``default.profile`` is the pre-0.12 spelling. Read it when the
    # current key is absent so an existing serve.yaml keeps working;
    # anything we write back uses ``loadout``, so the file converts
    # itself the first time something sets the active loadout.
    loadout = default_raw.get("loadout", default_raw.get("profile"))
    if loadout is not None:
        if not isinstance(loadout, str) or not loadout:
            raise ServeConfigError(
                f"{path}: 'default.loadout' must be a non-empty string"
            )
        cfg.default.loadout = loadout

    bare = default_raw.get("bare", False)
    if not isinstance(bare, bool):
        raise ServeConfigError(
            f"{path}: 'default.bare' must be a boolean (true/false)"
        )
    cfg.default.bare = bare

    disabled_raw = default_raw.get("disabled") or {}
    if not isinstance(disabled_raw, dict):
        raise ServeConfigError(f"{path}: 'default.disabled' must be a mapping")
    cfg.default.disabled_toolkits = list(disabled_raw.get("toolkits") or [])
    cfg.default.disabled_tools = list(disabled_raw.get("tools") or [])

    return cfg


def save_serve_config(cfg: ServeConfig, path: Path = SERVE_CONFIG_PATH) -> None:
    """Write the config back to disk, ensuring the parent dir exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            cfg.to_yaml_dict(),
            f,
            sort_keys=False,
            default_flow_style=False,
        )


def merge_serve_configs(user: ServeConfig, project: ServeConfig) -> ServeConfig:
    """Two-layer merge: project overrides user.

    - ``default.loadout``: project wins; user falls through when the
      project doesn't set one.
    - ``default.disabled.toolkits`` / ``.tools``: union (both layers
      block; a global disable stays in effect even if a project doesn't
      repeat it).
    """
    merged = ServeConfig()
    merged.default.loadout = project.default.loadout or user.default.loadout
    # A mode toggle: on if either layer turns it on (the CLI flag is the
    # per-invocation override either way).
    merged.default.bare = project.default.bare or user.default.bare

    def _union(a: List[str], b: List[str]) -> List[str]:
        seen: set = set()
        out: List[str] = []
        for item in list(a) + list(b):
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    merged.default.disabled_toolkits = _union(
        user.default.disabled_toolkits, project.default.disabled_toolkits
    )
    merged.default.disabled_tools = _union(
        user.default.disabled_tools, project.default.disabled_tools
    )
    return merged


def _split_tool(qualified: str) -> Tuple[str, str]:
    """Split a "toolkit__tool" string. Errors clearly if malformed."""
    if "__" not in qualified:
        raise ServeConfigError(
            f"tool reference '{qualified}' must be in 'toolkit__tool' form"
        )
    toolkit, _, tool = qualified.partition("__")
    if not toolkit or not tool:
        raise ServeConfigError(
            f"tool reference '{qualified}' must be in 'toolkit__tool' form"
        )
    return toolkit, tool
