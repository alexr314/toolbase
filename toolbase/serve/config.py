"""Serve defaults: ``~/.toolbase/serve.yaml`` (user) and
``<project>/.toolbase/serve.yaml`` (project).

This file is intentionally small. It carries only two things:

1. ``default.profile`` — the name of the active profile (which curated
   tool set ``tb serve`` exposes). This is the canonical way to choose
   the active profile; ``tb profile set-default`` and ``tb connect
   --profile`` are conveniences that write it. The profile *bodies*
   live one-file-per-profile under ``profiles/`` (see
   ``toolbase.serve.profiles``), NOT here.

2. ``default.disabled`` — an absolute blocklist applied on top of any
   active profile. Toolkits / tools listed here are never served, no
   matter what the active profile says.

Two-layer resolution: the project-level ``serve.yaml`` (if present)
overrides the user-level one. ``default.profile`` is project-wins;
the ``default.disabled`` lists are unioned (both layers block).

The profile resolution chain and per-toolkit curation live in
``toolbase.serve.profiles``; this module is just the serve.yaml I/O
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
    """Serve defaults: which profile is active + absolute blocklists."""

    profile: Optional[str] = None
    disabled_toolkits: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)  # "toolkit__tool"

    def to_yaml_dict(self) -> dict:
        out: dict = {}
        if self.profile:
            out["profile"] = self.profile
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
    profile layout — clean cutover, no silent ignore.
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
            "are now per-file profiles under 'profiles/<name>.yaml'. "
            "Set 'default.profile:' here to choose the active one."
        )

    cfg = ServeConfig()

    default_raw = raw.get("default") or {}
    if not isinstance(default_raw, dict):
        raise ServeConfigError(f"{path}: 'default' must be a mapping")

    profile = default_raw.get("profile")
    if profile is not None:
        if not isinstance(profile, str) or not profile:
            raise ServeConfigError(
                f"{path}: 'default.profile' must be a non-empty string"
            )
        cfg.default.profile = profile

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

    - ``default.profile``: project wins; user falls through when the
      project doesn't set one.
    - ``default.disabled.toolkits`` / ``.tools``: union (both layers
      block; a global disable stays in effect even if a project doesn't
      repeat it).
    """
    merged = ServeConfig()
    merged.default.profile = project.default.profile or user.default.profile

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
