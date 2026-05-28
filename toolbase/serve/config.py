"""Selective-serve configuration: ``~/.toolbase/serve.yaml`` plus the
resolver that decides which toolkits and tools a given ``toolbase serve``
invocation should expose.

Two decisions are kept strictly separate:

1. **Persistent default**, stored in ``serve.yaml``. Blocklist-only
   (``default.toolkits.disabled`` / ``default.tools.disabled``). New
   installs run by default — opt-out, not opt-in. Allowlist-style
   ``enabled`` keys are intentionally not supported here because they're a
   footgun: users install a new toolkit, it never gets served, they have
   no idea why.

2. **Named groups**, also in ``serve.yaml`` under ``groups:``. These are
   curated allowlists (a group is *defined by* its membership), so the
   ``toolkits:`` field is an allowlist; ``tools.disabled`` further filters.

Resolution priority for a single ``toolbase serve`` invocation, highest
first:

    positional toolkits  >  --group  >  serve.yaml default
    --enable-tool        →  switches to allowlist-mode for tools
    --disable-tool       →  applied last; wins over --enable-tool
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

from ..config import CONFIG_DIR


SERVE_CONFIG_PATH = CONFIG_DIR / "serve.yaml"


class ServeConfigError(Exception):
    """User-facing error during serve config load / resolution. Caller is
    expected to catch and render with the file path."""


@dataclass
class DefaultBlock:
    """Persistent default selection. Blocklist-only by design."""

    disabled_toolkits: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)  # "toolkit__tool"

    def to_yaml_dict(self) -> dict:
        out: dict = {}
        if self.disabled_toolkits:
            out.setdefault("toolkits", {})["disabled"] = list(self.disabled_toolkits)
        if self.disabled_tools:
            out.setdefault("tools", {})["disabled"] = list(self.disabled_tools)
        return out


@dataclass
class Group:
    """Named curated subset. ``toolkits`` is an allowlist."""

    name: str
    toolkits: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)

    def to_yaml_dict(self) -> dict:
        out: dict = {"toolkits": list(self.toolkits)}
        if self.disabled_tools:
            out["tools"] = {"disabled": list(self.disabled_tools)}
        return out


@dataclass
class ServeConfig:
    """Top-level ``serve.yaml`` shape."""

    default: DefaultBlock = field(default_factory=DefaultBlock)
    groups: Dict[str, Group] = field(default_factory=dict)

    def to_yaml_dict(self) -> dict:
        out: dict = {}
        d = self.default.to_yaml_dict()
        if d:
            out["default"] = d
        if self.groups:
            out["groups"] = {n: g.to_yaml_dict() for n, g in self.groups.items()}
        return out


def load_serve_config(path: Path = SERVE_CONFIG_PATH) -> ServeConfig:
    """Load ``serve.yaml``. Returns an empty config if the file is missing.

    Raises ``ServeConfigError`` with a clear message (and path) if the file
    exists but is malformed. The caller should catch and surface; we never
    throw a yaml stack trace at the user.
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

    cfg = ServeConfig()

    default_raw = raw.get("default") or {}
    if not isinstance(default_raw, dict):
        raise ServeConfigError(f"{path}: 'default' must be a mapping")
    tk = default_raw.get("toolkits") or {}
    if isinstance(tk, dict):
        cfg.default.disabled_toolkits = list(tk.get("disabled") or [])
    tools = default_raw.get("tools") or {}
    if isinstance(tools, dict):
        cfg.default.disabled_tools = list(tools.get("disabled") or [])

    groups_raw = raw.get("groups") or {}
    if not isinstance(groups_raw, dict):
        raise ServeConfigError(f"{path}: 'groups' must be a mapping of name → group")
    for name, gdict in groups_raw.items():
        if not isinstance(gdict, dict):
            raise ServeConfigError(
                f"{path}: group '{name}' must be a mapping"
            )
        toolkits = list(gdict.get("toolkits") or [])
        gtools = gdict.get("tools") or {}
        disabled_tools = list(gtools.get("disabled") or []) if isinstance(gtools, dict) else []
        cfg.groups[name] = Group(name=name, toolkits=toolkits, disabled_tools=disabled_tools)

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


# ── resolution ──────────────────────────────────────────────────────────────


