"""Read/mutate/write profile files for ``tb activate`` / ``tb deactivate``
and ``tb profile create``.

Uses ruamel round-trip mode so user comments and ordering survive
mutation (same discipline as ``setup/storage.py`` for ``<toolkit>.yaml``).

The casual-tier commands (``tb activate`` / ``tb deactivate``) operate on
the **default** profile in the chosen scope. ``<item>`` is one of:

- ``<toolkit>``            -> whole toolkit
- ``<toolkit>/<bundle>``   -> one bundle (slash = drill into the toolkit)
- ``<toolkit>__<tool>``    -> one tool (double underscore = MCP tool name)

Mutations are profile-file edits with documented, shallow semantics
(they don't consult the toolkit's actual tool/bundle list). The
orchestrator and ``tb list`` are where a selection is expanded against
real tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from ..envs.paths import (
    default_project_root,
    project_profiles_dir,
    user_profiles_dir,
)


_DEFAULT_HEADER = (
    "Profile: default\n"
    "Toolkits below are what the agent sees. Edit with tb activate /\n"
    "tb deactivate, or by hand. Run `tb profile tools` to see available\n"
    "bundles + tools per toolkit.\n"
)


def _new_yaml() -> YAML:
    y = YAML(typ="rt")
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 1000
    y.preserve_quotes = True
    return y


class ProfileItemError(ValueError):
    """Malformed ``<item>`` reference passed to activate/deactivate."""


@dataclass
class MutationResult:
    changed: bool
    message: str
    path: Path


# ── item parsing ─────────────────────────────────────────────────────


def parse_item(item: str) -> Tuple[str, str, Optional[str]]:
    """Parse an activate/deactivate item into ``(kind, toolkit, sub)``.

    ``kind`` is ``"toolkit"`` | ``"bundle"`` | ``"tool"``; ``sub`` is the
    bundle / tool name (None for toolkit-granularity).
    """
    if "/" in item and "__" in item:
        raise ProfileItemError(
            f"'{item}': use either '<toolkit>/<bundle>' or "
            "'<toolkit>__<tool>', not both."
        )
    if "/" in item:
        tk, _, bundle = item.partition("/")
        if not tk or not bundle:
            raise ProfileItemError(
                f"'{item}': bundle form must be '<toolkit>/<bundle>'."
            )
        return "bundle", tk, bundle
    if "__" in item:
        tk, _, tool = item.partition("__")
        if not tk or not tool:
            raise ProfileItemError(
                f"'{item}': tool form must be '<toolkit>__<tool>'."
            )
        return "tool", tk, tool
    if not item:
        raise ProfileItemError("empty item reference")
    return "toolkit", item, None


# ── scope -> default profile path ────────────────────────────────────


def default_profile_path(
    scope: str,
    project_root: Optional[Path] = None,
    *,
    user_base: Optional[Path] = None,
) -> Path:
    """Return ``<scope>/.toolbase/profiles/default.yaml``.

    ``scope`` is ``"user"`` or ``"project"``. Project scope requires
    ``project_root`` (the default-project root is used as a fallback by
    callers that resolve it).
    """
    if scope == "user":
        return user_profiles_dir(base=user_base) / "default.yaml"
    if scope == "project":
        root = project_root if project_root is not None else default_project_root(base=user_base)
        return project_profiles_dir(root) / "default.yaml"
    raise ValueError(f"unknown scope {scope!r}")


# ── load / save ──────────────────────────────────────────────────────


def _load(path: Path) -> CommentedMap:
    """Load a profile file as a round-trippable mapping. Returns a fresh
    scaffold (with header + empty ``toolkits:``) when the file is absent."""
    if not path.exists():
        data = CommentedMap()
        data["toolkits"] = CommentedMap()
        data.yaml_set_start_comment(_DEFAULT_HEADER)
        return data
    y = _new_yaml()
    with open(path, "r", encoding="utf-8") as f:
        data = y.load(f)
    if data is None:
        data = CommentedMap()
    if "toolkits" not in data or data["toolkits"] is None:
        data["toolkits"] = CommentedMap()
    return data


def _save(path: Path, data: CommentedMap) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    y = _new_yaml()
    with open(path, "w", encoding="utf-8") as f:
        y.dump(data, f)


def _ensure_toolkit(toolkits: CommentedMap, name: str) -> CommentedMap:
    """Return the toolkit's selection map, creating an empty one if absent."""
    entry = toolkits.get(name)
    if entry is None:
        entry = CommentedMap()
        toolkits[name] = entry
    return entry


# ── activate ─────────────────────────────────────────────────────────


