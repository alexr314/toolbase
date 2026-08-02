"""Loadouts: the user's named curated tool sets.

A *loadout* is one file per curation, at ``<scope>/.toolbase/loadouts/<name>.yaml``.
The filename is the loadout name; there is no ``name:`` field inside and
no wrapping block. A loadout body is a per-toolkit partitioned selection:

    toolkits:
      heptapod:
        bundles: [inspire, pythia]   # allowlist by author-declared bundle
        tools:
          enabled: [extra_tool]      # additive per-tool allowlist
          disabled: [pythia_debug]   # final per-tool blocklist
        skills:
          disabled: [debug_guide]    # per-skill blocklist (skills default on)
      aster:
        bundles: [transit]
      arxiv-search: {}               # whole toolkit, uncurated

Names inside a toolkit block are unqualified (``pythia``, not
``heptapod__pythia``) -- the toolkit context is the surrounding key.

Resolution has two parts:

1. *Which loadout is active* -- the chain in ``resolve_active_loadout_name``:
   explicit CLI flag > serve.yaml ``default.loadout`` (project-wins merge) >
   an implicit ``default`` loadout file > error. There is no
   "serve everything" fallback; ``tb serve`` always serves a named loadout
   or fails with a clear message.

2. *What the active loadout exposes* -- the per-toolkit ``ToolkitSelection``
   (bundles / enabled / disabled). The actual bundle->tool expansion and
   union/blocklist application happen in the orchestrator, which has each
   toolkit's real tool list and bundle membership at spawn time.

Discovery is per-file: ``discover_loadouts`` walks the user and project
``loadouts/`` directories; a project loadout shadows a user loadout with
the same basename (no field-level merge -- the project file wins whole).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from ..envs.paths import (
    project_loadouts_dir,
    user_loadouts_dir,
    legacy_project_profiles_dir,
    legacy_user_profiles_dir,
    project_serve_config_path,
    user_serve_config_path,
)
from .config import (
    ServeConfig,
    ServeConfigError,
    load_serve_config,
    merge_serve_configs,
)


class NoActiveLoadoutError(ServeConfigError):
    """No loadout resolved through the chain. Carries a user-facing hint."""


@dataclass
class ToolkitSelection:
    """Per-toolkit curation within a loadout.

    - ``bundles is None`` and ``enabled_tools is None`` -> include the
      whole toolkit (no allowlist).
    - Either set -> allowlist mode: the served set is the union of
      (tools in the named bundles) and (the explicitly enabled tools).
    - ``disabled_tools`` is always subtracted last.
    """

    bundles: Optional[List[str]] = None
    enabled_tools: Optional[List[str]] = None
    disabled_tools: List[str] = field(default_factory=list)
    # Per-toolkit skill blocklist (bare skill slugs, e.g. ``debug_guide``).
    # Skills surface by default when the toolkit is active; this subtracts
    # individual ones. Consumed by skill surfacing (``tb connect``), not by
    # the tool orchestrator.
    disabled_skills: List[str] = field(default_factory=list)

    @property
    def is_allowlist(self) -> bool:
        return self.bundles is not None or self.enabled_tools is not None


def tool_is_served(
    tool_name: str,
    tool_bundles: List[str],
    selection: Optional["ToolkitSelection"],
    availability,
    global_disabled: set,
    installed_bundles: Optional[set] = None,
) -> bool:
    """The single source of truth for "is this tool exposed?".

    Used by both the orchestrator (at spawn time) and ``tb list`` (for the
    active/served view) so the two never drift. Order matches the spec:

    1. **install-time gating** (``installed_bundles``): when set (subset
       install), a tool's bundles must intersect the installed set, or
       the tool is excluded — the pip packages it needs aren't in the
       cache venv. Tools with no declared bundles are always installed
       (no extras gating them) and pass this check.
    2. config-gating: a tool is served if any of its declared bundles is
       available; a tool with no declared bundles (empty list) is always
       past this check.
    3. loadout selection: in allowlist mode (bundles and/or enabled set),
       keep only tools where ANY of their bundles is in the loadout's
       allow-list OR the tool is explicitly enabled (union); then
       subtract the per-toolkit ``disabled``.
    4. the absolute serve.yaml blocklist (``global_disabled``, unqualified
       names for this toolkit).

    Multi-bundle semantics: a tool may belong to several bundles
    (``bundle: [a, b]``). It's available if *any* bundle is available,
    and in-loadout if *any* of its bundles is in ``selection.bundles``.
    Backward compat: a single-bundle tool is just a 1-element list here.

    ``installed_bundles=None`` means "the whole toolkit was installed"
    (legacy installs, or installs that brought in every declared
    bundle). All bundle-aware tools pass step 1 in that case.
    """
    if installed_bundles is not None and tool_bundles:
        if not any(b in installed_bundles for b in tool_bundles):
            return False

    if tool_bundles:
        if not any(availability.is_bundle_available(b) for b in tool_bundles):
            return False
    # else: no declared bundle — pass the config-gating check by default,
    # matching ``BundleAvailability.is_bundle_available(None) is True``.

    if selection is not None:
        if selection.is_allowlist:
            in_bundle = (
                selection.bundles is not None
                and any(b in selection.bundles for b in tool_bundles)
            )
            in_enabled = (
                selection.enabled_tools is not None
                and tool_name in selection.enabled_tools
            )
            if not (in_bundle or in_enabled):
                return False
        if tool_name in selection.disabled_tools:
            return False
    if tool_name in global_disabled:
        return False
    return True


@dataclass
class Loadout:
    """A parsed loadout file."""

    name: str
    path: Path
    scope: str  # "user" | "project"
    toolkits: Dict[str, ToolkitSelection] = field(default_factory=dict)


@dataclass
class ResolvedLoadout:
    """The active loadout plus the absolute serve.yaml blocklists.

    The orchestrator consumes this: for each toolkit in ``toolkits`` it
    applies the ``ToolkitSelection`` against the toolkit's real tool list,
    then subtracts the global ``disabled_tools`` / skips ``disabled_toolkits``.
    """

    name: str
    source: str  # human-readable provenance, for --dry-run
    toolkits: Dict[str, ToolkitSelection]
    disabled_toolkits: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)  # qualified
    warnings: List[str] = field(default_factory=list)


# ── parsing ──────────────────────────────────────────────────────────


def _parse_toolkit_selection(name: str, raw, path: Path) -> ToolkitSelection:
    """Parse one toolkit's entry from a loadout body."""
    if raw is None:
        return ToolkitSelection()  # ``heptapod:`` with no value -> whole toolkit
    if not isinstance(raw, dict):
        raise ServeConfigError(
            f"{path}: toolkit '{name}' must be a mapping (or empty), "
            f"got {type(raw).__name__}"
        )

    sel = ToolkitSelection()

    if "bundles" in raw and raw["bundles"] is not None:
        bundles = raw["bundles"]
        if not isinstance(bundles, list) or not all(
            isinstance(b, str) for b in bundles
        ):
            raise ServeConfigError(
                f"{path}: toolkit '{name}' bundles: must be a list of strings"
            )
        sel.bundles = list(bundles)

    tools_raw = raw.get("tools")
    if tools_raw is not None:
        if not isinstance(tools_raw, dict):
            raise ServeConfigError(
                f"{path}: toolkit '{name}' tools: must be a mapping with "
                "'enabled' / 'disabled' lists"
            )
        enabled = tools_raw.get("enabled")
        if enabled is not None:
            if not isinstance(enabled, list) or not all(
                isinstance(t, str) for t in enabled
            ):
                raise ServeConfigError(
                    f"{path}: toolkit '{name}' tools.enabled must be a list "
                    "of strings"
                )
            sel.enabled_tools = list(enabled)
        disabled = tools_raw.get("disabled")
        if disabled is not None:
            if not isinstance(disabled, list) or not all(
                isinstance(t, str) for t in disabled
            ):
                raise ServeConfigError(
                    f"{path}: toolkit '{name}' tools.disabled must be a list "
                    "of strings"
                )
            sel.disabled_tools = list(disabled)

    skills_raw = raw.get("skills")
    if skills_raw is not None:
        if not isinstance(skills_raw, dict):
            raise ServeConfigError(
                f"{path}: toolkit '{name}' skills: must be a mapping with a "
                "'disabled' list"
            )
        disabled_skills = skills_raw.get("disabled")
        if disabled_skills is not None:
            if not isinstance(disabled_skills, list) or not all(
                isinstance(s, str) for s in disabled_skills
            ):
                raise ServeConfigError(
                    f"{path}: toolkit '{name}' skills.disabled must be a list "
                    "of strings"
                )
            sel.disabled_skills = list(disabled_skills)
        unknown_skill_keys = set(skills_raw.keys()) - {"disabled"}
        if unknown_skill_keys:
            raise ServeConfigError(
                f"{path}: toolkit '{name}' skills has unknown key(s) "
                f"{sorted(unknown_skill_keys)}. Recognized: 'disabled'."
            )

    unknown = set(raw.keys()) - {"bundles", "tools", "skills"}
    if unknown:
        raise ServeConfigError(
            f"{path}: toolkit '{name}' has unknown key(s) {sorted(unknown)}. "
            "Recognized: 'bundles', 'tools', 'skills'."
        )

    return sel