@dataclass
class ResolvedSet:
    """The fully-resolved set of toolkits and tools to serve.

    ``tools[toolkit]`` is the explicit list of tools that should be served
    for that toolkit; ``None`` means "all of this toolkit's tools" (no
    per-tool filter active for that toolkit). The orchestrator uses this
    to decide what to expose upstream.

    ``warnings`` carries non-fatal issues (e.g. "group references missing
    toolkit, skipping") so the caller can surface them.

    ``resolution_path`` describes how the resolver got here, for the
    ``--dry-run`` printout.
    """

    toolkits: List[str]
    tools: Dict[str, Optional[List[str]]]  # toolkit -> tools or None for "all"
    warnings: List[str] = field(default_factory=list)
    resolution_path: List[str] = field(default_factory=list)
    # Fully-qualified disabled tool names ("toolkit__tool"). The
    # orchestrator subtracts these at spawn time when ``tools[tk] is None``,
    # i.e. when no allowlist is active for that toolkit.
    disable_qualified: List[str] = field(default_factory=list)
    # 0.5.1: per-toolkit requested bundles (from --enable-bundle).
    # Maps toolkit name → list of bundle names the user explicitly
    # requested. The orchestrator evaluates each against the toolkit's
    # ``bundles:`` block and surfaces a clear message if a
    # requested bundle is currently unavailable or undeclared.
    enable_bundles: Dict[str, List[str]] = field(default_factory=dict)


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