def activate(
    item: str,
    *,
    scope: str,
    project_root: Optional[Path] = None,
    user_base: Optional[Path] = None,
) -> MutationResult:
    """Activate a toolkit / bundle / tool in the scope's default profile."""
    kind, tk, sub = parse_item(item)
    path = default_profile_path(scope, project_root, user_base=user_base)
    data = _load(path)
    toolkits: CommentedMap = data["toolkits"]

    if kind == "toolkit":
        if tk in toolkits:
            existing = toolkits[tk]
            if not existing:
                return MutationResult(False, f"{tk} is already active (whole toolkit).", path)
            return MutationResult(
                False,
                f"{tk} is already active (curated). Use 'tb deactivate {tk}' "
                "then re-activate to reset to the whole toolkit.",
                path,
            )
        toolkits[tk] = CommentedMap()
        _save(path, data)
        return MutationResult(True, f"Activated {tk} (whole toolkit).", path)

    entry = _ensure_toolkit(toolkits, tk)

    if kind == "bundle":
        bundles = entry.get("bundles")
        if bundles is None:
            entry["bundles"] = [sub]
            _save(path, data)
            return MutationResult(
                True, f"Activated {tk}/{sub} ({tk} now restricted to bundles: [{sub}]).", path,
            )
        if sub in bundles:
            return MutationResult(False, f"{tk}/{sub} is already active.", path)
        bundles.append(sub)
        _save(path, data)
        return MutationResult(True, f"Activated {tk}/{sub}.", path)

    # kind == "tool"
    tools = entry.get("tools")
    if tools is None:
        tools = CommentedMap()
        entry["tools"] = tools
    disabled = tools.get("disabled") or []
    enabled = tools.get("enabled")
    changed = False
    if sub in disabled:
        disabled.remove(sub)
        if not disabled:
            del tools["disabled"]
        changed = True
    if enabled is None:
        tools["enabled"] = [sub]
        changed = True
    elif sub not in enabled:
        enabled.append(sub)
        changed = True
    if not changed:
        return MutationResult(False, f"{tk}__{sub} is already active.", path)
    _save(path, data)
    return MutationResult(True, f"Activated {tk}__{sub}.", path)


# ── deactivate ───────────────────────────────────────────────────────


def deactivate(
    item: str,
    *,
    scope: str,
    project_root: Optional[Path] = None,
    user_base: Optional[Path] = None,
) -> MutationResult:
    """Deactivate a toolkit / bundle / tool in the scope's default profile."""
    kind, tk, sub = parse_item(item)
    path = default_profile_path(scope, project_root, user_base=user_base)
    data = _load(path)
    toolkits: CommentedMap = data["toolkits"]

    if tk not in toolkits and kind != "toolkit":
        return MutationResult(False, f"{tk} is not in the profile; nothing to deactivate.", path)

    if kind == "toolkit":
        if tk not in toolkits:
            return MutationResult(False, f"{tk} is not active; nothing to deactivate.", path)
        del toolkits[tk]
        _save(path, data)
        return MutationResult(True, f"Deactivated {tk} (removed from profile).", path)

    # tk is guaranteed present here (the kind != "toolkit" guard above
    # returned early when absent). Use the stored entry directly — never
    # ``get(tk) or {}``, which would detach an empty ({}) entry from the
    # tree and silently drop the mutation.
    entry = toolkits[tk]
    if entry is None:  # yaml ``heptapod:`` with no value
        entry = CommentedMap()
        toolkits[tk] = entry

    if kind == "bundle":
        bundles = entry.get("bundles")
        if not bundles or sub not in bundles:
            return MutationResult(False, f"{tk}/{sub} is not active; nothing to deactivate.", path)
        bundles.remove(sub)
        _save(path, data)
        return MutationResult(
            True, f"Deactivated {tk}/{sub}." + (
                f" ({tk} now serves no bundles.)" if not bundles else ""
            ), path,
        )

    # kind == "tool"
    tools = entry.get("tools")
    if tools is not None:
        enabled = tools.get("enabled")
        if enabled is not None and sub in enabled:
            enabled.remove(sub)
            if not enabled:
                del tools["enabled"]
            if not tools:
                del entry["tools"]
            _save(path, data)
            return MutationResult(True, f"Deactivated {tk}__{sub} (removed from enabled).", path)
        if (tools.get("disabled") or []) and sub in tools["disabled"]:
            return MutationResult(False, f"{tk}__{sub} is already deactivated.", path)

    # Not explicitly enabled -> it's served via a bundle or whole-toolkit;
    # add to the disabled blocklist so it stops being served.
    if entry.get("tools") is None:
        entry["tools"] = CommentedMap()
    tools_block = entry["tools"]
    if tools_block.get("disabled") is None:
        tools_block["disabled"] = [sub]
    else:
        tools_block["disabled"].append(sub)
    _save(path, data)
    return MutationResult(True, f"Deactivated {tk}__{sub} (added to disabled).", path)
