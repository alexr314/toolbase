"""
Orchestrator for ``toolbase serve`` (MVP, ``--no-tui`` mode only).

Responsibilities:

1. Discover installed toolkits via ``~/.toolbase/toolkits/*/.tb_meta.json``.
2. Classify each as ready / skipped (docker, needs_setup, broken).
3. Print a scannable startup banner.
4. For each ready toolkit, hand the spawn argv to an Orchestral 1.4
   ``MCPClient`` in stdio mode; the client owns the subprocess and the
   wire is the subprocess's own stdin/stdout pipe (no port, no HTTP).
5. ``MCPClient.connect()`` spawns the host and completes the MCP handshake.
6. Build proxy tools (namespaced ``<toolkit>__<tool>``) and aggregate them.
7. Start ``orchestral.mcp.MCPServer`` on stdio (Claude Code talks to it).
8. On shutdown, disconnect each client (which terminates its subprocess).

Transport note: the orchestrator↔subprocess wire is **persistent stdio**
(the client holds one long-lived session per toolkit). The HTTP-loopback
design — port-bind handshake, FastMCP, ``_wait_for_port_ready`` — was
retired in 0.4.1. See ``docs/SERVE_ARCHITECTURE.md`` (carries a superseded
banner) for the historical HTTP rationale.

Out of scope for the MVP (deferred per direction):

- Per-call timeout enforcement (relies on MCPClient's default).
- TUI-facing event subscription API.
- Hot reload.

Implemented post-MVP:

- Auto-restart of crashed per-toolkit subprocesses with exponential
  backoff (1s, 4s, 16s; budget 3). See §3.3 of SERVE_ARCHITECTURE.md.

See ``docs/SERVE_ARCHITECTURE.md`` for the full design.
"""

from __future__ import annotations

import enum
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from ..config import LOGS_DIR, TOOLKITS_DIR
from ..envs.cache import LEGACY_META_FILE
from ..logging.logger import ToolLogger, get_logger


# NOTE 2026-05-22: ``HOST_HANDSHAKE_TIMEOUT_S`` and ``HOST_PORT_READY_TIMEOUT_S``
# were removed here — dead HTTP-loopback constants the 0.4.1 stdio cutover
# left behind (defined, referenced nowhere). With stdio there is no port to
# poll for accept-readiness; ``MCPClient.connect()`` completes the MCP
# handshake over the subprocess's own pipes. See the retirement note further
# down (``_wait_for_port_ready``/``_start_stderr_pump``).
#
# The MCPClient's `timeout` parameter governs *both* the initial stdio
# connect and each subsequent call. We default it to 60 s so long-running
# tool calls don't fail prematurely.
#
# History (2026-05-06): the original code passed a `MCP_CONNECT_TIMEOUT_S
# = 10.0` constant here, which Orchestral applied as the per-call timeout
# too — silently capping every tool at 10 s, *worse* than Orchestral's
# 30 s default. Renamed and bumped to 60 s. Override per-invocation with
# `toolbase serve --call-timeout SECONDS`.
DEFAULT_CALL_TIMEOUT_S = 60.0
SHUTDOWN_GRACEFUL_S = 5.0

# Restart policy for a per-toolkit subprocess that crashes *after* a
# successful initial connect (i.e. CRASHED in the lifecycle state machine).
#
# Initial-launch failures do NOT consume this budget — if `start()` can't
# bring a toolkit up (spawn failed, handshake timed out, MCPClient connect
# failed), the orchestrator skips that toolkit immediately and keeps
# serving the rest. Restart budget is reserved for *runtime* crashes
# (subprocess died mid-call, OOM-killed between calls). Configuration
# bugs don't get fixed by restarting three times in 21 seconds; flakes do.
RESTART_BUDGET = 3
RESTART_BACKOFF_S = (1.0, 4.0, 16.0)


class ToolkitState(enum.Enum):
    """Per-toolkit lifecycle state. See SERVE_ARCHITECTURE.md §3.7."""
    DISCOVERED = "discovered"  # walked, classified, not yet spawned
    STARTING = "starting"      # spawn → handshake → connect in flight
    READY = "ready"            # MCPClient connected; calls succeeding
    CRASHED = "crashed"        # detected dead; restart pending or running
    FAILED = "failed"          # restart budget exhausted; terminal
    STOPPED = "stopped"        # user toggled off (TUI hook; unused now)


# ── data classes ────────────────────────────────────────────────────────


@dataclass
class ToolkitDiscovery:
    """A single toolkit discovered in TOOLKITS_DIR. Pre-launch state."""
    name: str
    path: Path
    meta: Dict[str, Any]
    skip_reason: Optional[str] = None  # human-readable; None = ready

    @property
    def env_type(self) -> str:
        return self.meta.get("environment", "unknown")

    @property
    def python_version(self) -> str:
        return self.meta.get("python_version", "?")


@dataclass
class ToolkitRuntime:
    """A successfully spawned toolkit, post-connect.

    As of 0.4.1, ``mcp_client`` (an Orchestral 1.4 ``MCPClient`` in
    stdio mode) owns the subprocess lifecycle. The orchestrator never
    holds a ``Popen`` handle directly, never knows the port (there is
    none — the wire is the subprocess's own stdin/stdout pipe), and
    never pumps stderr (the host writes to the per-toolkit log file
    itself via ``TOOLBASE_HOST_LOG``).

    ``discovery`` is held so a restart can rebuild the spawn argv
    without re-walking TOOLKITS_DIR or re-reading metadata.
    """
    name: str
    path: Path
    upstream_tool_names: List[str]
    mcp_client: Any  # orchestral.mcp.MCPClient (stdio transport)
    # Restart machinery. See RESTART_BUDGET / RESTART_BACKOFF_S.
    state: ToolkitState = ToolkitState.READY
    restart_attempts: int = 0
    # Serializes restart kickoff so parallel tool calls on the same
    # crashed toolkit don't double-spawn the restart thread.
    restart_lock: threading.Lock = field(default_factory=threading.Lock)
    # Original discovery record; needed to re-spawn on restart.
    discovery: Optional[ToolkitDiscovery] = None
    # Last error seen on a permanently-failed toolkit (for the agent-facing
    # message and the `toolkit_permanently_failed` telemetry).
    last_error: str = ""


# ── discovery ───────────────────────────────────────────────────────────


