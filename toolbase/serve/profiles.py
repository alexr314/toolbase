"""Profiles: the user's named curated tool sets.

A *profile* is one file per curation, at ``<scope>/.toolbase/profiles/<name>.yaml``.
The filename is the profile name; there is no ``name:`` field inside and
no wrapping block. A profile body is a per-toolkit partitioned selection:

    toolkits:
      heptapod:
        bundles: [inspire, pythia]   # allowlist by author-declared bundle
        tools:
          enabled: [extra_tool]      # additive per-tool allowlist
          disabled: [pythia_debug]   # final per-tool blocklist
      aster:
        bundles: [transit]
      arxiv-search: {}               # whole toolkit, uncurated

Names inside a toolkit block are unqualified (``pythia``, not
``heptapod__pythia``) -- the toolkit context is the surrounding key.

Resolution has two parts:

1. *Which profile is active* -- the chain in ``resolve_active_profile_name``:
   explicit CLI flag > serve.yaml ``default.profile`` (project-wins merge) >
   an implicit ``default`` profile file > error. There is no
   "serve everything" fallback; ``tb serve`` always serves a named profile
   or fails with a clear message.

2. *What the active profile exposes* -- the per-toolkit ``ToolkitSelection``
   (bundles / enabled / disabled). The actual bundle->tool expansion and
   union/blocklist application happen in the orchestrator, which has each
   toolkit's real tool list and bundle membership at spawn time.

Discovery is per-file: ``discover_profiles`` walks the user and project
``profiles/`` directories; a project profile shadows a user profile with
the same basename (no field-level merge -- the project file wins whole).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from ..envs.paths import (
    project_profiles_dir,
    user_profiles_dir,
    project_serve_config_path,
    user_serve_config_path,
)
from .config import (
    ServeConfig,
    ServeConfigError,
    load_serve_config,
    merge_serve_configs,
)


class NoActiveProfileError(ServeConfigError):
    """No profile resolved through the chain. Carries a user-facing hint."""


@dataclass
class ToolkitSelection:
    """Per-toolkit curation within a profile.

    - ``bundles is None`` and ``enabled_tools is None`` -> include the
      whole toolkit (no allowlist).
    - Either set -> allowlist mode: the served set is the union of
      (tools in the named bundles) and (the explicitly enabled tools).
    - ``disabled_tools`` is always subtracted last.
    """

    bundles: Optional[List[str]] = None
    enabled_tools: Optional[List[str]] = None
    disabled_tools: List[str] = field(default_factory=list)

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
    3. profile selection: in allowlist mode (bundles and/or enabled set),
       keep only tools where ANY of their bundles is in the profile's
       allow-list OR the tool is explicitly enabled (union); then
       subtract the per-toolkit ``disabled``.
    4. the absolute serve.yaml blocklist (``global_disabled``, unqualified
       names for this toolkit).

    Multi-bundle semantics: a tool may belong to several bundles
    (``bundle: [a, b]``). It's available if *any* bundle is available,
    and in-profile if *any* of its bundles is in ``selection.bundles``.
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
class Profile:
    """A parsed profile file."""

    name: str
    path: Path
    scope: str  # "user" | "project"
    toolkits: Dict[str, ToolkitSelection] = field(default_factory=dict)


@dataclass
class ResolvedProfile:
    """The active profile plus the absolute serve.yaml blocklists.

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
    """Parse one toolkit's entry from a profile body."""
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

    unknown = set(raw.keys()) - {"bundles", "tools"}
    if unknown:
        raise ServeConfigError(
            f"{path}: toolkit '{name}' has unknown key(s) {sorted(unknown)}. "
            "Recognized: 'bundles', 'tools'."
        )

    return sel