def resolve_serve_set(
    *,
    installed_toolkits: List[str],
    config: ServeConfig,
    positional_toolkits: List[str] = (),
    group_name: Optional[str] = None,
    enable_tools: List[str] = (),
    disable_tools: List[str] = (),
    enable_bundles: List[str] = (),
) -> ResolvedSet:
    """Pure resolver: turn flags + config + installed list into a serve set.

    Heavily tested in isolation. The orchestrator wraps this and never
    duplicates resolution logic.

    Priority (highest first):
      1. Positional toolkits — replaces the toolkit set entirely.
      2. ``--group`` — replaces the toolkit set; group's blocklist seeds
         the per-tool disable list.
      3. ``serve.yaml`` ``default`` — installed minus disabled lists.

    Then ``--enable-tool`` (allowlist mode) and ``--disable-tool`` (final
    veto) are applied to the resulting set.

    Errors:
      - Positional toolkit not installed → ServeConfigError.
      - --enable-tool refs a toolkit not in the resolved set → ServeConfigError.
      - --group refs a missing group → ServeConfigError.

    Warnings (non-fatal, surfaced via ResolvedSet.warnings):
      - A group's toolkits list contains a name that's not installed.
      - A blocklist entry refers to a non-installed toolkit (no-op).
    """
    installed_set: Set[str] = set(installed_toolkits)
    warnings: List[str] = []
    path: List[str] = []

    # ── Step 1: choose the toolkit set ────────────────────────────────────
    selected_toolkits: List[str]
    seeded_disabled_tools: List[str] = []

    positional_list = list(positional_toolkits)
    if positional_list:
        not_installed = [t for t in positional_list if t not in installed_set]
        if not_installed:
            raise ServeConfigError(
                "Cannot serve toolkits that aren't installed: "
                + ", ".join(not_installed)
                + ".\nInstall them first with 'toolbase install <name>'."
            )
        selected_toolkits = list(positional_list)
        path.append(f"positional toolkits: {', '.join(positional_list)}")
    elif group_name is not None:
        if group_name not in config.groups:
            raise ServeConfigError(
                f"Group '{group_name}' is not defined in {SERVE_CONFIG_PATH}.\n"
                f"List groups with 'toolbase groups list'."
            )
        group = config.groups[group_name]
        kept = [t for t in group.toolkits if t in installed_set]
        skipped = [t for t in group.toolkits if t not in installed_set]
        for s in skipped:
            warnings.append(
                f"group '{group_name}' references toolkit '{s}', "
                "which is not installed; skipping"
            )
        if not kept:
            raise ServeConfigError(
                f"Group '{group_name}' has no installed toolkits."
            )
        selected_toolkits = kept
        seeded_disabled_tools = list(group.disabled_tools)
        path.append(f"--group {group_name}: {', '.join(kept)}")
        if seeded_disabled_tools:
            path.append(
                f"  group disables: {', '.join(seeded_disabled_tools)}"
            )
    else:
        # Default path: installed minus disabled.
        disabled_default = set(config.default.disabled_toolkits)
        # Warn on stale entries that name uninstalled toolkits.
        for t in disabled_default:
            if t not in installed_set:
                warnings.append(
                    f"default.toolkits.disabled lists '{t}', "
                    "which is not installed"
                )
        selected_toolkits = [t for t in installed_toolkits if t not in disabled_default]
        seeded_disabled_tools = list(config.default.disabled_tools)
        if disabled_default:
            path.append(
                f"default: all installed minus {', '.join(sorted(disabled_default))}"
            )
        else:
            path.append("default: all installed")
        if seeded_disabled_tools:
            path.append(
                f"  default disables tools: {', '.join(seeded_disabled_tools)}"
            )

    # ── Step 2: per-tool filtering ────────────────────────────────────────
    selected_set: Set[str] = set(selected_toolkits)

    enable_pairs: List[Tuple[str, str]] = []
    for q in enable_tools:
        tk, t = _split_tool(q)
        if tk not in selected_set:
            raise ServeConfigError(
                f"Cannot enable '{q}' — '{tk}' is not in this serve session."
            )
        enable_pairs.append((tk, t))

    disable_pairs: List[Tuple[str, str]] = []
    for q in list(seeded_disabled_tools) + list(disable_tools):
        tk, t = _split_tool(q)
        if tk in selected_set:
            disable_pairs.append((tk, t))
        # silently drop disable entries for toolkits not in this session;
        # could be a stale serve.yaml entry but it's not an error here.

    # Per-toolkit allowlist if --enable-tool was used; else no per-tool restriction.
    per_toolkit_allowlist: Dict[str, Set[str]] = {}
    if enable_pairs:
        for tk, t in enable_pairs:
            per_toolkit_allowlist.setdefault(tk, set()).add(t)
        path.append(
            "--enable-tool (allowlist): "
            + ", ".join(f"{tk}__{t}" for tk, t in enable_pairs)
        )

    disable_index: Dict[str, Set[str]] = {}
    for tk, t in disable_pairs:
        disable_index.setdefault(tk, set()).add(t)
    if disable_tools:
        path.append(
            "--disable-tool: " + ", ".join(disable_tools)
        )

    tools_resolved: Dict[str, Optional[List[str]]] = {}
    final_toolkits: List[str] = []
    for tk in selected_toolkits:
        if tk in per_toolkit_allowlist:
            allow = per_toolkit_allowlist[tk]
            disabled = disable_index.get(tk, set())
            kept = sorted(allow - disabled)
            if not kept:
                # Allowlisting + disabling left the toolkit empty; drop it.
                warnings.append(
                    f"toolkit '{tk}' has no tools enabled in this session"
                )
                continue
            tools_resolved[tk] = kept
            final_toolkits.append(tk)
        else:
            disabled = disable_index.get(tk, set())
            if disabled:
                # Can't compute the final tool list yet — we don't know the
                # toolkit's full tool list at resolver time. Leave None and
                # let the orchestrator subtract `disabled` from the toolkit's
                # actual tool list at spawn time.
                tools_resolved[tk] = None  # placeholder; orchestrator subtracts
            else:
                tools_resolved[tk] = None
            final_toolkits.append(tk)

    disable_qualified = [f"{tk}__{t}" for tk, t in disable_pairs]

    # ── --enable-bundle: per-toolkit requested bundles ──────────────────
    # Format: ``TOOLKIT__BUNDLE``. Each entry asks the orchestrator to
    # serve the named bundle's tools within that toolkit. If the toolkit
    # isn't in the resolved set, that's an error (the user can't ask
    # for bundles from a toolkit they're not serving). If the bundle is
    # currently unavailable (its ``requires:`` aren't satisfied) or
    # undeclared, the orchestrator surfaces a clear message at spawn
    # time — we just collect the request here without crashing.
    enable_bundles_map: Dict[str, List[str]] = {}
    final_toolkit_set: Set[str] = set(final_toolkits)
    for q in enable_bundles:
        if "__" not in q:
            raise ServeConfigError(
                f"--enable-bundle '{q}' must be in 'toolkit__bundle' form"
            )
        tk, _, bname = q.partition("__")
        if not tk or not bname:
            raise ServeConfigError(
                f"--enable-bundle '{q}' must be in 'toolkit__bundle' form"
            )
        if tk not in final_toolkit_set:
            raise ServeConfigError(
                f"Cannot enable bundle '{q}' — toolkit '{tk}' is not in "
                "this serve session."
            )
        enable_bundles_map.setdefault(tk, []).append(bname)
    if enable_bundles_map:
        path.append(
            "--enable-bundle: " + ", ".join(
                f"{tk}__{b}"
                for tk, bs in enable_bundles_map.items()
                for b in bs
            )
        )

    return ResolvedSet(
        toolkits=final_toolkits,
        tools=tools_resolved,
        warnings=warnings,
        resolution_path=path,
        disable_qualified=disable_qualified,
        enable_bundles=enable_bundles_map,
    )


def disabled_tools_for(
    cfg: ServeConfig,
    *,
    group_name: Optional[str],
    flag_disables: List[str],
) -> List[str]:
    """Return the merged set of fully-qualified disabled tools after picking
    a base (group or default). Used by the orchestrator to subtract from a
    toolkit's actual tool list at spawn time when no allowlist is active.
    """
    out: List[str] = []
    if group_name and group_name in cfg.groups:
        out.extend(cfg.groups[group_name].disabled_tools)
    elif group_name is None:
        out.extend(cfg.default.disabled_tools)
    out.extend(flag_disables)
    # de-dup, preserve order
    seen: Set[str] = set()
    deduped: List[str] = []
    for q in out:
        if q not in seen:
            seen.add(q)
            deduped.append(q)
    return deduped