def discover_toolkits(toolkits_dir: Optional[Path] = None) -> List[ToolkitDiscovery]:
    """Discover installed toolkits from the 0.5.0 cache layout.

    Walks ``~/.toolbase/cache/<name>/<version>/`` via
    ``toolbase.envs.walk_cache``. For each entry, selects the version
    pinned in the active project's manifest if a pin exists; otherwise
    falls back to the only-installed-version when there's exactly one,
    or the highest version when there are several (with a soft warning
    on the skip channel).

    The ``toolkits_dir`` parameter is retained as a back-compat hook for
    tests that still pass it; if non-None, the function reverts to the
    legacy walk over that directory (used by the existing test suite
    pending its migration). Production callers should pass ``None``
    (the default) so the cache walker is used.
    """
    if toolkits_dir is not None:
        return _legacy_discover_toolkits(toolkits_dir)

    from ..envs import (
        walk_cache,
        project_manifest_path,
        load_manifest,
    )
    from ..versioning import parse_version

    entries = walk_cache()
    if not entries:
        return []

    # Read the active project's manifest. Phase 3 wires real discovery
    # via ``_resolve_active_project_root`` (in cli.py); we import it
    # lazily to avoid a circular dependency at module load.
    pin_by_name: Dict[str, str] = {}
    try:
        from ..cli import _resolve_active_project_root
        project_root, _source = _resolve_active_project_root()
        manifest_path = project_manifest_path(project_root)
        manifest = load_manifest(manifest_path)
        for e in manifest.toolkits:
            pin_by_name[e.name] = e.version
    except Exception:
        pin_by_name = {}

    # Group cache entries by name.
    by_name: Dict[str, List] = {}
    for e in entries:
        by_name.setdefault(e.name, []).append(e)

    found: List[ToolkitDiscovery] = []
    for name in sorted(by_name):
        candidates = by_name[name]
        pin = pin_by_name.get(name)
        chosen = None
        skip_extra = None

        if pin is not None:
            for c in candidates:
                if c.version == pin:
                    chosen = c
                    break
            if chosen is None:
                # Pin exists but no matching slot — install was deleted
                # outside our knowledge. Skip with a clear reason.
                # Use the first candidate's path for the discovery
                # record so the banner still shows the name.
                chosen = candidates[0]
                skip_extra = (
                    f"pinned version {pin} not in cache "
                    f"(available: {', '.join(c.version for c in candidates)})"
                )
        elif len(candidates) == 1:
            chosen = candidates[0]
        else:
            # No pin, multiple versions. Pick the highest; log it.
            chosen = sorted(
                candidates,
                key=lambda c: parse_version(c.version) or (0, 0, 0),
                reverse=True,
            )[0]

        # Build the legacy-shaped meta dict that the rest of the
        # orchestrator expects.
        meta = dict(chosen.legacy_meta)
        if not meta.get("environment") and chosen.install_meta.get("install_method"):
            meta["environment"] = chosen.install_meta["install_method"]
            meta.setdefault(
                "python_version",
                chosen.install_meta.get("python_version", "?"),
            )
            for k in ("python_path", "env_name"):
                if k not in meta and k in chosen.install_meta:
                    meta[k] = chosen.install_meta[k]

        skip: Optional[str] = skip_extra
        if not skip:
            env = meta.get("environment")
            if env == "docker":
                skip = "Docker mode (not yet supported)"
            elif env not in ("venv", "conda"):
                skip = f"unknown environment type: {env!r}"

        found.append(ToolkitDiscovery(
            name=name, path=chosen.path, meta=meta, skip_reason=skip,
        ))
    return found