def parse_profile(data, name: str, path: Path, scope: str) -> Profile:
    """Parse a profile-file mapping into a ``Profile``."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ServeConfigError(
            f"{path}: profile must be a YAML mapping at the top level"
        )
    toolkits_raw = data.get("toolkits") or {}
    if not isinstance(toolkits_raw, dict):
        raise ServeConfigError(f"{path}: 'toolkits' must be a mapping")

    unknown = set(data.keys()) - {"toolkits"}
    if unknown:
        raise ServeConfigError(
            f"{path}: unknown top-level key(s) {sorted(unknown)}. "
            "A profile only has a 'toolkits:' block."
        )

    toolkits: Dict[str, ToolkitSelection] = {}
    for tk_name, tk_raw in toolkits_raw.items():
        if not isinstance(tk_name, str):
            raise ServeConfigError(f"{path}: toolkit names must be strings")
        toolkits[tk_name] = _parse_toolkit_selection(tk_name, tk_raw, path)

    return Profile(name=name, path=path, scope=scope, toolkits=toolkits)


def load_profile_file(path: Path, name: str, scope: str) -> Profile:
    """Read and parse a single profile file. Raises ``ServeConfigError``
    with the path on malformed yaml."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ServeConfigError(f"could not parse {path}: {e}") from e
    return parse_profile(raw, name, path, scope)


# ── discovery ────────────────────────────────────────────────────────


def discover_profiles(
    project_root: Optional[Path] = None,
    *,
    user_base: Optional[Path] = None,
) -> Dict[str, Profile]:
    """Return all available profiles keyed by name.

    Walks the user ``profiles/`` dir and (if ``project_root`` is given)
    the project ``profiles/`` dir. A project profile shadows a
    user profile with the same basename -- the project file is used
    whole; the user file with that name is ignored (no merge).
    """
    found: Dict[str, Profile] = {}

    user_dir = user_profiles_dir(base=user_base)
    if user_dir.is_dir():
        for entry in sorted(user_dir.glob("*.yaml")):
            name = entry.stem
            found[name] = load_profile_file(entry, name, "user")

    if project_root is not None:
        proj_dir = project_profiles_dir(project_root)
        if proj_dir.is_dir():
            for entry in sorted(proj_dir.glob("*.yaml")):
                name = entry.stem
                found[name] = load_profile_file(entry, name, "project")

    return found


# ── active-profile resolution chain ──────────────────────────────────


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


def resolve_active_profile_name(
    merged_cfg: ServeConfig,
    available: Dict[str, Profile],
    cli_profile: Optional[str] = None,
) -> Tuple[str, str]:
    """Pick the active profile name. Returns ``(name, source)``.

    Order (first match wins):
      1. explicit ``cli_profile`` (``--profile`` flag)
      2. ``default.profile`` from the merged serve.yaml (project-wins)
      3. an implicit profile literally named ``default``
      4. raise ``NoActiveProfileError``

    Raises ``ServeConfigError`` if a named profile (from flag or
    serve.yaml) doesn't exist among ``available``.
    """
    if cli_profile is not None:
        if cli_profile not in available:
            raise ServeConfigError(
                f"No profile named '{cli_profile}'. "
                f"Available: {', '.join(sorted(available)) or '(none)'}. "
                "Create one with 'toolbase profile create'."
            )
        return cli_profile, "--profile flag"

    if merged_cfg.default.profile:
        name = merged_cfg.default.profile
        if name not in available:
            raise ServeConfigError(
                f"serve.yaml sets default.profile: '{name}', but no profile "
                f"by that name exists. Available: "
                f"{', '.join(sorted(available)) or '(none)'}."
            )
        return name, "serve.yaml default.profile"

    if "default" in available:
        return "default", "implicit default profile"

    raise NoActiveProfileError(
        "No active profile. Create one with 'toolbase activate <name>' "
        "(creates the default profile) or 'toolbase profile create <name>', "
        "or set 'default.profile:' in serve.yaml, or pass '--profile <name>'."
    )


def resolve_profile(
    project_root: Optional[Path] = None,
    *,
    cli_profile: Optional[str] = None,
    user_base: Optional[Path] = None,
) -> ResolvedProfile:
    """Full resolution: pick the active profile and fold in the absolute
    serve.yaml blocklists.

    Raises ``NoActiveProfileError`` / ``ServeConfigError`` (both
    subclasses of ``ServeConfigError``) on an unresolvable or malformed
    configuration. The caller renders the message and exits.
    """
    merged_cfg = load_merged_serve_config(project_root, user_base=user_base)
    available = discover_profiles(project_root, user_base=user_base)
    name, source = resolve_active_profile_name(merged_cfg, available, cli_profile)
    profile = available[name]

    return ResolvedProfile(
        name=name,
        source=source,
        toolkits=dict(profile.toolkits),
        disabled_toolkits=list(merged_cfg.default.disabled_toolkits),
        disabled_tools=list(merged_cfg.default.disabled_tools),
        warnings=[],
    )