def parse_loadout(data, name: str, path: Path, scope: str) -> Loadout:
    """Parse a loadout-file mapping into a ``Loadout``."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ServeConfigError(
            f"{path}: loadout must be a YAML mapping at the top level"
        )
    toolkits_raw = data.get("toolkits") or {}
    if not isinstance(toolkits_raw, dict):
        raise ServeConfigError(f"{path}: 'toolkits' must be a mapping")

    unknown = set(data.keys()) - {"toolkits"}
    if unknown:
        raise ServeConfigError(
            f"{path}: unknown top-level key(s) {sorted(unknown)}. "
            "A loadout only has a 'toolkits:' block."
        )

    toolkits: Dict[str, ToolkitSelection] = {}
    for tk_name, tk_raw in toolkits_raw.items():
        if not isinstance(tk_name, str):
            raise ServeConfigError(f"{path}: toolkit names must be strings")
        toolkits[tk_name] = _parse_toolkit_selection(tk_name, tk_raw, path)

    return Loadout(name=name, path=path, scope=scope, toolkits=toolkits)


def load_loadout_file(path: Path, name: str, scope: str) -> Loadout:
    """Read and parse a single loadout file. Raises ``ServeConfigError``
    with the path on malformed yaml."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ServeConfigError(f"could not parse {path}: {e}") from e
    return parse_loadout(raw, name, path, scope)