def _legacy_discover_toolkits(toolkits_dir: Path) -> List[ToolkitDiscovery]:
    """0.4.x walker — still used by tests that haven't migrated yet.

    Walks a flat ``<dir>/<name>/.tb_meta.json`` shape. Production
    code paths use the cache walker via ``discover_toolkits(None)``.
    """
    found: List[ToolkitDiscovery] = []
    if not toolkits_dir.exists():
        return found

    for entry in sorted(toolkits_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta_file = entry / LEGACY_META_FILE
        if not meta_file.exists():
            found.append(ToolkitDiscovery(
                name=entry.name, path=entry, meta={},
                skip_reason=f"missing {LEGACY_META_FILE} (broken install)",
            ))
            continue
        try:
            meta = json.loads(meta_file.read_text())
        except Exception:
            found.append(ToolkitDiscovery(
                name=entry.name, path=entry, meta={},
                skip_reason=f"unreadable {LEGACY_META_FILE}",
            ))
            continue

        skip: Optional[str] = None
        env = meta.get("environment")
        if env == "docker":
            skip = "Docker mode (not yet supported)"
        elif env not in ("venv", "conda"):
            skip = f"unknown environment type: {env!r}"

        found.append(ToolkitDiscovery(
            name=entry.name, path=entry, meta=meta, skip_reason=skip,
        ))
    return found


def _resolve_state_config(
    disc: ToolkitDiscovery,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate the toolkit's stored config against its declared schema.

    Returns ``(state_config_dict, skip_reason)``. Exactly one is non-None:

    - ``({...}, None)`` — config is valid (or the toolkit has no
      ``config:`` block, in which case ``state_config_dict`` is empty).
      The orchestrator passes the dict to the host subprocess via
      ``--state-config <json>``.
    - ``(None, reason)`` — the toolkit declared required fields the
      user hasn't filled in, or stored values fail validation. The
      orchestrator skips this toolkit with ``reason`` in the banner.

    Imports the setup module lazily so a malformed ``config:`` block
    in some other toolkit can't take down the whole orchestrator
    startup. Per-toolkit failures stay per-toolkit.
    """
    # Read the toolkit's published `config:` block from its toolkit.yaml.
    # The metadata file (.tb_meta.json) doesn't carry it because the
    # block is the toolkit author's published schema, not user data.
    yaml_path = disc.path / "toolkit.yaml"
    if not yaml_path.exists():
        # Broken install at this point shouldn't happen — discover
        # already filtered missing metadata — but be defensive.
        return None, "toolkit.yaml missing (broken install)"

    try:
        import yaml as _yaml
        with open(yaml_path, "r") as f:
            tk_data = _yaml.safe_load(f) or {}
    except Exception as e:
        return None, f"unreadable toolkit.yaml: {e}"

    raw_block = tk_data.get("config") or []
    has_setup_py = (disc.path / "setup.py").exists()
    declares_setup = bool(tk_data.get("setup_script"))

    # Tier-1 declarative validation (config: block).
    state_config: Dict[str, Any] = {}
    if raw_block:
        try:
            from ..setup import parse_config_block, load_state_config
        except Exception as e:
            return None, f"setup module unavailable: {e}"

        try:
            schema = parse_config_block(raw_block)
        except Exception as e:
            return None, f"invalid config: schema in toolkit.yaml: {e}"

        # Phase 4 (0.5.0): resolve via two-layer user→project merge.
        # Discovery of the active project is delegated to the CLI helper
        # (lazy-imported here to avoid a circular at module load).
        try:
            from ..cli import _resolve_active_project_root
            project_root, _source = _resolve_active_project_root()
        except Exception:
            project_root = None

        resolution = load_state_config(
            disc.name, schema, project_root=project_root,
        )
        if not resolution.ok:
            return None, "config incomplete — " + (resolution.skip_reason() or "unknown")
        state_config = dict(resolution.state_config)

    # Tier-2 validate(ctx) — only if the toolkit declares setup_script
    # AND has a setup.py at root. Both checks are needed because a
    # toolkit could ship one without the other (broken state we surface
    # explicitly at validate / publish time, but be defensive here).
    if declares_setup and has_setup_py:
        try:
            from ..setup import validate_setup_script_cached
        except Exception as e:
            return None, f"setup module unavailable: {e}"
        try:
            v_result = validate_setup_script_cached(disc.name)
        except Exception as e:
            return None, f"validate(ctx) failed to run: {e}"
        if not v_result.ok:
            msg = v_result.message or "validate(ctx) returned False"
            return None, f"validate(ctx) failed — {msg}"

    return state_config, None


# ── subprocess launch ───────────────────────────────────────────────────


def _read_tools_spec(toolkit_path: Path) -> List[Dict[str, Any]]:
    """Extract the ``tools:`` list from the toolkit's yaml, if present.

    Returns ``[]`` when the yaml is missing, malformed, or carries no
    ``tools:`` field — the host treats that as "fall back to implicit
    tools/__init__.py discovery", which is the legacy path. Each
    returned entry is a dict with at least ``name`` and either
    ``module`` (explicit form) or ``function`` (implicit form).
    """
    yaml_path = toolkit_path / "toolkit.yaml"
    if not yaml_path.is_file():
        return []
    try:
        import yaml as pyyaml  # PyYAML; bundled with toolbase's deps
        data = pyyaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    tools = data.get("tools")
    if not isinstance(tools, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        # Pass through only the fields the host consumes.
        cleaned: Dict[str, Any] = {}
        for key in ("name", "module", "function", "description"):
            if key in entry:
                cleaned[key] = entry[key]
        if "name" in cleaned and ("module" in cleaned or "function" in cleaned):
            out.append(cleaned)
    return out


def _read_bundles_and_membership(
    toolkit_path: Path,
) -> Tuple[Optional[Dict[str, Dict[str, Any]]], Dict[str, List[str]]]:
    """Extract the ``bundles:`` block and per-tool bundle membership.

    Returns ``(bundles_block, name_to_bundles)``:

    - ``bundles_block``: the parsed ``bundles:`` mapping, or
      ``None`` if the toolkit doesn't declare one (backward compat —
      no gating in that case).
    - ``name_to_bundles``: tool name → list of bundle names the tool
      belongs to (empty list = no bundle declared = always-available).
      Empty mapping if the yaml is missing or malformed (gate-evaluation
      falls back to "all served").

    Accepts both the historical singular form (``bundle: foo``) and the
    list form (``bundle: [foo, bar]``); both are normalised to a list.

    Defensive reading: any malformed shape returns the safe fallback
    (no block, empty membership), matching how ``_read_tools_spec``
    handles parse failures.
    """
    yaml_path = toolkit_path / "toolkit.yaml"
    if not yaml_path.is_file():
        return None, {}
    try:
        import yaml as pyyaml
        data = pyyaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return None, {}
    if not isinstance(data, dict):
        return None, {}

    raw_block = data.get("bundles")
    bundles_block: Optional[Dict[str, Dict[str, Any]]] = None
    if isinstance(raw_block, dict) and raw_block:
        # Keep only mapping-shaped entries; ignore malformed ones rather
        # than failing the whole serve startup. Validation at publish
        # time is the gate for shape correctness.
        cleaned_block: Dict[str, Dict[str, Any]] = {}
        for bname, bentry in raw_block.items():
            if not isinstance(bname, str):
                continue
            if isinstance(bentry, dict):
                cleaned_block[bname] = dict(bentry)
            elif bentry is None:
                # YAML ``foo:`` with no value parses as None — treat
                # as an empty bundle entry (no requires).
                cleaned_block[bname] = {}
        if cleaned_block:
            bundles_block = cleaned_block

    name_to_bundles: Dict[str, List[str]] = {}
    tools = data.get("tools") if isinstance(data, dict) else None
    if isinstance(tools, list):
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            tool_name = entry.get("name")
            if not isinstance(tool_name, str):
                continue
            bundle = entry.get("bundle")
            if isinstance(bundle, str) and bundle:
                name_to_bundles[tool_name] = [bundle]
            elif isinstance(bundle, list):
                name_to_bundles[tool_name] = [
                    b for b in bundle if isinstance(b, str) and b
                ]
            else:
                name_to_bundles[tool_name] = []

    return bundles_block, name_to_bundles


def _resolve_bundle_availability(
    disc: "ToolkitDiscovery",
) -> Tuple[Any, Dict[str, List[str]]]:
    """Evaluate this toolkit's ``bundles:`` against its resolved config.

    Returns ``(BundleAvailability, name_to_bundles)``. The orchestrator
    uses ``BundleAvailability.is_bundle_available(bundle)`` per bundle
    of each tool at spawn time to decide whether to expose it; a tool
    with multiple declared bundles is served if any are available.

    Resolution sourcing: same two-layer user→project merge that the
    Phase 3C-1 declarative path uses, via
    ``envs.config.resolve_toolkit_config``. Project layer overrides
    user layer key-by-key (see ``envs/config.py``). The ``<NEEDS
    VALUE>`` sentinel counts as unset, matching the existing
    state-config gate semantics one level up.

    On any read failure (no toolkit.yaml, malformed file, etc.),
    returns an empty availability that gates nothing — pass-through
    behavior so a broken yaml doesn't take down all toolkits.
    """
    from .bundles import (
        BundleAvailability,
        evaluate_bundles,
    )

    bundles_block, name_to_bundles = _read_bundles_and_membership(
        disc.path
    )

    # Resolve the two-layer config for this toolkit. The active project
    # root is resolved via the CLI helper (lazy import to break the
    # circular dependency; see HANDOFF.md gotcha #17).
    resolved_config: Dict[str, Any] = {}
    try:
        from ..envs.config import resolve_toolkit_config
        try:
            from ..cli import _resolve_active_project_root
            project_root, _src = _resolve_active_project_root()
        except Exception:
            project_root = None
        if project_root is not None:
            resolved_config = resolve_toolkit_config(
                disc.name, project_root,
            )
    except Exception:
        # Pass-through on any error — better to over-serve than block
        # startup over a config-resolution glitch.
        resolved_config = {}

    availability = evaluate_bundles(bundles_block, resolved_config)
    return availability, name_to_bundles


def _build_host_command(
    disc: ToolkitDiscovery,
    *,
    state_config: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return the argv for spawning the per-toolkit host subprocess.

    ``state_config`` is the validated Phase 3C-1 config dict (flat
    ``{state_field: value}``) for the toolkit. ``None`` or empty dict
    is serialized to ``""`` for the host's empty-input fast path.
    """
    state_arg = ""
    if state_config:
        state_arg = json.dumps(state_config, ensure_ascii=False)
    tools_spec = _read_tools_spec(disc.path)
    tools_spec_arg = ""
    if tools_spec:
        tools_spec_arg = json.dumps(tools_spec, ensure_ascii=False)
    base_args = [
        "-m", "toolbase._toolkit_host",
        "--toolkit-dir", str(disc.path),
        "--name", disc.name,
        "--state-config", state_arg,
        "--tools-spec", tools_spec_arg,
    ]
    if disc.env_type == "venv":
        python_exe = disc.meta.get("python_path")
        if not python_exe:
            raise RuntimeError(
                f"venv toolkit {disc.name!r} has no python_path in metadata"
            )
        return [python_exe] + base_args
    if disc.env_type == "conda":
        env_name = disc.meta.get("env_name")
        if not env_name:
            raise RuntimeError(
                f"conda toolkit {disc.name!r} has no env_name in metadata"
            )
        # --no-capture-output so stderr (and our stdout handshake) flow through.
        return [
            "conda", "run", "--no-capture-output", "-n", env_name,
            "python",
        ] + base_args
    raise RuntimeError(f"unsupported env_type {disc.env_type!r}")


def _build_host_env(toolkit_path: Path, toolkit_name: str) -> Dict[str, str]:
    """Compose the subprocess environment for the per-toolkit host.

    The toolkit's interpreter doesn't have ``toolbase`` installed, only
    ``orchestral-ai`` and ``mcp``. We need ``toolbase._toolkit_host`` to
    be importable, so we point ``PYTHONPATH`` at the parent package
    location of the running orchestrator.

    We also pass ``TOOLBASE_HOST_LOG`` so the host can redirect its
    stderr into ``~/.toolbase/logs/<toolkit>.log``. Pre-0.4.1 the
    orchestrator captured the host's stderr via ``Popen(stderr=PIPE)``
    and pumped it to that file; with Orchestral 1.4's MCPClient owning
    the subprocess lifecycle, the orchestrator can no longer intercept
    stderr, so the host writes directly. Same destination, simpler
    plumbing.
    """
    env = os.environ.copy()
    # Find the directory that contains the ``toolbase`` package.
    import toolbase as _sk
    pkg_parent = str(Path(_sk.__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        pkg_parent + (os.pathsep + existing if existing else "")
    )
    env["PYTHONUNBUFFERED"] = "1"
    # The host is launched as ``python -m toolbase._toolkit_host`` with cwd
    # at the toolkit dir, and ``-m`` prepends cwd to sys.path. A toolkit
    # that ships a top-level dir named like an installed package (the
    # scaffold's ``mcp/`` is the canonical trap) would then shadow that
    # package -- e.g. ``import mcp`` resolves to the toolkit's ``mcp/``
    # instead of the MCP SDK, and ``orchestral.mcp`` fails to import.
    # PYTHONSAFEPATH (3.11+) stops the implicit cwd/script-dir entry; the
    # explicit PYTHONPATH above and the spec_from_file_location tool loader
    # are unaffected. Pins the regression in test_host_import_isolation.
    env["PYTHONSAFEPATH"] = "1"
    env["TOOLBASE_HOST_LOG"] = str(LOGS_DIR / f"{toolkit_name}.log")
    return env


def _prepare_per_toolkit_log(toolkit_name: str, pid_hint: str = "") -> None:
    """Pre-create or rotate the per-toolkit log before host startup.

    The host opens this same path (passed via ``TOOLBASE_HOST_LOG``)
    and appends to it. Running this first ensures rotation happens
    BEFORE the host writes its first line, and that we add a session
    separator the user can grep for in long log files.
    """
    log_path = LOGS_DIR / f"{toolkit_name}.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _prune_per_toolkit_log_if_oversized(log_path)
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(
                f"\n--- session {time.strftime('%Y-%m-%d %H:%M:%S')}"
                f"{' ' + pid_hint if pid_hint else ''} ---\n"
            )
    except OSError:
        # Logging is best-effort; never block startup over it.
        pass


PER_TOOLKIT_LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB
PER_TOOLKIT_LOG_TAIL_BYTES = 2 * 1024 * 1024  # keep ~last 2 MB on prune


def _prune_per_toolkit_log_if_oversized(log_path: Path) -> None:
    """Tail-prune a per-toolkit stderr log if it grew past the size cap.

    Mirrors the serve.log strategy: read the last ~tail bytes, drop any
    partial leading line, rewrite the file. Cheaper than a true rolling
    rotation and good enough for stderr-noise capture.
    """
    try:
        if not log_path.exists():
            return
        size = log_path.stat().st_size
        if size <= PER_TOOLKIT_LOG_MAX_BYTES:
            return
        import os as _os
        with open(log_path, "rb") as f:
            f.seek(-PER_TOOLKIT_LOG_TAIL_BYTES, _os.SEEK_END)
            tail = f.read()
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]
        with open(log_path, "wb") as f:
            f.write(b"# --- log pruned to last ~2 MB ---\n")
            f.write(tail)
    except Exception:
        # Pruning is best-effort; never block startup over it.
        pass


# NOTE 2026-05-07: ``_wait_for_port_ready`` and ``_start_stderr_pump`` were
# retired with the HTTP-loopback machinery. The stdio MCP path has no port
# (the wire is the subprocess's own stdin/stdout pipe), and the host
# writes directly to its per-toolkit log file via ``TOOLBASE_HOST_LOG``
# rather than relying on the orchestrator to pump its stderr.


# ── orchestrator ────────────────────────────────────────────────────────


class Orchestrator:
    """Holds discovered toolkits, running subprocesses, MCP clients."""

    def __init__(
        self,
        *,
        console: Optional[Console] = None,
        toolkits_dir: Optional[Path] = None,
        profile: Optional[Any] = None,  # serve.profiles.ResolvedProfile
        call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
    ):
        self.console = console or Console(stderr=True)
        # Stderr console because stdin/stdout are owned by MCP stdio in the
        # serve flow — anything we print to stdout would corrupt the
        # protocol stream.
        #
        # ``toolkits_dir`` is None in production — the 0.5.0 cache layout
        # discovers via ``walk_cache()``. Some tests still pass an explicit
        # legacy path; that path takes the back-compat walker.
        self.toolkits_dir = toolkits_dir
        self.logger = get_logger(serve_log=True)
        self._runtimes: Dict[str, ToolkitRuntime] = {}
        self._proxy_tools: List[Any] = []
        self._shutdown_initiated = False
        # The active profile (``serve.profiles.ResolvedProfile``) decides
        # which toolkits and which tools per toolkit to expose. None means
        # "serve every discovered toolkit, uncurated" — used by lower-level
        # callers (e.g. crash-recovery tests). The CLI always resolves a
        # profile and passes it, so the "no active profile" requirement is
        # enforced at the CLI boundary, not here.
        self._profile = profile
        self._call_timeout_s = call_timeout_s

    def _selection_for(self, name: str):
        """Return the ``ToolkitSelection`` for a toolkit under the active
        profile, or ``None`` when no profile is active (serve-all mode)."""
        if self._profile is None:
            return None
        return self._profile.toolkits.get(name)

    # ── startup ─────────────────────────────────────────────────────────

    def start(self) -> List[Any]:
        """Discover, spawn, connect, build proxy tools. Returns the list.

        Raises ``RuntimeError`` only if no toolkit is ready to serve at all
        (so the user gets a clear "nothing to do" instead of a silent stdio
        server with zero tools).
        """
        self.logger.log_event("serve_started", message="orchestrator booting")

        discoveries = discover_toolkits(self.toolkits_dir)
        if not discoveries:
            self.console.print(
                "[yellow]No toolkits installed.[/yellow] "
                "Install one with: [cyan]toolbase install <name>[/cyan]"
            )
            raise RuntimeError("no toolkits installed")

        # Filter discoveries to the active profile: only toolkits named in
        # the profile are served, minus the absolute serve.yaml blocklist.
        # When no profile is active (serve-all mode) every discovered
        # toolkit is served.
        if self._profile is not None:
            served = set(self._profile.toolkits.keys())
            disabled_tk = set(self._profile.disabled_toolkits)
            for d in discoveries:
                if d.skip_reason is not None:
                    continue
                if d.name in disabled_tk:
                    d.skip_reason = "disabled in serve.yaml"
                elif d.name not in served:
                    d.skip_reason = (
                        f"not in active profile '{self._profile.name}'"
                    )
            for w in self._profile.warnings:
                self.console.print(f"  [yellow]warning:[/yellow] {w}")

        self._print_startup_banner_pre(discoveries)

        ready = [d for d in discoveries if d.skip_reason is None]
        for d in ready:
            self._launch_one(d)

        # Print second half of the banner (final status + tool count).
        self._print_startup_banner_post()

        if not self._runtimes:
            raise RuntimeError("no toolkits could be started")

        return self._proxy_tools

    def _print_startup_banner_pre(self, discoveries: List[ToolkitDiscovery]) -> None:
        self.console.print("\n[bold]Checking installed toolkits...[/bold]")
        for d in discoveries:
            if d.skip_reason is None:
                # We don't yet know tool count for ready ones — fill in
                # after launch. Show "loading" placeholder.
                env = d.env_type
                self.console.print(
                    f"  [dim]…[/dim] [cyan]{d.name:<18}[/cyan] loading ({env})"
                )
            else:
                self.console.print(
                    f"  [yellow]⊘[/yellow] [dim]{d.name:<18}[/dim] "
                    f"[dim]skipped — {d.skip_reason}[/dim]"
                )
                self.logger.log_event(
                    "toolkit_skipped",
                    toolkit=d.name,
                    message=d.skip_reason,
                )
        self.console.print()  # blank line

    def _print_startup_banner_post(self) -> None:
        """After launches complete, print the final per-toolkit verdict."""
        # We don't try to overwrite the "loading" lines (terminal-dependent
        # cursor games we don't want); we just print the final status.
        self.console.print("[bold]Toolkit launch results:[/bold]")
        for name, rt in self._runtimes.items():
            tcount = len(rt.upstream_tool_names)
            self.console.print(
                f"  [green]✓[/green] [cyan]{name:<18}[/cyan] "
                f"ready ({tcount} tool{'s' if tcount != 1 else ''})"
            )
        # Tools that failed to launch were already logged inline by _launch_one.
        total_tools = sum(
            len(rt.upstream_tool_names) for rt in self._runtimes.values()
        )
        self.console.print(
            f"\nStarting MCP server with [bold]{len(self._runtimes)}[/bold] "
            f"toolkit{'s' if len(self._runtimes) != 1 else ''} "
            f"([bold]{total_tools}[/bold] tool{'s' if total_tools != 1 else ''})..."
        )

    # ── per-toolkit launch ──────────────────────────────────────────────

    @dataclass
    class _SpawnResult:
        """Internal: artifacts of a successful spawn → connect sequence.

        Pre-0.4.1 this carried a Popen handle, stderr pump thread, and
        port. With Orchestral 1.4's stdio MCPClient, all of that lives
        inside the client itself — we just hold the client + the tool
        list it surfaced via MCP's ``tools/list``.
        """
        upstream_tools: List[str]
        client: Any  # orchestral.mcp.MCPClient (stdio transport)

    def _spawn_and_connect(
        self,
        disc: ToolkitDiscovery,
        *,
        state_config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional["Orchestrator._SpawnResult"], Optional[str]]:
        """Construct an MCPClient over stdio and connect.

        The MCPClient owns the host subprocess: ``connect()`` spawns it
        with the given ``server_command``, runs the MCP handshake,
        caches the tool list, and holds the session open until
        ``disconnect()``.

        Returns ``(SpawnResult, None)`` on success or ``(None, error)``
        on failure. On failure, the (possibly-spawned) subprocess is
        torn down via ``client.disconnect()``.

        ``state_config`` is forwarded to the host via ``--state-config``
        JSON so ``_inject_state_into_tools`` can populate
        ``@define_tool(state=[...])`` fields before any tool call lands.
        """
        # Pre-create the per-toolkit log so the host's
        # TOOLBASE_HOST_LOG redirect lands in a real file with a
        # session-separator header.
        _prepare_per_toolkit_log(disc.name)

        cmd = _build_host_command(disc, state_config=state_config)
        env = _build_host_env(disc.path, disc.name)

        try:
            from orchestral.mcp import MCPClient
            client = MCPClient(
                server_command=cmd,
                env=env,
                timeout=self._call_timeout_s,
            )
        except Exception as e:
            return None, f"could not construct MCPClient: {e}"

        try:
            client.connect()
        except Exception as e:
            # connect() raises if the host died before MCP init or if
            # the handshake failed. We try to disconnect anyway in case
            # the persistent loop did spawn a subprocess that needs
            # reaping.
            try:
                client.disconnect()
            except Exception:
                pass
            return None, f"mcp connect failed: {e}"

        # MCPClient caches the tool list during connect via tools/list.
        try:
            tool_defs = client.get_tool_definitions()
            upstream_tools = [d["name"] for d in tool_defs]
        except Exception as e:
            try:
                client.disconnect()
            except Exception:
                pass
            return None, f"could not list tools after connect: {e}"

        self.logger.log_event(
            "subprocess_spawned",
            toolkit=disc.name,
            message=f"interpreter={disc.env_type}",
        )

        return Orchestrator._SpawnResult(
            upstream_tools=upstream_tools,
            client=client,
        ), None

    def _launch_one(self, disc: ToolkitDiscovery) -> None:
        """Spawn host, read handshake, connect MCPClient, build proxies.

        On any failure: log clearly, skip this toolkit, keep going with
        the rest. Initial-launch failures do NOT consume the per-toolkit
        restart budget — see the comment on ``RESTART_BUDGET``.

        Phase 3C-1: before spawning, resolve the toolkit's stored
        config against its declared schema. Missing required fields or
        invalid values short-circuit to a skip with a clear pointer to
        ``toolbase config edit <toolkit>``.
        """
        # Resolve declarative state-config first. A missing-required-
        # field condition means we never spawn.
        state_config, config_err = _resolve_state_config(disc)
        if config_err is not None:
            self.console.print(
                f"  [red]✗[/red] [dim]{disc.name:<18}[/dim] "
                f"[red]{config_err}[/red]"
            )
            self.console.print(
                f"     [dim]Edit:[/dim] "
                f"~/.toolbase/config/{disc.name}.yaml"
            )
            self.console.print(
                f"     [dim]Or:[/dim] "
                f"toolbase config edit {disc.name}"
            )
            self.logger.log_event(
                "toolkit_skipped", toolkit=disc.name,
                message=config_err, level="warn",
            )
            return

        spawn, err = self._spawn_and_connect(disc, state_config=state_config)
        if err is not None:
            self.console.print(
                f"  [red]✗[/red] [dim]{disc.name:<18}[/dim] [red]{err}[/red]"
            )
            self.logger.log_event(
                "toolkit_skipped", toolkit=disc.name,
                message=err, level="error",
            )
            return
        assert spawn is not None  # for type checkers

        # Touch ``.last_used`` so ``tb list`` shows the slot was activated.
        # Best-effort — touch_last_used never raises.
        try:
            from ..envs import touch_last_used as _touch_last_used
            _touch_last_used(disc.path)
        except Exception:
            pass

        self.logger.log_event(
            "mcp_client_connected", toolkit=disc.name,
            tool_count=len(spawn.upstream_tools),
        )

        # Build proxies from the canonical MCP listing (richer schema info
        # than the bare tool name list in the handshake).
        from .proxy_tool import make_proxy_tool
        from .bundles import format_skip_log_line

        # The active profile's per-toolkit selection (bundles / enabled /
        # disabled). None when no profile is active (serve-all mode).
        sel = self._selection_for(disc.name)

        # Global absolute blocklist of qualified tool names (serve.yaml
        # default.disabled.tools), restricted to this toolkit.
        global_disabled: set = set()
        if self._profile is not None:
            prefix = f"{disc.name}__"
            for q in self._profile.disabled_tools:
                if q.startswith(prefix):
                    global_disabled.add(q.split("__", 1)[1])

        # 0.5.1: evaluate bundles against the resolved two-layer
        # config. Tools whose ``bundle:`` field names an unavailable
        # bundle are dropped from the served set. One stderr line per
        # dropped bundle, fired once at startup (NOT per call).
        availability, name_to_bundles = _resolve_bundle_availability(disc)
        if availability.dropped_bundles:
            for bname, missing in availability.dropped_bundles.items():
                line = format_skip_log_line(disc.name, bname, missing)
                # stderr console for visibility + serve.log for grepping.
                self.console.print(f"  [yellow]⊘[/yellow] [dim]{line}[/dim]")
                self.logger.log_event(
                    "bundle_skipped",
                    toolkit=disc.name,
                    bundle=bname,
                    reason="missing_config",
                    missing_keys=",".join(missing),
                    level="warn",
                )

        # Forwarder is bound to the toolkit *name*, not the client. The
        # forwarder looks up the live MCPClient on every call so a restart
        # that swaps the client is picked up transparently.
        from .profiles import tool_is_served

        exposed_tools: List[str] = []
        forward = self._make_forwarder(disc.name)
        for defn in spawn.client.get_tool_definitions():
            upstream_name = defn["name"]
            tool_bundles = name_to_bundles.get(upstream_name, [])
            if not tool_is_served(
                upstream_name, tool_bundles, sel, availability, global_disabled
            ):
                continue
            namespaced = f"{disc.name}__{upstream_name}"
            self._proxy_tools.append(make_proxy_tool(
                upstream_name=upstream_name,
                namespaced_name=namespaced,
                description=defn.get("description") or "",
                input_schema=defn.get("inputSchema") or {
                    "type": "object", "properties": {}, "required": []
                },
                forward=forward,
            ))
            exposed_tools.append(upstream_name)

        self._runtimes[disc.name] = ToolkitRuntime(
            name=disc.name,
            path=disc.path,
            upstream_tool_names=exposed_tools,
            mcp_client=spawn.client,
            state=ToolkitState.READY,
            discovery=disc,
        )
        self.logger.log_event(
            "toolkit_loaded", toolkit=disc.name,
            tool_count=len(exposed_tools),
        )

    # ── restart machinery (subprocess crash recovery) ──────────────────

    # See SERVE_ARCHITECTURE.md §3.3 / §3.7 and RESTART_BUDGET above.

    @staticmethod
    def _is_crash_exception(exc: BaseException) -> bool:
        """Decide whether an exception from ``client.call_tool`` indicates
        the subprocess died (vs. the tool itself raising).

        Crash signals: connection-class errors and Orchestral's
        ``MCPSubprocessDiedError`` (the canonical signal under stdio
        transport — the persistent-session loop sets ``_subprocess_died``
        and the next call_tool raises this exception). Tool exceptions
        (RuntimeError, ValueError, etc. raised inside the tool body)
        are *not* crashes — Orchestral catches those upstream and turns
        them into ``isError=True`` MCP results that come back through
        the wire normally; we only see them when something more
        fundamental is wrong.

        Pre-0.4.1 the load-bearing check was ``proc.poll() is not
        None``; with MCPClient owning the subprocess that lever isn't
        ours to pull, but ``MCPSubprocessDiedError`` covers the same
        class of failure with strictly less ambiguity.
        """
        # MCPSubprocessDiedError is the explicit "host process died"
        # signal under Orchestral 1.4 stdio. Match by class name so
        # this module doesn't have to import orchestral.mcp eagerly
        # (it's a heavy import via mcp SDK).
        cls_name = type(exc).__name__
        if cls_name == "MCPSubprocessDiedError":
            return True
        # ConnectionError covers most stdlib-level cases (connection
        # refused, reset, etc.).
        if isinstance(exc, ConnectionError):
            return True
        if cls_name in (
            "ConnectError",         # httpx: TCP connect failed (HTTP transport, kept for safety)
            "RemoteProtocolError",  # httpx: server closed connection mid-stream
            "ReadError",            # httpx: socket read failed
            "BrokenPipeError",      # stdio pipe closed mid-write
        ):
            return True
        # ExceptionGroup / TaskGroup wrapped errors (anyio): unwrap one level.
        inner = getattr(exc, "exceptions", None)
        if inner:
            return any(Orchestrator._is_crash_exception(e) for e in inner)
        return False

    def _classify_call_failure(
        self, rt: ToolkitRuntime, exc: BaseException
    ) -> bool:
        """Return True iff the failure represents a subprocess crash.

        Combines the exception-shape heuristic with the MCPClient's
        ``_subprocess_died`` flag (set by the persistent-session loop
        when the connection drops). Even an unfamiliar exception type
        is a crash if the underlying subprocess is gone.
        """
        if self._is_crash_exception(exc):
            return True
        try:
            return bool(getattr(rt.mcp_client, "_subprocess_died", False))
        except Exception:
            return False

    def _schedule_restart(self, rt: ToolkitRuntime) -> None:
        """If no restart is in flight, start one on a background thread.

        The lock guarantees exactly one restart attempt is queued per
        crash event, even when parallel tool calls all detect the same
        crashed subprocess.
        """
        with rt.restart_lock:
            if rt.state == ToolkitState.STARTING:
                # A restart is already running; nothing to do.
                return
            if rt.state == ToolkitState.FAILED:
                # Permanently failed; no further restart attempts.
                return
            if rt.restart_attempts >= RESTART_BUDGET:
                rt.state = ToolkitState.FAILED
                self.logger.log_event(
                    "toolkit_permanently_failed", toolkit=rt.name,
                    message=rt.last_error or "restart budget exhausted",
                    level="error",
                    attempts=rt.restart_attempts,
                    final_error=rt.last_error or "",
                )
                return
            attempt = rt.restart_attempts + 1
            backoff = RESTART_BACKOFF_S[
                min(attempt - 1, len(RESTART_BACKOFF_S) - 1)
            ]
            rt.state = ToolkitState.STARTING
            self.logger.log_event(
                "restart_scheduled", toolkit=rt.name,
                attempt=attempt, backoff_s=backoff,
            )
            t = threading.Thread(
                target=self._attempt_restart,
                args=(rt, attempt, backoff),
                name=f"restart-{rt.name}-{attempt}",
                daemon=True,
            )
            t.start()

    def _attempt_restart(
        self, rt: ToolkitRuntime, attempt: int, backoff_s: float
    ) -> None:
        """Wait ``backoff_s``, then try to spawn-and-connect again.

        Runs on a daemon thread. On success, swaps in the new subprocess
        and client and returns to READY. On failure, increments the
        attempt counter; if budget remains, schedules the next attempt;
        otherwise marks the toolkit FAILED.
        """
        if self._shutdown_initiated:
            return
        time.sleep(backoff_s)
        if self._shutdown_initiated:
            return

        self.logger.log_event(
            "restart_attempt", toolkit=rt.name, attempt=attempt,
        )

        if rt.discovery is None:
            # Defensive: shouldn't happen for a runtime we built.
            rt.state = ToolkitState.FAILED
            rt.last_error = "missing discovery record"
            self.logger.log_event(
                "toolkit_permanently_failed", toolkit=rt.name,
                message="missing discovery record", level="error",
                attempts=attempt, final_error="missing discovery record",
            )
            return

        # Best-effort cleanup of the prior MCPClient. Each MCPClient
        # owns a daemon thread running its own asyncio loop; abandoning
        # them across many restarts would leak threads and event-loop
        # state. Disconnect failures are non-fatal (the client may
        # already be in a broken state from the connection drop).
        try:
            rt.mcp_client.disconnect()
        except Exception:
            pass

        # MCPClient.disconnect() above tears down the subprocess via the
        # MCP SDK's stdio_client context manager, so we don't need a
        # separate Popen.kill() pass here. Pre-0.4.1 the orchestrator
        # held the Popen handle directly and had to reap it manually.

        # Re-resolve state-config on restart in case the user edited the
        # config file between sessions (the file is canonical; we always
        # read fresh). Same shape as initial launch: a config error here
        # marks the toolkit failed for this restart attempt.
        state_config, config_err = _resolve_state_config(rt.discovery)
        if config_err is not None:
            rt.restart_attempts = attempt
            rt.last_error = config_err
            self.logger.log_event(
                "restart_failed", toolkit=rt.name,
                attempt=attempt, message=config_err, level="warn",
            )
            rt.state = ToolkitState.FAILED
            self.logger.log_event(
                "toolkit_permanently_failed", toolkit=rt.name,
                message=config_err, level="error",
                attempts=attempt, final_error=config_err,
            )
            return

        spawn, err = self._spawn_and_connect(
            rt.discovery, state_config=state_config,
        )
        if err is not None:
            rt.restart_attempts = attempt
            rt.last_error = err
            self.logger.log_event(
                "restart_failed", toolkit=rt.name,
                attempt=attempt, message=err, level="warn",
            )
            if attempt >= RESTART_BUDGET:
                rt.state = ToolkitState.FAILED
                self.logger.log_event(
                    "toolkit_permanently_failed", toolkit=rt.name,
                    message=err, level="error",
                    attempts=attempt, final_error=err,
                )
                # Note: we deliberately do NOT send an MCP
                # `tools/list_changed` notification here. Orchestral's
                # MCPServer exposes no public surface for arbitrary
                # notifications; logging is the best we can do today.
                # See HANDOFF.md "upstream-blocked" entry.
            else:
                # State stays STARTING via _schedule_restart's transition;
                # reset to CRASHED so the next call's _schedule_restart
                # treats it as a fresh schedule (not a reentry). Then
                # schedule the next attempt with the longer backoff.
                rt.state = ToolkitState.CRASHED
                self._schedule_restart(rt)
            return

        # Success. Swap in the new client; keep the same ToolkitRuntime
        # object so the proxy's forwarder (which looks up by name) sees
        # the new client on its next call.
        assert spawn is not None
        rt.mcp_client = spawn.client
        rt.state = ToolkitState.READY
        rt.restart_attempts = attempt
        rt.last_error = ""
        # Per SERVE_ARCHITECTURE.md §3.3: "at most 3 restarts per toolkit
        # per orchestrator session." We count *attempts* (success or
        # failure), not failures. A toolkit that crashes 3 separate times
        # in one session is suspect — silently restarting forever masks
        # the bug. The 4th crash transitions to FAILED via _schedule_restart.
        self.logger.log_event(
            "restart_succeeded", toolkit=rt.name,
            attempt=attempt,
            tool_count=len(spawn.upstream_tools),
        )

    def _make_forwarder(self, toolkit_name: str):
        """Return a closure that the proxy uses to invoke an upstream tool.

        Bound to the toolkit *name*, not its MCPClient. The forwarder
        resolves the live runtime on each call so a restart that swaps
        in a fresh client is picked up transparently. Crash detection
        and restart scheduling happen here.
        """
        logger = self.logger

        def forward(upstream_name: str, kwargs: Dict[str, Any]) -> str:
            rt = self._runtimes.get(toolkit_name)
            if rt is None:
                # Should not happen — runtime is created before any proxy
                # tool that references it. Defensive.
                return (
                    f"Tool unavailable: {toolkit_name} runtime not registered."
                )

            # If the toolkit is in a non-ready state, return guidance
            # without attempting the call. This covers two cases:
            #   - CRASHED: a prior call detected a dead subprocess; a
            #     restart is in flight or will be scheduled by this call.
            #   - FAILED: budget exhausted; no point trying.
            if rt.state == ToolkitState.FAILED:
                return (
                    f"Tool unavailable: subprocess crashed "
                    f"{rt.restart_attempts} times. Marked failed for this "
                    f"serve session. Run toolbase logs for details."
                )
            if rt.state in (ToolkitState.CRASHED, ToolkitState.STARTING):
                # Make sure a restart is queued (idempotent thanks to lock).
                self._schedule_restart(rt)
                return (
                    f"Tool unavailable: restart in progress "
                    f"(attempt {rt.restart_attempts + 1} of {RESTART_BUDGET}). "
                    f"Retry shortly."
                )

            tid = logger.log_tool_start(toolkit_name, upstream_name, kwargs)
            t0 = time.monotonic()
            try:
                result = rt.mcp_client.call_tool(upstream_name, kwargs)
                duration = time.monotonic() - t0
                logger.log_tool_complete(tid, duration=duration, success=True)
                return result
            except Exception as e:
                duration = time.monotonic() - t0
                # Some exceptions (httpx ReadTimeout, anyio cancellations,
                # etc.) stringify to empty. Fall back to the class name so
                # the user gets *something* useful rather than a bare colon.
                detail = str(e) or type(e).__name__
                logger.log_tool_complete(
                    tid, duration=duration, success=False, error=detail,
                )

                if self._classify_call_failure(rt, e):
                    # Subprocess died. Transition to CRASHED, schedule
                    # restart, return guidance.
                    rt.state = ToolkitState.CRASHED
                    rt.last_error = detail
                    self.logger.log_event(
                        "subprocess_crashed", toolkit=toolkit_name,
                        message=detail, level="warn",
                        state_before="ready",
                    )
                    self._schedule_restart(rt)
                    next_attempt = min(
                        rt.restart_attempts + 1, RESTART_BUDGET
                    )
                    return (
                        f"Tool unavailable: subprocess crashed. Automatic "
                        f"restart scheduled (attempt {next_attempt} of "
                        f"{RESTART_BUDGET}). Retry in a few seconds."
                    )

                # Tool error (or transient non-crash failure). Surface
                # the failure to the upstream MCP client (Claude Code)
                # as an error string. Don't touch toolkit state.
                return f"{upstream_name} failed after {duration:.1f}s: {detail}"

        return forward

    # ── serve loop ──────────────────────────────────────────────────────

    def run_mcp_stdio(self) -> None:
        """Run the upstream MCP stdio server. Blocks until shutdown."""
        from orchestral.mcp import MCPServer
        server = MCPServer(
            tools=self._proxy_tools,
            name="toolbase",
            use_display_names=False,  # we already namespaced with __
        )
        try:
            server.run()
        except KeyboardInterrupt:
            pass

    # ── shutdown ────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Disconnect MCP clients, terminate child subprocesses cleanly."""
        if self._shutdown_initiated:
            return
        self._shutdown_initiated = True
        self.logger.log_event("serve_shutting_down")

        for name, rt in list(self._runtimes.items()):
            # MCPClient.disconnect() tears down both the persistent
            # session and the underlying subprocess (via the MCP SDK's
            # stdio_client context manager); pre-0.4.1 we had to
            # SIGTERM Popen ourselves.
            try:
                rt.mcp_client.disconnect()
                self.logger.log_event(
                    "mcp_client_disconnected", toolkit=name,
                )
            except Exception:
                pass

    def _kill_DEPRECATED(self, proc, name: Optional[str] = None) -> None:
        """Graceful → SIGTERM → SIGKILL.

        Pre-0.4.1 the orchestrator owned the Popen handle directly and
        had to reap it itself. With ``MCPClient.disconnect()`` handling
        teardown, this method is unused. Kept temporarily so any leaked
        external caller (mocked in old tests, etc.) still imports
        cleanly until Day 4 sweeps the test suite.
        """
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=SHUTDOWN_GRACEFUL_S)
                return
            except subprocess.TimeoutExpired:
                pass
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception as e:
            if name:
                self.logger.log_event(
                    "subprocess_crashed", toolkit=name,
                    message=f"shutdown error: {e}", level="warn",
                )

    # ── context manager ────────────────────────────────────────────────

    def __enter__(self) -> "Orchestrator":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()