# ── discovery ────────────────────────────────────────────────────────


def discover_loadouts(
    project_root: Optional[Path] = None,
    *,
    user_base: Optional[Path] = None,
) -> Dict[str, Loadout]:
    """Return all available loadouts keyed by name.

    Walks the user ``loadouts/`` dir and (if ``project_root`` is given)
    the project ``loadouts/`` dir. A project loadout shadows a
    user loadout with the same basename -- the project file is used
    whole; the user file with that name is ignored (no merge).

    Each scope falls back to its pre-0.12 ``profiles/`` directory when
    the current one is absent, so a machine that hasn't migrated keeps
    serving. Files are read in place and never rewritten here; the
    directory converts when something writes a loadout.
    """
    found: Dict[str, Loadout] = {}

    for scope, current, legacy in (
        ("user", user_loadouts_dir(base=user_base),
         legacy_user_profiles_dir(base=user_base)),
        *([("project", project_loadouts_dir(project_root),
            legacy_project_profiles_dir(project_root))]
          if project_root is not None else []),
    ):
        directory = current if current.is_dir() else legacy
        if not directory.is_dir():
            continue
        for entry in sorted(directory.glob("*.yaml")):
            found[entry.stem] = load_loadout_file(entry, entry.stem, scope)

    return found


# ── active-loadout resolution chain ──────────────────────────────────


def load_merged_serve_config(
    project_root: Optional[Path] = None,
    *,
    user_base: Optional[Path] = None,
) -> ServeConfig:
    """User serve.yaml merged with the project one (project wins)."""
    user_cfg = load_serve_config(user_serve_config_path(base=user_base))
    if project_root is None:
        return user_cfg
    proj_cfg = load_serve_config(project_serve_config_path(project_root))
    return merge_serve_configs(user_cfg, proj_cfg)


def resolve_active_loadout_name(
    merged_cfg: ServeConfig,
    available: Dict[str, Loadout],
    cli_loadout: Optional[str] = None,
) -> Tuple[str, str]:
    """Pick the active loadout name. Returns ``(name, source)``.

    Order (first match wins):
      1. explicit ``cli_loadout`` (``--loadout`` flag)
      2. ``default.loadout`` from the merged serve.yaml (project-wins)
      3. an implicit loadout literally named ``default``
      4. raise ``NoActiveLoadoutError``

    Raises ``ServeConfigError`` if a named loadout (from flag or
    serve.yaml) doesn't exist among ``available``.
    """
    if cli_loadout is not None:
        if cli_loadout not in available:
            raise ServeConfigError(
                f"No loadout named '{cli_loadout}'. "
                f"Available: {', '.join(sorted(available)) or '(none)'}. "
                "Create one with 'toolbase loadout create'."
            )
        return cli_loadout, "--loadout flag"

    if merged_cfg.default.loadout:
        name = merged_cfg.default.loadout
        if name not in available:
            raise ServeConfigError(
                f"serve.yaml sets default.loadout: '{name}', but no loadout "
                f"by that name exists. Available: "
                f"{', '.join(sorted(available)) or '(none)'}."
            )
        return name, "serve.yaml default.loadout"

    if "default" in available:
        return "default", "implicit default loadout"

    raise NoActiveLoadoutError(
        "No active loadout. Create one with 'toolbase activate <name>' "
        "(creates the default loadout) or 'toolbase loadout create <name>', "
        "or set 'default.loadout:' in serve.yaml, or pass '--loadout <name>'."
    )


def resolve_loadout(
    project_root: Optional[Path] = None,
    *,
    cli_loadout: Optional[str] = None,
    user_base: Optional[Path] = None,
) -> ResolvedLoadout:
    """Full resolution: pick the active loadout and fold in the absolute
    serve.yaml blocklists.

    Raises ``NoActiveLoadoutError`` / ``ServeConfigError`` (both
    subclasses of ``ServeConfigError``) on an unresolvable or malformed
    configuration. The caller renders the message and exits.
    """
    merged_cfg = load_merged_serve_config(project_root, user_base=user_base)
    available = discover_loadouts(project_root, user_base=user_base)
    name, source = resolve_active_loadout_name(merged_cfg, available, cli_loadout)
    loadout = available[name]

    return ResolvedLoadout(
        name=name,
        source=source,
        toolkits=dict(loadout.toolkits),
        disabled_toolkits=list(merged_cfg.default.disabled_toolkits),
        disabled_tools=list(merged_cfg.default.disabled_tools),
        warnings=[],
    )