# ── module-level entrypoint used by CLI ─────────────────────────────────


def serve(
    *,
    no_tui: bool = True,
    profile: Optional[Any] = None,
    call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
) -> int:
    """Top-level entry point for ``toolbase serve``.

    For now ``no_tui=False`` is rejected (TUI not implemented yet).

    ``profile`` is the resolved active profile (``serve.profiles.
    ResolvedProfile``) that decides which toolkits and tools to serve.
    The CLI resolves it before calling here; None means "serve every
    discovered toolkit, uncurated" (lower-level / test path).

    ``call_timeout_s`` is the upper bound on each upstream tool call as
    enforced by Orchestral's MCPClient. Default 60 s.
    """
    if not no_tui:
        raise NotImplementedError("TUI mode not yet implemented; use --no-tui")

    # Console must write to stderr so it doesn't corrupt the MCP stdio
    # stream we're handing to Claude Code.
    console = Console(stderr=True)
    orch = Orchestrator(
        console=console, profile=profile, call_timeout_s=call_timeout_s,
    )

    try:
        orch.start()
    except RuntimeError as e:
        console.print(f"[red]Cannot start serve: {e}[/red]")
        return 1
    except Exception as e:
        console.print(f"[red]Startup failed: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        orch.shutdown()
        return 2

    # Install signal handlers so a Ctrl-C tears subprocesses down cleanly.
    def _sigterm(*_):
        orch.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    # If stdin is a TTY, a human ran `toolbase serve` directly. The next
    # thing to happen would be silence forever (this process waits on stdin
    # for MCP JSON-RPC), which looks identical to a hang and is misleading:
    # Claude Code does NOT connect to a running serve, it spawns its own
    # subprocess. So make the framing honest.
    if sys.stdin.isatty():
        console.print(
            "\n[dim]This is a standalone serve process. It will idle until "
            "an MCP client writes JSON-RPC to its stdin.[/dim]"
        )
        console.print(
            "[dim]Note: Claude Code spawns its own `toolbase serve` "
            "subprocess; it does not connect to this one.[/dim]"
        )
        console.print(
            "[dim]To watch tool calls Claude Code makes, run "
            "`toolbase logs` in another terminal. Press Ctrl-C to stop.[/dim]"
        )

    try:
        orch.run_mcp_stdio()
        return 0
    finally:
        orch.shutdown()
