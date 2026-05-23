"""
Toolbase CLI - Command-line interface for managing scientific agentic toolkits.

This module provides the main CLI commands for creating, validating, publishing,
installing, and managing scientific toolkits.
"""

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
import os
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime
from typing import Any, List, Optional, Tuple
import yaml
import tarfile
import tempfile
import requests
import shutil

from .config import _api_url

console = Console()


# ── Agent-friendliness: --yes / --no / --no-input across all commands ──────
#
# Per STATUS.md §"Named principles" (flag-equivalence) and the Tier-1 polish
# pass: every interactive prompt must have a flag-driven equivalent so the
# CLI is usable from coding agents and CI without a TTY.
#
# Conventions:
# - ``--yes`` / ``-y``: answer Yes to all confirms.
# - ``--no``: answer No to all confirms.
# - ``--no-input``: skip prompts entirely. Use the prompt's stated default
#   for confirms; for required text prompts, fail with a clear error
#   pointing at the flag that bypasses it.
# - Non-TTY stdin implicitly sets ``--no-input`` (per manager Q3 answer).
#
# Mutually exclusive: at most one of --yes / --no / --no-input may be set.


def _interactive_options(f):
    """Decorator: add --yes/-y, --no, --no-input to a Click command.

    Apply via ``@_interactive_options`` above other decorators. The flags are
    surfaced as kwargs ``yes``, ``no``, ``no_input`` on the command function.
    Pass them into ``_resolve_prompt_mode()`` to get a single resolved mode.
    """
    f = click.option(
        "--no-input", "no_input", is_flag=True, default=False,
        help="Don't prompt; use defaults or fail. Implied when stdin is not a TTY.",
    )(f)
    f = click.option(
        "--no", "no_", is_flag=True, default=False,
        help="Answer No to all confirmation prompts.",
    )(f)
    f = click.option(
        "-y", "--yes", "yes", is_flag=True, default=False,
        help="Answer Yes to all confirmation prompts.",
    )(f)
    return f


def _resolve_prompt_mode(yes: bool, no_: bool, no_input: bool) -> str:
    """Reduce the three flags + TTY status to a single mode.

    Returns one of:
        "yes"   — accept any confirm
        "no"    — decline any confirm
        "skip"  — non-interactive: confirms use their default; required text
                  prompts fail with a flag-pointing error
        "ask"   — interactive prompt (default in a TTY)
    """
    flags_set = sum(int(b) for b in (yes, no_, no_input))
    if flags_set > 1:
        raise click.UsageError(
            "--yes, --no, and --no-input are mutually exclusive."
        )
    if yes:
        return "yes"
    if no_:
        return "no"
    if no_input:
        return "skip"
    if not sys.stdin.isatty():
        return "skip"
    return "ask"


def _confirm(
    message: str,
    *,
    default: bool,
    mode: str,
    consequential: bool = False,
) -> bool:
    """Confirmation prompt that honors the resolved interactive mode.

    ``consequential=True`` flips the ``skip`` mode's behavior: instead of
    using the prompt's stated default, we treat skip as "no" (refuse to do
    a destructive thing implicitly). Use for deletes, replacements, and
    other irreversible actions.
    """
    if mode == "yes":
        return True
    if mode == "no":
        return False
    if mode == "skip":
        # Consequential prompts never auto-yes in skip mode, even if their
        # interactive default is True. Benign prompts use their default.
        if consequential:
            return False
        return default
    return click.confirm(message, default=default)


def _format_bytes(n: int) -> str:
    """Render a byte count in the largest unit that keeps it >= 1.

    Avoids "0.0 MB" for kilobyte-scale tarballs and "1234567.8 kB" for
    multi-MB ones. Uses 1024-based units throughout (B, kB, MB, GB).
    """
    n = int(n)
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} kB"
    if n < 1024 ** 3:
        return f"{n / (1024 ** 2):.2f} MB"
    return f"{n / (1024 ** 3):.2f} GB"


def _require_input(
    label: str,
    *,
    mode: str,
    bypass_flag: str,
    hide_input: bool = False,
) -> str:
    """Required text prompt. In skip mode, error with a flag pointer.

    Use for inputs that have no sensible default (toolkit name, auth
    token). The error names the flag that supplies the value
    non-interactively.
    """
    if mode == "skip":
        raise click.UsageError(
            f"{label} is required. Pass {bypass_flag} when running "
            "non-interactively."
        )
    return click.prompt(label, hide_input=hide_input)


def _publish_auto_register(
    *,
    toolkit_name: str,
    version: str,
    config: dict,
    mode: str,
) -> bool:
    """Register a toolkit on the registry mid-publish (closes issue #5).

    Called from ``publish`` when the pre-flight GET returned 404, i.e.
    the toolkit name isn't yet registered. Prompts the user (using
    metadata from toolkit.yaml), then POSTs to ``/api/toolkits`` so the
    subsequent upload doesn't fail with an opaque 404.

    Returns True if the toolkit was just registered (so the caller can
    surface a "registered but empty" hint if the upload then fails),
    or exits the process on user decline or registration failure.
    Never returns False: success is the only non-exit code path.
    """
    import requests
    from . import auth as _auth

    category = (config.get("category") or "").strip()
    description = (config.get("description") or "").strip()

    if not category:
        console.print(
            f"[red]✗ Cannot auto-register '{toolkit_name}': "
            "toolkit.yaml is missing a 'category' field.[/red]"
        )
        console.print(
            "Add e.g. [cyan]category: other[/cyan] to toolkit.yaml "
            "(allowed: astro, hep, quantum, bio, chem, materials, "
            "utils, other), or run [cyan]toolbase create[/cyan] "
            "explicitly."
        )
        sys.exit(1)
    if not description:
        console.print(
            f"[red]✗ Cannot auto-register '{toolkit_name}': "
            "toolkit.yaml is missing a 'description' field.[/red]"
        )
        sys.exit(1)

    console.print(
        f"[yellow]✗ Toolkit '{toolkit_name}' is not yet registered on "
        "this registry.[/yellow]\n"
    )
    console.print(
        f"Register and publish v{version} under your account?"
    )
    console.print(f"  Name:        [cyan]{toolkit_name}[/cyan]")
    console.print(f"  Category:    [cyan]{category}[/cyan]")
    console.print(f"  Description: [cyan]{description}[/cyan]")
    console.print()

    approved = _confirm(
        f"Register '{toolkit_name}' on the registry?",
        default=True,
        mode=mode,
        consequential=False,
    )
    if not approved:
        console.print(
            "[yellow]Registration declined; nothing published.[/yellow]"
        )
        console.print(
            "Use [cyan]toolbase create[/cyan] to register the name "
            "without uploading, or re-run [cyan]toolbase publish[/cyan] "
            "and accept the prompt."
        )
        sys.exit(1)

    # Auth required for registration. Reuse the publish stale-token
    # pre-flight here so the user gets a clear message before any
    # HTTP hits the backend.
    _abort_if_stored_token_is_retired()

    token = _auth.load_user_token()
    if not token:
        console.print(
            "[red]✗ Not logged in.[/red] Run [cyan]toolbase login[/cyan] "
            "first, then re-run [cyan]toolbase publish[/cyan]."
        )
        sys.exit(1)

    api_url = _api_url()
    create_url = f"{api_url}/api/toolkits"
    body = {
        "name": toolkit_name,
        "category": category,
        "description": description,
        "version": version,
    }
    headers = {"Authorization": f"Bearer {token}"}

    console.print(
        f"Registering [cyan]{toolkit_name}[/cyan] on the registry..."
    )
    try:
        r = requests.post(create_url, json=body, headers=headers, timeout=15)
    except requests.exceptions.RequestException as e:
        console.print(
            f"[red]✗ Could not reach registry to register: {e}[/red]"
        )
        sys.exit(1)

    if r.status_code == 401:
        console.print(
            "[red]✗ Token rejected by registry.[/red] Run "
            "[cyan]toolbase login[/cyan] to refresh, then re-run "
            "[cyan]toolbase publish[/cyan]."
        )
        sys.exit(1)
    if r.status_code == 409:
        console.print(
            f"[red]✗ Toolkit name '{toolkit_name}' is already taken by "
            "another account.[/red]"
        )
        console.print(
            "Choose a different name in [cyan]toolkit.yaml[/cyan]."
        )
        sys.exit(1)
    if r.status_code == 422:
        try:
            details = r.json().get("detail", "")
        except Exception:
            details = r.text
        console.print(
            f"[red]✗ Registry rejected the registration:[/red]\n  {details}"
        )
        sys.exit(1)
    if not (200 <= r.status_code < 300):
        console.print(
            f"[red]✗ Registration failed (HTTP {r.status_code}): "
            f"{r.text[:200]}[/red]"
        )
        sys.exit(1)

    console.print(
        f"[bold green]✓[/bold green] Registered toolkit "
        f"[cyan]{toolkit_name}[/cyan].\n"
    )
    return True


def _abort_if_stored_token_is_retired() -> None:
    """Pre-flight check: short-circuit if ``~/.toolbase/token`` is stale.

    The 2026-05-15 backend rollover invalidated all ``tb_user_``
    tokens. Any command that authenticates against the registry calls
    this BEFORE making an HTTP request so the user gets a clear
    actionable message ("run toolbase logout && toolbase login")
    instead of an opaque 401 from the backend. Works offline.

    Exits non-zero on hit. No-op when no token is stored or when the
    stored token is fresh (``tb_user_...``).
    """
    from . import auth
    if auth.stored_token_is_retired():
        console.print(
            f"[bold red]✗[/bold red] {auth.STALE_TOKEN_MESSAGE}",
            style="red",
        )
        sys.exit(1)


class _SectionedGroup(click.Group):
    """Click group whose ``--help`` renders commands in named sections.

    Each command's name is checked against ``COMMAND_SECTIONS``; matched
    commands appear under their section header, anything unmatched falls
    through to a generic "Other commands" tail. This keeps the help text
    scannable as the CLI grows past the eight-or-nine-command mark where
    a flat alphabetical list stops being useful.
    """

    COMMAND_SECTIONS = [
        (
            "Authoring & publishing",
            ["create", "init", "ingest", "validate", "login", "logout", "whoami", "publish"],
        ),
        (
            "Installing & serving",
            ["search", "install", "uninstall", "list", "serve", "logs", "groups"],
        ),
        (
            "Configuration",
            ["config", "setup", "project"],
        ),
        (
            "Maintenance",
            ["reset"],
        ),
    ]

    def format_commands(self, ctx, formatter):
        commands = {name: self.get_command(ctx, name) for name in self.list_commands(ctx)}
        commands = {n: c for n, c in commands.items() if c is not None and not c.hidden}

        seen: set[str] = set()
        for header, names in self.COMMAND_SECTIONS:
            rows = []
            for name in names:
                cmd = commands.get(name)
                if cmd is None:
                    continue
                seen.add(name)
                rows.append((name, cmd.get_short_help_str(limit=120)))
            if rows:
                with formatter.section(header):
                    formatter.write_dl(rows)

        # Anything not pre-classified ends up here so a future-added command
        # is still discoverable even before this list is updated.
        rows = [
            (name, cmd.get_short_help_str(limit=120))
            for name, cmd in commands.items()
            if name not in seen
        ]
        if rows:
            with formatter.section("Other commands"):
                formatter.write_dl(rows)


@click.group(cls=_SectionedGroup)
@click.version_option(version="0.1.0", prog_name="toolbase")
@click.option(
    "--project-dir",
    "project_dir_override",
    type=click.Path(file_okay=False, resolve_path=False),
    default=None,
    hidden=True,
    expose_value=False,
    is_eager=True,
    callback=lambda ctx, param, value: _stash_project_dir_override(ctx, value),
    help=(
        "Override project discovery and treat <path> as the active project "
        "root. Power-user / CI / scripting flag — see `tb` documentation."
    ),
)
def main():
    """
    Toolbase - Scientific agentic tools made easy

    A platform for creating, publishing, and using AI tools for science.
    """
    # Phase 6 cutover messaging: surface a one-time-per-invocation heads-up
    # on stderr when the 0.4.x install layout is detected on disk. Stderr
    # (not stdout) keeps machine-readable outputs (``tb list --json``,
    # MCP wire) clean while still surfacing the message to humans and to
    # any caller piping stderr.
    _warn_legacy_layout_if_present()


def _warn_legacy_layout_if_present() -> None:
    """If ``~/.toolbase/toolkits/`` exists with content, print a heads-up.

    The 0.5.0 environments cutover moved installs from
    ``~/.toolbase/toolkits/`` to ``~/.toolbase/cache/<name>/<version>/``.
    Existing 0.4.x installs aren't auto-migrated (Alex authorized a clean
    break). When we see the old dir, we tell the user once per
    invocation and point at ``tb reset``.

    Best-effort: silent on any error. Goes to stderr so JSON / MCP
    consumers aren't affected. The heads-up is suppressed when
    ``TOOLBASE_SUPPRESS_LEGACY_WARNING`` is set in the environment
    (used by tests and by ``tb reset`` itself so the message doesn't
    appear during the very command that cleans it up).
    """
    if os.environ.get("TOOLBASE_SUPPRESS_LEGACY_WARNING"):
        return
    try:
        from .envs import legacy_toolkits_dir
        legacy_dir = legacy_toolkits_dir()
        if not legacy_dir.exists():
            return
        # "Non-empty" means at least one entry. We don't care what's
        # inside — even an aborted install leaves directory bones we
        # should clean up.
        try:
            has_content = any(True for _ in legacy_dir.iterdir())
        except OSError:
            return
        if not has_content:
            return
        # Greppable log line, mirroring the brief's telemetry list.
        try:
            from .logging.logger import get_logger
            get_logger().log_event(
                event="legacy_layout_detected",
                path=str(legacy_dir),
            )
        except Exception:
            pass
        click.echo(
            "Heads up: 0.5.0 adds multi-version installs and per-project pinning,\n"
            f"and moved the install dir from {legacy_dir} to ~/.toolbase/cache/.\n"
            f"The old layout at {legacy_dir} is no longer used.\n"
            "Run `tb reset` to remove it, then `tb install <name>` to repopulate the\n"
            "new layout. See `tb reset --help` for options.",
            err=True,
        )
    except Exception:
        # Never fail a command because the heads-up couldn't be emitted.
        pass


def _stash_project_dir_override(ctx, value):
    """Top-level ``--project-dir`` callback — stash on ctx.obj for later.

    The flag is hidden / eager so it gets parsed before any subcommand
    needs to resolve the active project root. Subcommands (or helpers
    they call) read the override via ``_resolve_active_project_root``.
    """
    if ctx.obj is None:
        ctx.obj = {}
    if value is not None:
        ctx.obj["project_dir_override"] = Path(value)
    return value


def _resolve_active_project_root(
    *,
    cwd: Optional[Path] = None,
    allow_implicit_create: bool = False,
    mode: str = "ask",
    create_message: Optional[str] = None,
):
    """Return the active project root (Path) for the current command.

    Honors (in priority order):
      1. ``--project-dir`` global override (stashed on the Click context).
      2. Discovery walk upward from ``cwd`` for a ``.toolbase/manifest.yaml``.
      3. ``allow_implicit_create=True`` + TTY: prompt to create
         ``.toolbase/`` in ``cwd`` (default-Y).
      4. Fall back to ``~/.toolbase/default-project/``.

    Returns a ``(project_root, source)`` tuple where source is one of
    ``"override" | "walk" | "implicit-create" | "fallback"``. A greppable
    log line ``[toolbase.envs] project_discovered ...`` is emitted on
    every call for debug visibility.

    ``allow_implicit_create`` is True for ``tb install``; False for
    every other command that reads the project (uninstall, serve, list,
    config, setup) — those silently fall back if no project exists,
    matching the "casual user shouldn't be upgraded into a project just
    by reading state" lean.
    """
    from .envs import (
        find_project_root as _find_project_root,
        default_project_root as _default_project_root,
        project_manifest_path as _project_manifest_path,
    )

    # 1. Override stashed by the eager top-level callback.
    ctx = click.get_current_context(silent=True)
    override: Optional[Path] = None
    if ctx is not None and ctx.obj and isinstance(ctx.obj, dict):
        override = ctx.obj.get("project_dir_override")

    if override is not None:
        # Resolve but don't require existence — the override may be a
        # path that doesn't have a .toolbase/ yet (legit for CI seeding).
        resolved = override.resolve()
        _log_project_discovered(resolved, "override")
        return resolved, "override"

    # 2. Walk upward from cwd.
    if cwd is None:
        cwd = Path.cwd()
    found = _find_project_root(cwd=cwd)
    if found is not None:
        _log_project_discovered(found, "walk")
        return found, "walk"

    # 3. Optional implicit creation (only on ``tb install`` in a TTY).
    if allow_implicit_create and mode == "ask":
        msg = create_message or (
            f"No .toolbase/ found above {cwd}. Create one here?"
        )
        try:
            if click.confirm(msg, default=True):
                target = cwd.resolve()
                _materialize_project_dir(target)
                _log_project_discovered(target, "implicit-create")
                return target, "implicit-create"
        except click.exceptions.Abort:
            # User Ctrl-C'd the prompt — fall through to default-project
            # silently. The command they invoked still proceeds; they
            # just don't get a new project dir created behind their back.
            pass

    # 4. Default-project fallback.
    default = _default_project_root()
    _log_project_discovered(default, "fallback")
    return default, "fallback"


def _materialize_project_dir(project_root: Path) -> Path:
    """Create ``<project_root>/.toolbase/`` and an empty manifest.yaml.

    Idempotent — if the dir / manifest already exists, leaves them alone.
    Returns the path to the manifest file.
    """
    from .envs import (
        project_manifest_path as _project_manifest_path,
        save_manifest as _save_manifest,
        Manifest as _Manifest,
    )

    manifest_path = _project_manifest_path(project_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if not manifest_path.exists():
        _save_manifest(manifest_path, _Manifest())
    return manifest_path


def _log_project_discovered(path: Path, source: str) -> None:
    """Emit a greppable ``[toolbase.envs]`` log line for debug visibility.

    Uses the existing ToolLogger if available (so the line lands in
    serve.log when running under ``tb serve``); otherwise falls back
    to writing nothing — the line is for grep, not for normal output.
    """
    try:
        from .logging.logger import get_logger
        logger = get_logger()
        logger.log_event(
            event="project_discovered",
            path=str(path),
            source=source,
        )
    except Exception:
        # Logging is best-effort. Never fail a command because the
        # debug line couldn't be written.
        pass


@main.command()
@click.argument("name")
@click.option(
    "--category", "-c", required=True,
    help=(
        "Toolkit category (e.g. astro, hep, quantum). Validated against "
        "the registry's category list."
    ),
)
@click.option(
    "--description", "-d", required=True,
    help="One-line description of what the toolkit does.",
)
@click.option(
    "--organization",
    default=None,
    help=(
        "Organization name (optional; reserved for future org support). "
        "Currently ignored by the registry."
    ),
)
@click.option(
    "--version",
    default="0.1.0",
    help="Initial version string (default: 0.1.0).",
)
@_interactive_options
def create(name, category, description, organization, version, yes, no_, no_input):
    """
    Create a new toolkit row in the registry.

    Registers a toolkit name, category, and description against
    the Toolbase registry under the authenticated user. Use this before
    `toolbase init <name>` (to scaffold a fresh local toolkit dir)
    or `toolbase ingest .` (to onboard an existing codebase).

    Requires a per-user CLI token (run `toolbase login` first).

    Example:
        toolbase create heptapod --category hep --description "HEP toolkit"
        toolbase create my-toolkit -c astro -d "Astro tools"
    """
    import requests
    from .auth import load_user_token
    from .validation import get_allowed_categories

    mode = _resolve_prompt_mode(yes, no_, no_input)

    # 0. Stale-token pre-flight (post-2026-05-15 rollover). Short-
    # circuits with a clear migration message before any HTTP request
    # if the stored token uses the retired tb_user_ prefix.
    _abort_if_stored_token_is_retired()

    # 1. Auth check.
    token = load_user_token()
    if not token:
        console.print(
            "[bold red]✗[/bold red] Not logged in. "
            "Run [cyan]toolbase login[/cyan] first to authenticate.",
            style="red",
        )
        sys.exit(1)

    # 2. Local validation. Backend re-validates, but loud-failure here
    # avoids a network round-trip for obvious mistakes.
    name_l = name.strip().lower()
    if not name_l.replace("-", "").replace("_", "").isalnum():
        console.print(
            f"[bold red]✗[/bold red] Invalid toolkit name: {name!r}. "
            "Use lowercase alphanumeric + hyphens/underscores only.",
            style="red",
        )
        sys.exit(1)
    if len(name_l) < 3:
        console.print(
            f"[bold red]✗[/bold red] Toolkit name must be at least 3 characters: {name!r}",
            style="red",
        )
        sys.exit(1)

    try:
        allowed_categories = get_allowed_categories()
    except Exception:
        # get_allowed_categories already falls back silently; this is
        # belt-and-suspenders.
        allowed_categories = None
    if allowed_categories and category not in allowed_categories:
        console.print(
            f"[bold red]✗[/bold red] Invalid category: {category!r}. "
            f"Allowed: {', '.join(sorted(allowed_categories))}",
            style="red",
        )
        sys.exit(1)

    if len(description) > 200:
        console.print(
            f"[bold red]✗[/bold red] Description too long "
            f"({len(description)} chars; limit 200).",
            style="red",
        )
        sys.exit(1)

    # 3. Hit the registry.
    api_url = _api_url()
    create_url = f"{api_url}/api/toolkits"
    body = {
        "name": name_l,
        "category": category,
        "description": description,
        "version": version,
    }
    if organization:
        # The endpoint doesn't currently honor this; pass it anyway so
        # backend can pick it up if/when it adds support without a CLI
        # bump.
        body["organization"] = organization

    headers = {"Authorization": f"Bearer {token}"}

    console.print(
        f"Creating toolkit [cyan]{name_l}[/cyan] in [cyan]{category}[/cyan]..."
    )
    try:
        response = requests.post(create_url, json=body, headers=headers, timeout=15)
    except requests.exceptions.RequestException as e:
        console.print(
            f"[bold red]✗[/bold red] Could not reach registry: {e}",
            style="red",
        )
        sys.exit(1)

    if response.status_code == 401:
        console.print(
            "[bold red]✗[/bold red] Token rejected by registry. "
            "Run [cyan]toolbase login[/cyan] to refresh.",
            style="red",
        )
        sys.exit(1)
    if response.status_code == 409:
        console.print(
            f"[bold red]✗[/bold red] Toolkit name {name_l!r} is already taken.",
            style="red",
        )
        sys.exit(1)
    if response.status_code == 422:
        # FastAPI's validation envelope.
        try:
            details = response.json().get("detail", "")
        except Exception:
            details = response.text
        console.print(
            f"[bold red]✗[/bold red] Registry rejected the request:\n  {details}",
            style="red",
        )
        sys.exit(1)
    if not (200 <= response.status_code < 300):
        console.print(
            f"[bold red]✗[/bold red] Registry returned "
            f"HTTP {response.status_code}: {response.text[:200]}",
            style="red",
        )
        sys.exit(1)

    # The endpoint returns {id, name, token}. The token is a legacy
    # per-toolkit publish token; we deliberately don't save it. The
    # user's per-user CLI token already covers publish via
    # auth.load_token_for_publish, and surfacing or persisting the
    # legacy token would just create another credential to manage.
    try:
        payload = response.json()
    except Exception:
        payload = {}
    created_name = payload.get("name", name_l)

    console.print(
        f"\n[bold green]✓[/bold green] Toolkit "
        f"[cyan]{created_name}[/cyan] created."
    )
    console.print("\n[bold]Next steps:[/bold]")
    console.print(
        f"  - [cyan]toolbase init {created_name}[/cyan] "
        "(scaffold a fresh local toolkit directory), or"
    )
    console.print(
        f"  - [cyan]cd[/cyan] into your existing codebase and "
        f"run [cyan]toolbase ingest .[/cyan] (emit a "
        "toolkit.yaml from existing tools)."
    )
    console.print(
        f"  - Then [cyan]toolbase validate[/cyan] and "
        f"[cyan]toolbase publish[/cyan]."
    )


@main.command()
@click.argument('name', required=False)
@click.option(
    '--path', '-p', default=None,
    help='Parent directory to create the toolkit in (default: current dir).',
)
@click.option('--with-docker', is_flag=True, help='Include Dockerfile template')
@click.option(
    '--with-setup', is_flag=True,
    help=(
        'Include Tier-2 setup.py template (and flip setup_script: true '
        'in toolkit.yaml). Use when your toolkit needs interactive setup '
        'beyond the declarative config: block — downloads, hardware '
        'detection, multi-step flows.'
    ),
)
@_interactive_options
def init(name, path, with_docker, with_setup, yes, no_, no_input):
    """
    Initialize a new toolkit from template.

    If the toolkit exists in the registry, pre-fills metadata.
    Otherwise, creates a fresh template.

    Creates a new toolkit directory with the standard structure:
    - toolkit.yaml (metadata; commented-out config: block to uncomment)
    - tools/ (tool definitions)
    - skills/ (skill guides)
    - requirements.txt (dependencies)
    - README.md (documentation)
    - Dockerfile (optional, if --with-docker is used)
    - setup.py (optional, if --with-setup is used)

    Example:
        toolbase init my-awesome-toolkit
        toolbase init my-toolkit --with-docker
        toolbase init my-toolkit --with-setup     # for Tier-2 setup
    """
    from .toolkit import create_toolkit_from_template
    import requests

    mode = _resolve_prompt_mode(yes, no_, no_input)

    # Interactive mode if no name provided
    if not name:
        if mode != "skip":
            console.print(Panel.fit(
                "[bold cyan]Toolbase Initialization[/bold cyan]\n"
                "Let's create your new toolkit!",
                border_style="cyan"
            ))
        name = _require_input("Toolkit name", mode=mode, bypass_flag="NAME (positional argument)")

    # Check if a toolkit by this name is already registered. This is
    # only a name-collision warning: `init` always scaffolds local files
    # regardless. If the name *is* registered (and we have access),
    # we pre-fill toolkit.yaml from the registry's metadata so the user
    # doesn't have to re-type fields they've already set.
    api_url = _api_url()
    registry_metadata = None

    try:
        console.print(f"Checking if '{name}' is already registered...")
        response = requests.get(f"{api_url}/api/toolkits/{name}", timeout=5)

        if response.status_code == 200:
            registry_metadata = response.json()
            latest_version = registry_metadata.get('latest_version', 'unknown')
            console.print(f"[green]✓ Found {name} in registry (v{latest_version})[/green]")
            console.print("Pre-filling metadata from registry...")
        elif response.status_code == 404:
            console.print(
                f"[dim]'{name}' is not yet on the registry — "
                "scaffolding a new local toolkit.[/dim]"
            )
        else:
            console.print(f"[yellow]Could not check registry (status {response.status_code})[/yellow]")
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]Could not connect to registry: {e}[/yellow]")
        console.print("Scaffolding a new local toolkit...")

    # ``--path`` is the *parent directory* in which to create the new
    # toolkit dir; the toolkit's own name is always appended. Matches
    # how `npm create`, `cargo new`, `cookiecutter`, etc. behave —
    # `tb init my-tk --path /tmp` produces /tmp/my-tk/, not overwrites /tmp.
    parent_dir = Path(path) if path else Path.cwd()
    target_path = parent_dir / name

    try:
        create_toolkit_from_template(
            name=name,
            path=target_path,
            with_docker=with_docker,
            with_setup=with_setup,
            registry_metadata=registry_metadata
        )

        # Render the path the user typed (not the macOS-resolved /private/...
        # variant). Substitute $HOME with ~ for compactness.
        display_path = str(target_path)
        home = str(Path.home())
        if display_path.startswith(home):
            display_path = "~" + display_path[len(home):]
        console.print(
            f"\n[bold green]✓[/bold green] Local toolkit scaffold created "
            f"at: [cyan]{display_path}[/cyan]"
        )

        if registry_metadata:
            console.print("\n[bold]Next steps:[/bold]")
            console.print(f"  1. cd {display_path}")
            console.print("  2. Add your tools in the tools/ directory")
            console.print("  3. Run [cyan]toolbase validate[/cyan]")
            console.print("  4. Run [cyan]toolbase login[/cyan] (browser flow)")
            console.print("  5. Run [cyan]toolbase publish[/cyan]")
        else:
            console.print("\n[bold]Next steps:[/bold]")
            console.print(f"  1. cd {display_path}")
            console.print("  2. Edit toolkit.yaml (name, description, author, category)")
            console.print("  3. Add your tools in the tools/ directory")
            console.print("  4. Run [cyan]toolbase validate[/cyan]")
            console.print("  5. Run [cyan]toolbase login[/cyan] (browser flow, one-time)")
            console.print(
                "  6. Run [cyan]toolbase publish[/cyan] "
                "(auto-registers '{0}' on the registry on first run)"
                .format(name)
            )

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Error creating toolkit: {e}", style="red")
        sys.exit(1)


def _print_dropped_warning(dropped, root: Path) -> None:
    """Loud stderr warning about files whose module path didn't resolve."""
    if not dropped:
        return
    err_console = Console(stderr=True)
    n = len(dropped)
    err_console.print(
        f"\n[bold yellow]WARNING:[/bold yellow] {n} file(s) contained "
        "tool definitions but were skipped because their module "
        "path could not be resolved:"
    )
    for d in dropped:
        try:
            rel_str = str(d.source_path.relative_to(root))
        except ValueError:
            rel_str = str(d.source_path)
        err_console.print(
            f"  [yellow]{rel_str}[/yellow]  ([dim]{d.reason}[/dim])"
        )
    err_console.print(
        "[dim]Add the missing __init__.py file(s) and re-run "
        "toolbase ingest to include these tools.[/dim]"
    )


def _print_merge_summary(result) -> None:
    """Render the merge-mode summary (add + report, never auto-remove)."""
    m = result.merge
    root = result.target.parent

    if not result.wrote and not m.added and not m.stale:
        console.print(
            "\n[green]No changes[/green] — toolkit.yaml already matches "
            "source."
        )
        _print_dropped_warning(result.dropped, root)
        return

    console.print("\n[bold green]Merge complete[/bold green]"
                  + (" (toolkit.yaml updated)." if result.wrote else "."))

    if m.added:
        names = ", ".join(f"{t.name} ({t.module})" for t in m.added)
        console.print(
            f"  [green]+[/green] {len(m.added)} new tool"
            f"{'s' if len(m.added) != 1 else ''} added (ungrouped): {names}"
        )
    console.print(
        f"  {m.preserved_count} existing entr"
        f"{'ies' if m.preserved_count != 1 else 'y'} preserved."
    )
    if m.pruned:
        names = ", ".join(f"{name} ({mod})" for mod, name in m.pruned)
        console.print(
            f"  [yellow]-[/yellow] {len(m.pruned)} stale entr"
            f"{'ies' if len(m.pruned) != 1 else 'y'} pruned: {names}"
        )
    elif m.stale:
        for mod, name in m.stale:
            console.print(
                f"  [yellow]![/yellow] yaml entry's source no longer "
                f"found: {name} ({mod})"
            )
        console.print(
            "    [dim](use --prune to remove, or if it was renamed, "
            "re-add the group:/description: by hand)[/dim]"
        )

    _print_dropped_warning(result.dropped, root)

    if m.added or m.stale:
        console.print("\n[bold]Next steps:[/bold]")
        if m.added:
            console.print(
                "  - Assign [cyan]group:[/cyan] to the new tools if you "
                "want tool_groups gating."
            )
        console.print("  - Run [cyan]toolbase validate[/cyan].")


@main.command()
@click.argument(
    "path",
    required=False,
    default=".",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write toolkit.yaml. Default: <PATH>/toolkit.yaml.",
)
@click.option(
    "--force", is_flag=True,
    help=(
        "Overwrite an existing toolkit.yaml from scratch, ignoring its "
        "current contents. Without --force, re-running over an existing "
        "toolkit.yaml merges (see below)."
    ),
)
@click.option(
    "--prune", is_flag=True,
    help=(
        "In merge mode, remove tools: entries whose source is no longer "
        "found (after confirmation). Default is to warn but keep them."
    ),
)
@click.option(
    "--dry-run", is_flag=True,
    help="Print discovered tools and the target path; don't write.",
)
@_interactive_options
def ingest(path, output, force, prune, dry_run, yes, no_, no_input):
    """
    Generate or re-sync a toolkit.yaml from an existing codebase.

    Walks the given directory (default: cwd), discovers tools via
    @define_tool decorators and BaseTool subclass detection. Pure
    static analysis — never imports the modules being scanned.

    \b
    Two modes, auto-detected by whether a toolkit.yaml already exists:
      - No toolkit.yaml  -> scaffold a fresh one from the discovered tools.
      - Existing yaml    -> MERGE: append newly-discovered tools to the
                            tools: list (ungrouped), leave existing entries
                            byte-for-byte untouched (custom description:,
                            group:, ordering, comments all preserved), and
                            report any entries whose source vanished. Only
                            the tools: list is touched — metadata, config:,
                            and tool_groups: are never modified.
      - --force          -> overwrite the yaml from scratch (escape hatch).

    Use --prune to actually remove stale entries in merge mode (default
    is to warn). The merge is a no-op (file untouched) when source and
    yaml already agree.

    \b
    Example (the local dev loop):
        toolbase install -e .     # editable install
        # ... add a new @define_tool ...
        toolbase ingest           # merge it into toolkit.yaml
        toolbase serve            # use it

    The author's code stays where it is. The emitted yaml lists each
    tool by import path.
    """
    from .ingest import ingest as run_ingest

    root = Path(path).resolve()
    target = (output if output else root / "toolkit.yaml")
    if not isinstance(target, Path):
        target = Path(target)
    target = target.resolve()

    mode = _resolve_prompt_mode(yes, no_, no_input)

    existing_at_target = target.is_file()

    # --force is the only path that overwrites from scratch. Without it,
    # an existing toolkit.yaml triggers MERGE mode (non-destructive),
    # not the old overwrite prompt — re-running ingest to pick up new
    # tools is the common case and shouldn't threaten the user's work.
    overwrite = bool(force)

    # --prune confirmation. Removing yaml entries is destructive (a
    # temporarily-commented-out tool could be pruned), so confirm unless
    # the user passed --yes / a non-interactive default.
    effective_prune = prune
    if prune and existing_at_target and not force and not dry_run:
        approved = _confirm(
            "Remove tools: entries whose source is no longer found?",
            mode=mode,
            default=False,
            consequential=True,
        )
        effective_prune = approved

    try:
        result = run_ingest(
            root=root,
            output=output if output else None,
            overwrite=overwrite,
            dry_run=dry_run,
            prune=effective_prune,
        )
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Ingest failed: {e}", style="red")
        sys.exit(1)

    # Summary output.
    fn_descriptors = [t for t in result.tools if t.kind == "function"]
    cls_descriptors = [t for t in result.tools if t.kind == "class"]

    console.print(f"[bold cyan]Scanning[/bold cyan] {root}...")
    console.print(
        f"Found [bold]{len(result.tools)}[/bold] tools "
        f"across [bold]{len({t.module for t in result.tools})}[/bold] modules."
    )
    if fn_descriptors:
        console.print(
            f"\n[cyan]Decorated functions ({len(fn_descriptors)}):[/cyan]"
        )
        for t in fn_descriptors:
            console.print(f"  {t.module}.{t.name}")
    if cls_descriptors:
        console.print(
            f"\n[cyan]BaseTool subclasses ({len(cls_descriptors)}):[/cyan]"
        )
        for t in cls_descriptors:
            console.print(f"  {t.module}.{t.name}")

    if dry_run:
        console.print(
            f"\n[dim](--dry-run; would write to {result.target})[/dim]"
        )
        return

    # Merge-mode summary. Distinct from scaffold/overwrite: report what
    # was added, preserved, and what's stale, without rewriting on a
    # no-op.
    if result.merged and result.merge is not None:
        _print_merge_summary(result)
        return

    if result.wrote:
        console.print(f"\n[bold green]✓[/bold green] Wrote {result.target}.")

    # Loud-warn about files dropped because their dotted module path
    # couldn't be resolved. Silent-drop here used to mean the author
    # shipped a confidently-wrong toolkit.yaml; see issue #1. Goes to
    # stderr so machine-readable consumers can pipe through.
    _print_dropped_warning(result.dropped, root)

    if not result.requirements_present:
        console.print(
            "[yellow]WARNING:[/yellow] requirements.txt not found. "
            "Create one before toolbase publish."
        )
    console.print("\n[bold]Next steps:[/bold]")
    console.print(
        "  - Edit toolkit.yaml metadata "
        "(name, version, category, description, author)."
    )
    if not result.requirements_present:
        console.print(
            "  - Create requirements.txt listing your toolkit's "
            "Python dependencies."
        )
    console.print("  - Run [cyan]toolbase validate[/cyan].")
    console.print("  - Run [cyan]toolbase login[/cyan] (one-time).")
    console.print("  - Run [cyan]toolbase publish[/cyan].")
    # Registration is no longer a required separate step: publish
    # auto-registers an unregistered toolkit on first upload (with a
    # prompt) as of 0.5.5. `toolbase create` and the web UI remain
    # available for pre-registering a name without uploading. See
    # issue #4 / issue #5.
    console.print(
        "    [dim](If the toolkit isn't registered yet, publish will offer "
        "to register it. To reserve the name first, use "
        "[/dim][cyan]toolbase create[/cyan][dim] or "
        "[/dim][cyan]https://toolbase-ai.com[/cyan][dim].)[/dim]"
    )


@main.command()
@click.argument('path', required=False, default='.')
def validate(path):
    """
    Validate a toolkit's structure and configuration.

    Checks:
    - toolbase.yaml exists and is valid
    - Required files are present
    - Tool definitions are valid
    - Dependencies can be parsed

    Example:
        toolbase validate
        toolbase validate ./my-toolkit
    """
    from .validation import validate_toolkit

    toolkit_path = Path(path).resolve()

    console.print(Panel.fit(
        f"[bold cyan]Validating toolkit at:[/bold cyan]\n{toolkit_path}",
        border_style="cyan"
    ))

    try:
        result = validate_toolkit(toolkit_path)

        if result.is_valid:
            console.print("\n[bold green]✓ Toolkit is valid![/bold green]")

            # Show summary
            table = Table(title="Toolkit Summary", show_header=False)
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Name", result.metadata.name)
            table.add_row("Version", result.metadata.version)
            table.add_row("Author", result.metadata.author)
            table.add_row("Tools", str(len(result.metadata.tools)))

            console.print(table)
        else:
            console.print("\n[bold red]✗ Validation failed[/bold red]")
            for error in result.errors:
                console.print(f"  [red]•[/red] {error}")

            # Show warnings (helpful hints)
            if result.warnings:
                console.print()
                for warning in result.warnings:
                    console.print(f"  [yellow]hint:[/yellow] {warning}")

            sys.exit(1)

    except Exception as e:
        console.print(f"\n[bold red]✗[/bold red] Error during validation: {e}", style="red")
        sys.exit(1)


@main.command()
@click.argument('toolkit_name', required=False)
@click.option(
    '--token', 'token_flag', default=None,
    help=(
        'Provide the token non-interactively. With no toolkit argument, '
        'expects a per-user token (tb_user_...). With a toolkit argument, '
        'expects a legacy per-toolkit token (stk_... or toolkit_...).'
    ),
)
@_interactive_options
def login(toolkit_name, token_flag, yes, no_, no_input):
    """
    Authenticate to the Toolbase registry.

    \b
    Modes:
        toolbase login                          # browser-flow (recommended)
        toolbase login --token tb_user_...     # paste a per-user token
        toolbase login <toolkit>                # legacy per-toolkit (deprecated)
        toolbase login <toolkit> --token stk_... # legacy paste mode

    The browser-flow opens https://toolbase-ai.com/cli-auth, asks you to
    approve, and writes the resulting per-user token to ~/.toolbase/token.
    Once logged in, tb publish works for any toolkit you have
    permission on — no per-toolkit login required.

    Per-toolkit tokens are still accepted but deprecated; use the
    browser-flow form for new setups.
    """
    from . import auth

    mode = _resolve_prompt_mode(yes, no_, no_input)

    # ── Branch 1: legacy per-toolkit form (`toolbase login <name>`) ─
    if toolkit_name:
        _login_legacy_toolkit(toolkit_name, token_flag, mode)
        return

    # ── Branch 2: per-user paste mode (`toolbase login --token ...`) ─
    if token_flag is not None:
        _login_paste_user_token(token_flag, mode)
        return

    # ── Branch 3: no toolkit + no token → browser-flow with migration ─
    legacy_files = auth.find_legacy_token_files()
    if legacy_files:
        _login_run_migration_prompt(legacy_files, mode)

    _login_browser_flow(mode)


def _login_legacy_toolkit(toolkit_name: str, token_flag: Optional[str], mode: str) -> None:
    """Old `toolbase login <toolkit>` flow. Writes ~/.toolbase/<name>/token.

    Per the per-user-token migration, this path is deprecated and prints
    a one-line warning. Kept working through Phase B (~30 days) so CI
    pipelines and existing workflows don't break.
    """
    from . import auth

    if token_flag is not None:
        token = token_flag
    else:
        if mode != "skip":
            console.print(
                f"\n[bold blue]Authenticating for toolkit: {toolkit_name}[/bold blue]\n"
            )
            console.print(
                "Per-toolkit tokens are deprecated. The recommended flow "
                "is [cyan]toolbase login[/cyan] (no toolkit argument)."
            )
            console.print(
                "Get a per-toolkit token from "
                "[link]https://toolbase-ai.com[/link] (the toolkit's "
                "management page) if you still need one.\n"
            )
        token = _require_input(
            "Enter the publish token",
            mode=mode,
            bypass_flag="--token",
            hide_input=True,
        )

    token = token.strip()
    if not auth.is_legacy_toolkit_token(token):
        console.print(
            "[yellow]Warning: legacy per-toolkit tokens normally start with "
            "[bold]stk_[/bold] or [bold]toolkit_[/bold]. The token you provided "
            "doesn't match either prefix.[/yellow]"
        )
        if auth.is_user_token(token):
            console.print(
                "It looks like you pasted a per-user token "
                "([bold]tb_user_...[/bold]) into the legacy form. Use "
                "[cyan]toolbase login --token <token>[/cyan] (no toolkit "
                "argument) instead."
            )
            sys.exit(1)
        if auth.is_retired_user_token(token):
            console.print(
                f"[red]✗ {auth.STALE_TOKEN_MESSAGE}[/red]"
            )
            sys.exit(1)
        if not _confirm("Continue anyway?", default=False, mode=mode, consequential=True):
            sys.exit(0)

    path = auth.save_legacy_toolkit_token(toolkit_name, token)

    console.print(f"\n[green]✓ Token stored at: {path}[/green]")
    console.print(
        "\n[yellow]Note:[/yellow] per-toolkit tokens are being phased out. "
        "Run [cyan]toolbase login[/cyan] (no toolkit argument) to "
        "consolidate to a single per-user token."
    )


def _login_paste_user_token(token: str, mode: str) -> None:
    """Non-interactive per-user paste mode."""
    from . import auth

    token = token.strip()
    # Reject retired-prefix tokens before they hit disk. Backend
    # rotated tb_user_ → tb_user_ on 2026-05-15; CLI tokens issued
    # before then no longer work. Catching this here gives a clear
    # actionable message and avoids saving a stale credential that
    # would just fail at the next HTTP call.
    if auth.is_retired_user_token(token):
        console.print(
            "[red]✗ That token uses the retired tb_user_ prefix. "
            "CLI tokens issued before 2026-05-15 no longer work.[/red]"
        )
        console.print(
            "Run [cyan]toolbase login[/cyan] (no --token flag) to "
            "start the browser flow and get a fresh tb_user_ token."
        )
        sys.exit(1)
    if auth.is_legacy_toolkit_token(token):
        console.print(
            "[red]✗ This looks like a legacy per-toolkit token "
            "(stk_... / toolkit_...).[/red]"
        )
        console.print(
            "Use [cyan]toolbase login <toolkit-name> --token <token>[/cyan] "
            "for the legacy form, or generate a per-user token at "
            "[link]https://toolbase-ai.com/profile/cli-tokens[/link]."
        )
        sys.exit(1)
    if not auth.is_user_token(token):
        console.print(
            "[yellow]Warning: per-user tokens normally start with "
            "[bold]tb_user_[/bold]. The token you provided doesn't match."
            "[/yellow]"
        )
        if not _confirm("Continue anyway?", default=False, mode=mode, consequential=True):
            sys.exit(1)

    path = auth.save_user_token(token)
    console.print(f"[green]✓ Token stored at: {path}[/green]")


def _login_run_migration_prompt(
    legacy_files: List[Tuple[str, Path]], mode: str,
) -> None:
    """Surface the legacy-tokens migration prompt before the browser-flow.

    The prompt is informational, not blocking — even if the user
    declines, we still proceed to the browser-flow. The point is to
    explain why they're about to log in and let them know the legacy
    files will keep working but become inert (per-user tokens take
    precedence at publish time).
    """
    names = ", ".join(name for name, _ in legacy_files)
    console.print(
        f"\n[yellow]Detected legacy per-toolkit tokens for:[/yellow] {names}"
    )
    console.print(
        "Generating a per-user token will consolidate authentication. "
        "The legacy files will remain on disk but the per-user token "
        "takes precedence at publish time. To remove the legacy files "
        "later, run [cyan]toolbase logout --clean-legacy[/cyan]."
    )

    proceed = _confirm(
        "Generate a per-user token now?",
        default=True,
        mode=mode,
    )
    if not proceed:
        console.print(
            "[dim]Skipped. Re-run [cyan]toolbase login[/cyan] anytime to "
            "do this later.[/dim]"
        )
        sys.exit(0)


def _login_browser_flow(mode: str) -> None:
    """Run the browser-flow login dance. Stores the resulting per-user token."""
    from . import auth

    if mode == "skip":
        # The browser-flow is interactive by definition; in non-TTY
        # / no-input mode there's no human to approve. Surface the
        # workaround flag.
        raise click.UsageError(
            "Cannot run the browser-flow login non-interactively. "
            "Generate a per-user token at "
            "https://toolbase-ai.com/profile/cli-tokens and pass it via "
            "--token <token>."
        )

    web_base = os.environ.get("TOOLBASE_WEB_URL") or "https://toolbase-ai.com"
    flow = auth.BrowserFlow(web_base=web_base)

    # We don't know the bound port until run() picks one. Print the URL
    # template now so a headless user knows what's about to happen.
    console.print(
        "\n[bold blue]Opening browser for Toolbase login...[/bold blue]"
    )
    console.print(
        "[dim]If your browser doesn't open automatically, the CLI will "
        "print the URL below.[/dim]"
    )
    console.print(
        "[dim]Waiting for approval (timeout: "
        f"{int(auth.BROWSER_FLOW_TIMEOUT_S)}s). Press Ctrl-C to cancel.[/dim]\n"
    )

    try:
        result = flow.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Login cancelled.[/yellow]")
        sys.exit(130)

    if result.timed_out:
        console.print(
            "[red]✗ Login timed out. No token was saved.[/red]"
        )
        console.print(
            "Try again, or generate a token manually at "
            "[link]https://toolbase-ai.com/profile/cli-tokens[/link] and pass "
            "it via [cyan]--token <token>[/cyan]."
        )
        sys.exit(1)

    if result.denied:
        console.print(
            "[yellow]Login denied. No token was saved.[/yellow]"
        )
        sys.exit(1)

    if result.error:
        console.print(f"[red]✗ Login failed: {result.error}[/red]")
        sys.exit(1)

    if not result.token:
        console.print(
            "[red]✗ Login completed but no token was returned. "
            "Please try again.[/red]"
        )
        sys.exit(1)

    if not auth.is_user_token(result.token):
        # Defense in depth — the website should never send anything else,
        # but if it does we want a clear error rather than silently
        # storing a malformed token.
        console.print(
            "[red]✗ The website returned an unexpected token format.[/red]"
        )
        console.print("[dim]Expected tb_user_... prefix.[/dim]")
        sys.exit(1)

    path = auth.save_user_token(result.token)
    console.print(f"\n[green]✓ Logged in. Token stored at: {path}[/green]")
    console.print(
        "Run [cyan]tb whoami[/cyan] to verify, or [cyan]tb publish[/cyan] "
        "from any toolkit you own or collaborate on."
    )


# ────────────────────────────────────────────────────────────────────────
# `toolbase project` — manage project-local manifests.
#
# A project is any directory with a ``.toolbase/manifest.yaml``. Walk-
# upward discovery makes any subdirectory of a project a project member
# too. ``tb install`` in a project pins its toolkit version into the
# project's manifest, so different projects can pin different versions
# of the same toolkit without conflict.
#
# Most users will never run ``tb project init`` explicitly — ``tb
# install`` in a non-project dir prompts (TTY) "create .toolbase/
# here?" and does it for them. This subcommand is the explicit
# alternative for scripts / CI that want to seed the dir up-front.
# ────────────────────────────────────────────────────────────────────────


@main.group()
def project():
    """Manage project-local Toolbase manifests."""
    pass


@project.command(name="init")
@click.option(
    "--path", "target_path",
    type=click.Path(file_okay=False, resolve_path=False),
    default=None,
    help="Directory to initialize (default: cwd).",
)
@_interactive_options
def project_init(target_path, yes, no_, no_input):
    """Create ``.toolbase/`` + empty ``manifest.yaml`` in this directory.

    Idempotent — if a project already exists at the target, prints
    where it is and exits cleanly. The created manifest is empty;
    ``tb install <name>`` will populate it.
    """
    mode = _resolve_prompt_mode(yes, no_, no_input)

    target = Path(target_path) if target_path else Path.cwd()
    target = target.resolve()
    if not target.exists():
        console.print(
            f"[red]✗ Target directory does not exist: {target}[/red]"
        )
        sys.exit(1)
    if not target.is_dir():
        console.print(f"[red]✗ Not a directory: {target}[/red]")
        sys.exit(1)

    from .envs import project_manifest_path as _project_manifest_path

    manifest_path = _project_manifest_path(target)
    if manifest_path.exists():
        console.print(
            f"[yellow]Project already initialized.[/yellow] "
            f"Manifest at: {manifest_path}"
        )
        return

    # Materialize the project dir + empty manifest.
    _materialize_project_dir(target)
    console.print(
        f"[green]✓[/green] Initialized toolbase project at "
        f"[cyan]{target}[/cyan]"
    )
    console.print(f"  Manifest: [dim]{manifest_path}[/dim]")
    console.print(
        "\nPin toolkits with [cyan]tb install <name>[/cyan] from inside "
        "this directory."
    )



@main.command()
@click.option(
    '--clean-legacy', is_flag=True, default=False,
    help='Also remove ~/.toolbase/<toolkit>/token files (legacy per-toolkit tokens).',
)
@_interactive_options
def logout(clean_legacy, yes, no_, no_input):
    """
    Sign out and remove the local CLI token.

    Deletes ~/.toolbase/token (per-user). Best-effort revokes the
    token on the backend (the local file is removed regardless of
    network success). Pass --clean-legacy to also remove any leftover
    ~/.toolbase/<toolkit>/token files from the pre-per-user-token era.
    """
    from . import auth

    mode = _resolve_prompt_mode(yes, no_, no_input)
    user_token = auth.load_user_token()

    if user_token is None and not clean_legacy:
        legacy = auth.find_legacy_token_files()
        if legacy:
            console.print(
                "[yellow]No per-user token found, but legacy per-toolkit "
                "tokens exist:[/yellow] " + ", ".join(n for n, _ in legacy)
            )
            console.print(
                "Run [cyan]toolbase logout --clean-legacy[/cyan] to remove them."
            )
        else:
            console.print("[dim]Already logged out.[/dim]")
        return

    if user_token is not None:
        # Best-effort backend revocation. If the user has many tokens and
        # we don't know which one this is, we can't supply a token_id —
        # the backend resolves the bearer token to its own row. Some
        # backend designs accept "DELETE /cli-tokens/me" or similar; the
        # current shipped contract is "DELETE /cli-tokens/<id>" only,
        # so without a stored id we skip the API call. The local file
        # delete still happens and the user can revoke from the website.
        # If telemetry shows people want better revocation here, we can
        # add a "DELETE /cli-tokens/current" or store the id on save.
        if auth.delete_user_token():
            console.print(
                f"[green]✓ Removed per-user token: {auth.USER_TOKEN_PATH}[/green]"
            )
            console.print(
                "[dim]To revoke this token on the server side too, visit "
                "[link]https://toolbase-ai.com/profile/cli-tokens[/link].[/dim]"
            )

    if clean_legacy:
        legacy = auth.find_legacy_token_files()
        if not legacy:
            console.print("[dim]No legacy per-toolkit tokens to remove.[/dim]")
        else:
            names = ", ".join(n for n, _ in legacy)
            if not _confirm(
                f"Remove legacy tokens for: {names}?",
                default=True,
                mode=mode,
                consequential=True,
            ):
                console.print("[dim]Skipped legacy cleanup.[/dim]")
                return
            removed = auth.delete_legacy_token_files()
            console.print(
                f"[green]✓ Removed {len(removed)} legacy token "
                f"file{'s' if len(removed) != 1 else ''}: "
                f"{', '.join(removed)}[/green]"
            )


@main.command()
def whoami():
    """
    Show which account the current CLI token belongs to.

    Hits the registry's whoami endpoint with whatever token is stored
    locally. Useful sanity check ("am I about to publish as the right
    account?").
    """
    from . import auth

    # Stale-token pre-flight (post-2026-05-15 rollover). Catches the
    # case where a user still has an tb_user_ token in
    # ~/.toolbase/token and would otherwise see an opaque 401 from
    # the backend.
    _abort_if_stored_token_is_retired()

    token = auth.load_user_token()
    if token is None:
        # Fall back to looking at legacy per-toolkit tokens — at least
        # tell the user something useful about what's authenticated.
        legacy = auth.find_legacy_token_files()
        if legacy:
            names = ", ".join(n for n, _ in legacy)
            console.print(
                "[yellow]Not logged in with a per-user token.[/yellow]"
            )
            console.print(
                f"You have legacy per-toolkit tokens for: {names}."
            )
            console.print(
                "Run [cyan]toolbase login[/cyan] to consolidate to a "
                "per-user token."
            )
        else:
            console.print("[yellow]Not logged in.[/yellow]")
            console.print(
                "Run [cyan]toolbase login[/cyan] to authenticate."
            )
        sys.exit(1)

    info = auth.whoami(token)
    if info is None:
        console.print(
            "[red]✗ Could not reach the registry, or the stored token "
            "is invalid.[/red]"
        )
        console.print(
            "Run [cyan]toolbase login[/cyan] to refresh your token, "
            "or check your network connection."
        )
        sys.exit(1)

    email = info.get("email") or "(unknown)"
    name = info.get("name") or info.get("display_name") or ""
    auth_method = info.get("auth_method") or "(unknown)"
    uid = info.get("uid") or info.get("user_id") or ""

    console.print(f"\n[bold]Logged in as:[/bold] {email}")
    if name:
        console.print(f"  Display name: {name}")
    if uid:
        console.print(f"  User ID:      [dim]{uid}[/dim]")
    console.print(f"  Auth method:  {auth_method}")
    console.print(
        f"  Token file:   [dim]{auth.USER_TOKEN_PATH}[/dim]\n"
    )


# ────────────────────────────────────────────────────────────────────────
# `toolbase config` group — Phase 3C-1 file-canonical config management
# ────────────────────────────────────────────────────────────────────────

@main.group()
def config():
    """Manage per-toolkit configuration files.

    Configuration for each installed toolkit lives at
    ~/.toolbase/config/<toolkit>.yaml. These commands view and
    mutate that file. Hand-editing the file directly is also fully
    supported — the file is canonical.
    """
    pass


def _resolve_toolkit_for_config(toolkit_name: str):
    """Common helper: load toolkit.yaml + parsed schema (or None).

    Returns ``(toolkit_yaml_path, schema_or_None)`` for a given
    installed toolkit. ``schema`` is None if the toolkit has no
    ``config:`` block. Errors out (sys.exit 1) if the toolkit isn't
    installed.
    """
    from .setup import parse_config_block
    from .setup.runner import _resolve_toolkit_dir

    try:
        toolkit_dir = _resolve_toolkit_dir(toolkit_name, None)
    except RuntimeError:
        console.print(
            f"[red]✗ Toolkit '{toolkit_name}' is not installed.[/red]"
        )
        console.print(
            f"Run [cyan]toolbase install {toolkit_name}[/cyan] first."
        )
        sys.exit(1)

    yaml_path = toolkit_dir / "toolkit.yaml"
    if not yaml_path.exists():
        console.print(
            f"[red]✗ {yaml_path} is missing — broken install.[/red]"
        )
        sys.exit(1)

    try:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        console.print(f"[red]✗ Could not read {yaml_path}: {e}[/red]")
        sys.exit(1)

    raw_block = data.get("config")
    if not raw_block:
        return yaml_path, None

    try:
        schema = parse_config_block(raw_block)
    except Exception as e:
        console.print(
            f"[yellow]Warning: {toolkit_name}'s config: block is "
            f"malformed: {e}[/yellow]"
        )
        return yaml_path, None
    return yaml_path, schema


def _layer_option(f):
    """Decorator: add ``--layer``/``--user``/``--project`` to a config command.

    Resolves to a single ``layer_flag: Optional[str]`` kwarg with value
    ``"user"``, ``"project"``, or ``None`` (delegate to context-based
    default). Mutually exclusive — passing more than one is a usage error.
    """
    f = click.option(
        "--project", "layer_project", is_flag=True, default=False,
        help="Target the project layer explicitly.",
    )(f)
    f = click.option(
        "--user", "layer_user", is_flag=True, default=False,
        help="Target the user layer explicitly.",
    )(f)
    f = click.option(
        "--layer", "layer_explicit",
        type=click.Choice(["user", "project"]), default=None,
        help="Target a specific config layer (alternative to --user/--project).",
    )(f)
    return f


def _resolve_config_layer(
    *,
    layer_explicit: Optional[str],
    layer_user: bool,
    layer_project: bool,
    default_context: str = "auto",
) -> Tuple[str, Optional[Path]]:
    """Resolve the per-command effective layer.

    Returns ``(layer, project_root)`` where ``project_root`` is non-None
    iff the resolved layer is ``"project"``.

    Priority:
        1. Explicit ``--layer user|project`` flag.
        2. Explicit ``--user`` / ``--project`` flag.
        3. Default: walk discovery. In a project context, default is
           ``"project"``. In default-project context, default is
           ``"user"``.

    The default chosen mirrors the brief's lean: "writes go to project
    in a project; writes go to user in default-project context."
    """
    # Conflicts: at most one explicit choice.
    explicit = []
    if layer_explicit is not None:
        explicit.append(layer_explicit)
    if layer_user:
        explicit.append("user")
    if layer_project:
        explicit.append("project")
    if len(explicit) > 1:
        raise click.UsageError(
            "--layer / --user / --project are mutually exclusive."
        )

    if explicit:
        layer = explicit[0]
    else:
        # Default: peek at discovery to decide.
        project_root, source = _resolve_active_project_root()
        if source == "fallback":
            return "user", None
        # In any real project context (walk / override / implicit-create)
        # the project layer is the default target.
        return "project", project_root

    # Explicit layer specified.
    if layer == "project":
        project_root, _source = _resolve_active_project_root()
        return "project", project_root
    return "user", None


@config.command(name="path")
@click.argument("toolkit_name")
@_layer_option
def config_path_cmd(toolkit_name, layer_explicit, layer_user, layer_project):
    """Print the absolute path to a toolkit's config file.

    Defaults to the active layer for the current context (project layer
    in a project, user layer in default-project). Override with
    ``--user``, ``--project``, or ``--layer user|project``.
    """
    from .setup import config_path as _cfg_path
    _resolve_toolkit_for_config(toolkit_name)  # exits if not installed
    layer, project_root = _resolve_config_layer(
        layer_explicit=layer_explicit,
        layer_user=layer_user, layer_project=layer_project,
    )
    print(_cfg_path(toolkit_name, layer=layer, project_root=project_root))


@config.command(name="show")
@click.argument("toolkit_name")
@_layer_option
def config_show(toolkit_name, layer_explicit, layer_user, layer_project):
    """Show a toolkit's effective configuration.

    Default (no flags): merged view of user + project layers, with each
    key annotated by which layer it came from. Project overrides user
    key-by-key.

    With ``--layer user|project`` (or ``--user``/``--project``): just
    that layer's stored values, no merging.

    Secrets are masked. The ``<NEEDS VALUE>`` sentinel surfaces as-is.
    """
    from .setup import (
        config_path as _cfg_path,
        load_config,
        NEEDS_VALUE_SENTINEL,
    )

    _yaml_path, schema = _resolve_toolkit_for_config(toolkit_name)
    secret_fields = set()
    if schema:
        secret_fields = {
            f.name for f in schema.fields if f.type == "secret"
        }

    # Detect whether the user asked for a single layer or the merged view.
    explicit_layer: Optional[str] = None
    if layer_explicit is not None:
        explicit_layer = layer_explicit
    elif layer_user:
        explicit_layer = "user"
    elif layer_project:
        explicit_layer = "project"

    def _fmt_value(key: str, value: Any) -> str:
        if key in secret_fields and value and value != NEEDS_VALUE_SENTINEL:
            return "[dim]<set>[/dim]"
        if value == NEEDS_VALUE_SENTINEL:
            return f"[yellow]{value}[/yellow]"
        if isinstance(value, str):
            return value
        return repr(value)

    if explicit_layer is not None:
        # Single-layer view — same shape as 3C-1, no annotation.
        if explicit_layer == "project":
            project_root, _source = _resolve_active_project_root()
            cfg_file = _cfg_path(
                toolkit_name, layer="project", project_root=project_root,
            )
            data = load_config(
                toolkit_name, layer="project", project_root=project_root,
            )
        else:
            cfg_file = _cfg_path(toolkit_name, layer="user")
            data = load_config(toolkit_name, layer="user")

        if not cfg_file.exists():
            console.print(
                f"[yellow]No {explicit_layer}-layer config file yet for "
                f"{toolkit_name}.[/yellow] ({cfg_file})"
            )
            return

        console.print(
            f"\n[bold]{toolkit_name}[/bold] [dim]({explicit_layer} layer: "
            f"{cfg_file})[/dim]\n"
        )
        # Skip the schema_version envelope from the displayed body —
        # it's stamped on save but isn't a config value.
        body = {k: v for k, v in data.items() if k != "schema_version"}
        if not body:
            console.print("  [dim](empty)[/dim]")
            return
        for key, value in body.items():
            console.print(
                f"  [cyan]{key}[/cyan]: {_fmt_value(key, value)}"
            )
        return

    # Default: merged view with per-key layer annotations.
    project_root, source = _resolve_active_project_root()
    user_file = _cfg_path(toolkit_name, layer="user")
    project_file = _cfg_path(
        toolkit_name, layer="project", project_root=project_root,
    )

    user_data = load_config(toolkit_name, layer="user")
    project_data = load_config(
        toolkit_name, layer="project", project_root=project_root,
    )

    # Strip schema_version envelopes before merging.
    user_body = {k: v for k, v in user_data.items() if k != "schema_version"}
    project_body = {
        k: v for k, v in project_data.items() if k != "schema_version"
    }

    if not user_body and not project_body:
        console.print(
            f"[yellow]No config file yet for {toolkit_name}.[/yellow]"
        )
        console.print(f"  User layer:    {user_file}")
        console.print(f"  Project layer: {project_file}")
        if schema and schema.fields:
            console.print(
                "\nRun [cyan]toolbase config edit "
                f"{toolkit_name}[/cyan] to create one, or set fields "
                "individually with [cyan]config set[/cyan]."
            )
        return

    # Merge for display order: union of keys, project values winning.
    # Order: user keys first (in file order), then project-only keys.
    all_keys: List[str] = list(user_body.keys())
    for k in project_body:
        if k not in all_keys:
            all_keys.append(k)

    project_label = "default-project" if source == "fallback" else "project"
    console.print(f"\n[bold]{toolkit_name}[/bold]")
    console.print(f"  user layer:    [dim]{user_file}[/dim]")
    console.print(
        f"  {project_label} layer: [dim]{project_file}[/dim]"
    )
    console.print()

    for key in all_keys:
        if key in project_body:
            value = project_body[key]
            layer_tag = "project"
        else:
            value = user_body[key]
            layer_tag = "user"
        console.print(
            f"  [cyan]{key}[/cyan]: {_fmt_value(key, value)}  "
            f"[dim]# from {layer_tag}[/dim]"
        )


@config.command(name="edit")
@click.argument("toolkit_name")
@_layer_option
def config_edit(toolkit_name, layer_explicit, layer_user, layer_project):
    """Open the toolkit's config file in $EDITOR.

    Defaults to the project layer in a project context, the user layer
    in default-project context. Override with ``--user``, ``--project``,
    or ``--layer user|project``.

    If the file doesn't exist yet, a template is dropped first so the
    user lands in a populated buffer. Falls back to nano then vi if
    $EDITOR isn't set.
    """
    from .setup import (
        config_path as _cfg_path,
        load_config,
        save_config,
        parse_config_block,
        NEEDS_VALUE_SENTINEL,
    )

    _resolve_toolkit_for_config(toolkit_name)  # validates install
    layer, project_root = _resolve_config_layer(
        layer_explicit=layer_explicit,
        layer_user=layer_user, layer_project=layer_project,
    )
    cfg_file = _cfg_path(
        toolkit_name, layer=layer, project_root=project_root,
    )

    # Drop a template if the file doesn't exist yet. Templates are only
    # populated against the toolkit's declared schema for the user layer
    # — the project layer is meant for sparse overrides, so we leave it
    # empty (a comment hint is enough).
    if not cfg_file.exists():
        if layer == "user":
            _yaml_path, schema = _resolve_toolkit_for_config(toolkit_name)
            if schema:
                existing = load_config(toolkit_name, layer=layer)
                for f in schema.fields:
                    if f.name in existing:
                        continue
                    if f.default is not None:
                        existing[f.name] = f.default
                    elif f.required:
                        existing[f.name] = NEEDS_VALUE_SENTINEL
                save_config(
                    toolkit_name, existing,
                    layer=layer, project_root=project_root,
                )
            else:
                cfg_file.parent.mkdir(parents=True, exist_ok=True)
                cfg_file.touch()
                try:
                    os.chmod(cfg_file, 0o600)
                except (OSError, NotImplementedError):
                    pass
        else:
            # Project layer: drop an empty schema-versioned file with a
            # header comment hint. Users add only the keys they want to
            # override.
            save_config(
                toolkit_name, {},
                layer="project", project_root=project_root,
                header_comment=(
                    f"Project-layer config for {toolkit_name}.\n"
                    "Only fields set here override the user layer; "
                    "other fields fall through to ~/.toolbase/config/."
                ),
            )

    editor = os.environ.get("EDITOR") or shutil.which("nano") or shutil.which("vi")
    if not editor:
        console.print(
            "[red]✗ No editor available.[/red] Set [cyan]$EDITOR[/cyan] "
            "or install nano/vi."
        )
        console.print(f"You can edit the file directly at: {cfg_file}")
        sys.exit(1)

    try:
        subprocess.call([editor, str(cfg_file)])
    except Exception as e:
        console.print(f"[red]✗ Editor failed: {e}[/red]")
        sys.exit(1)


@config.command(name="set")
@click.argument("toolkit_name")
@click.argument("key")
@click.argument("value")
@_layer_option
def config_set(
    toolkit_name, key, value,
    layer_explicit, layer_user, layer_project,
):
    """Set one config field on a toolkit (preserves other fields/comments).

    Default target layer:
        - In a project context (``.toolbase/`` discovered upward, or
          ``--project-dir`` override): the *project* layer. Smaller
          diffs in git, clearer intent — the project layer file is
          created with just this one key if it didn't exist.
        - In default-project context (no project anywhere upward): the
          *user* layer.

    Override with ``--user``, ``--project``, or ``--layer user|project``.
    """
    from .setup import (
        config_path as _cfg_path,
        coerce_value,
        set_config_value,
        ConfigError,
    )

    _yaml_path, schema = _resolve_toolkit_for_config(toolkit_name)

    parsed: object = value
    if schema is not None:
        field = schema.field_by_name(key)
        if field is None:
            console.print(
                f"[yellow]Warning: {key!r} is not declared in "
                f"{toolkit_name}'s config: schema. Storing as a raw "
                "string anyway.[/yellow]"
            )
        else:
            try:
                parsed = coerce_value(field, value)
            except ConfigError as e:
                console.print(f"[red]✗ {e}[/red]")
                sys.exit(1)

    layer, project_root = _resolve_config_layer(
        layer_explicit=layer_explicit,
        layer_user=layer_user, layer_project=layer_project,
    )
    set_config_value(
        toolkit_name, key, parsed,
        layer=layer, project_root=project_root,
    )
    cfg_file = _cfg_path(
        toolkit_name, layer=layer, project_root=project_root,
    )
    console.print(
        f"[green]✓[/green] {toolkit_name}.{key} set in {layer} layer "
        f"[dim]({cfg_file})[/dim]"
    )


@config.command(name="unset")
@click.argument("toolkit_name")
@click.argument("key")
@_layer_option
def config_unset(
    toolkit_name, key,
    layer_explicit, layer_user, layer_project,
):
    """Remove one config field from a toolkit's config file.

    Same default-layer rules as ``config set``.
    """
    from .setup import unset_config_value

    _resolve_toolkit_for_config(toolkit_name)
    layer, project_root = _resolve_config_layer(
        layer_explicit=layer_explicit,
        layer_user=layer_user, layer_project=layer_project,
    )
    removed = unset_config_value(
        toolkit_name, key, layer=layer, project_root=project_root,
    )
    if removed:
        console.print(
            f"[green]✓[/green] removed {toolkit_name}.{key} from "
            f"{layer} layer"
        )
    else:
        console.print(
            f"[yellow]No such field {key!r} in {toolkit_name}'s "
            f"{layer}-layer config.[/yellow]"
        )


@config.command(name="validate")
@click.argument("toolkit_name")
def config_validate(toolkit_name):
    """Check that all required fields are filled in and types are correct."""
    from .setup import load_state_config

    _yaml_path, schema = _resolve_toolkit_for_config(toolkit_name)
    if schema is None or not schema.fields:
        console.print(
            f"[dim]{toolkit_name} has no config: schema. Nothing to "
            "validate.[/dim]"
        )
        return

    # Validate the merged user+project view — same view the orchestrator
    # uses at serve startup.
    project_root, _source = _resolve_active_project_root()
    resolution = load_state_config(
        toolkit_name, schema, project_root=project_root,
    )
    if resolution.ok:
        n = len(resolution.state_config)
        console.print(
            f"[green]✓[/green] {toolkit_name} config is valid "
            f"({n} field{'s' if n != 1 else ''})"
        )
        return

    console.print(f"[red]✗ {toolkit_name} config is incomplete:[/red]")
    if resolution.missing_required:
        console.print(
            "  Missing required: "
            + ", ".join(resolution.missing_required)
        )
    for name, err in resolution.invalid:
        console.print(f"  Invalid {name}: {err}")
    sys.exit(1)


@main.command()
@click.argument("toolkit_name")
@click.option(
    "--reset", is_flag=True, default=False,
    help=(
        "Delete the toolkit's config file before re-running setup. "
        "Useful when credentials change or you want a fresh start."
    ),
)
@click.option(
    "--check", is_flag=True, default=False,
    help=(
        "Run validate(ctx) only; don't run setup(ctx). Useful to "
        "diagnose why a toolkit refuses to serve."
    ),
)
@_interactive_options
def setup(toolkit_name, reset, check, yes, no_, no_input):
    """
    Run a toolkit's setup.py script.

    Tier-2 toolkits (those with a setup.py at root) use this command to
    run their interactive setup flow. Use it to:

    \b
    - Re-run setup after install (e.g., new credentials needed)
    - Run setup that was skipped during install (--no-prompt mode)
    - Trigger setup-script logic like data downloads

    \b
    Examples:
        toolbase setup aster              # run setup.py::setup(ctx)
        toolbase setup aster --reset      # clear config, re-run setup
        toolbase setup aster --check      # run validate(ctx) only
    """
    from .setup import (
        run_setup_script, validate_setup_script,
        run_install_setup, parse_config_block,
        delete_config, config_path,
    )
    from .setup.runner import SetupResult, _resolve_toolkit_dir

    if reset and check:
        raise click.UsageError("--reset and --check are mutually exclusive.")

    mode = _resolve_prompt_mode(yes, no_, no_input)

    try:
        toolkit_dir = _resolve_toolkit_dir(toolkit_name, None)
    except RuntimeError:
        console.print(
            f"[red]✗ Toolkit '{toolkit_name}' is not installed.[/red]"
        )
        console.print(
            f"Run [cyan]toolbase install {toolkit_name}[/cyan] first."
        )
        sys.exit(1)

    setup_py_file = toolkit_dir / "setup.py"

    # ── --check mode ──────────────────────────────────────────────
    if check:
        if not setup_py_file.exists():
            console.print(
                f"[dim]{toolkit_name} has no setup.py; nothing to "
                "validate (Tier-1 toolkit).[/dim]"
            )
            return
        result = validate_setup_script(toolkit_name)
        if result.ok:
            console.print(
                f"[green]✓[/green] {toolkit_name}: validate(ctx) passed"
            )
            return
        console.print(
            f"[red]✗[/red] {toolkit_name}: validate(ctx) failed"
        )
        if result.message:
            console.print(f"  {result.message}")
        if result.log_path:
            console.print(f"  Full log: [cyan]{result.log_path}[/cyan]")
        sys.exit(1)

    # ── --reset mode ──────────────────────────────────────────────
    if reset:
        cfg = config_path(toolkit_name)
        if cfg.exists():
            confirm_msg = (
                f"Reset will delete {cfg} and re-run setup. Continue?"
            )
            if not _confirm(
                confirm_msg, default=False, mode=mode, consequential=True,
            ):
                console.print("[dim]Aborted.[/dim]")
                return
            delete_config(toolkit_name)
            console.print(f"[dim]Deleted {cfg}[/dim]")

    # ── run Tier 1 first if a config: block is declared ────────────
    yaml_path = toolkit_dir / "toolkit.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path) as f:
                toolkit_meta = yaml.safe_load(f) or {}
        except Exception:
            toolkit_meta = {}
        config_block = toolkit_meta.get("config")
        if config_block:
            try:
                schema = parse_config_block(config_block)
                run_install_setup(toolkit_name, schema, mode=mode)
            except Exception as e:
                console.print(
                    f"[yellow]Tier-1 declarative setup raised: {e}. "
                    "Continuing to setup.py.[/yellow]"
                )

    # ── run Tier 2 (setup.py) if present ───────────────────────────
    if not setup_py_file.exists():
        console.print(
            f"[dim]{toolkit_name} has no setup.py; Tier-1 setup "
            "complete (or no-op if no config: block).[/dim]"
        )
        return

    console.print(f"Running [cyan]{toolkit_name}[/cyan] setup script...")
    result = run_setup_script(toolkit_name, prompt_mode=mode)

    if result.ok:
        console.print(
            f"[green]✓[/green] {toolkit_name} setup complete."
        )
        return

    # Failure
    console.print(f"[red]✗[/red] {toolkit_name} setup failed.")
    if result.message:
        console.print(f"  {result.message}")
    if result.traceback:
        # Show a short summary; full traceback goes to log file.
        first_lines = result.traceback.strip().splitlines()
        if first_lines:
            console.print(f"  {first_lines[-1]}")
    if result.log_path:
        console.print(f"  Full log: [cyan]{result.log_path}[/cyan]")
    sys.exit(1)


@main.command()
@click.option(
    '--dry-run', is_flag=True,
    help='Validate and package, but skip auth and upload.',
)
@click.option(
    '--allow-version-decrease', 'allow_decrease', is_flag=True, default=False,
    help=(
        'Allow publishing a version lower than the latest already on the '
        'registry. Use only when you know what you are doing — most users '
        'should bump the version forward.'
    ),
)
@_interactive_options
def publish(dry_run, allow_decrease, yes, no_, no_input):
    """
    Publish toolkit to the Toolbase registry.

    Packages the current directory as a tarball and uploads it. Requires
    authentication via `toolbase login`. If the toolkit name has not
    yet been registered on the registry, prompts to register it on the
    spot (using metadata from toolkit.yaml), then uploads — so a brand-
    new toolkit can be shipped with just `toolbase login` + `toolbase
    publish`. Use `toolbase create` explicitly if you want to reserve
    a name without uploading code yet.

    \b
    Lifecycle:
        tb validate                 # check structure
        tb login                    # one-time, stores user token
        tb publish --dry-run        # local sanity check
        tb publish                  # ship it (auto-registers on first run)

    \b
    Examples:
        tb publish
        tb publish --dry-run
        tb publish -y               # auto-accept the registration prompt
        tb publish --allow-version-decrease   # rare; emergency rollbacks
    """
    mode = _resolve_prompt_mode(yes, no_, no_input)
    console.print("\n[bold blue]Publishing toolkit to Toolbase registry...[/bold blue]\n")

    # Step 1: Find and read toolkit.yaml
    yaml_path = Path.cwd() / 'toolkit.yaml'
    if not yaml_path.exists():
        console.print("[red]✗ Error: toolkit.yaml not found in current directory[/red]")
        console.print("Make sure you're in the toolkit root directory.")
        sys.exit(1)

    try:
        with open(yaml_path, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]✗ Error reading toolkit.yaml: {e}[/red]")
        sys.exit(1)

    toolkit_name = config.get('name')
    version = config.get('version')

    if not toolkit_name or not version:
        console.print("[red]✗ Error: toolkit.yaml must contain 'name' and 'version' fields[/red]")
        sys.exit(1)

    console.print(f"Toolkit: [bold]{toolkit_name}[/bold]")
    console.print(f"Version: [bold]{version}[/bold]\n")

    # Step 2: Validate toolkit structure
    console.print("Validating toolkit structure...")

    from .validation import validate_toolkit

    result = validate_toolkit(Path.cwd())
    if not result.is_valid:
        console.print("[red]✗ Validation failed:[/red]")
        for error in result.errors:
            console.print(f"  [red]•[/red] {error}")
        console.print("\nRun 'toolbase validate' for details.")
        sys.exit(1)

    console.print("[green]✓ Toolkit structure is valid[/green]\n")

    # Step 2b: Pre-flight version check against the registry. Catches the
    # two common ways to get bounced at upload: re-publishing an existing
    # version, and accidentally regressing the version number. Skip for
    # --dry-run (offline) and when the user explicitly asked for a
    # decrease via --allow-version-decrease.
    #
    # Side benefit: this GET also tells us whether the toolkit is
    # registered at all. A 404 here means "name not yet on the
    # registry"; we record that and chain a `POST /api/toolkits`
    # later, after building the tarball (Step 2c2).
    needs_registration = False
    if not dry_run:
        from .versioning import is_strictly_greater, max_version, suggest_next_version
        api_url_check = _api_url()
        try:
            r = requests.get(
                f"{api_url_check}/api/toolkits/{toolkit_name}", timeout=5,
            )
        except requests.exceptions.RequestException:
            r = None
        if r is not None and r.status_code == 404:
            needs_registration = True
        if r is not None and r.status_code == 200:
            try:
                tk_meta = r.json()
                versions = [
                    v.get("version") for v in tk_meta.get("versions") or []
                    if isinstance(v, dict) and v.get("version")
                ]
            except Exception:
                versions = []
            if version in versions:
                suggested = suggest_next_version(version) or "<bumped version>"
                console.print(
                    f"[red]✗ Version {version} already exists on the "
                    f"registry for {toolkit_name}.[/red]"
                )
                console.print(
                    f"Bump the version in [cyan]toolkit.yaml[/cyan] "
                    f"(e.g. [bold]version: {suggested}[/bold]) and re-run."
                )
                sys.exit(1)
            latest = max_version(versions)
            if latest:
                gt = is_strictly_greater(version, latest)
                if gt is False and not allow_decrease:
                    console.print(
                        f"[red]✗ Version {version} is not greater than "
                        f"the latest published version ({latest}).[/red]"
                    )
                    console.print(
                        "Pass [cyan]--allow-version-decrease[/cyan] if you "
                        "really need to publish an older version, or bump "
                        "the version in [cyan]toolkit.yaml[/cyan]."
                    )
                    sys.exit(1)
                if gt is False and allow_decrease:
                    # Telemetry: how often does this escape hatch fire?
                    # If never, deprecate the flag. If often, the rule
                    # was wrong.
                    from .logging.logger import get_logger
                    get_logger().log_event(
                        event="version_decrease_allowed",
                        toolkit=toolkit_name,
                        message=f"publishing {version} over latest {latest}",
                        level="warn",
                        from_version=latest,
                        to_version=version,
                    )
                    console.print(
                        f"[yellow]Warning: publishing {version} which is "
                        f"older than the registry's latest ({latest}). "
                        "Logged for telemetry.[/yellow]"
                    )
                if gt is None:
                    # Unparseable; not our place to block, registry will
                    # decide. Just warn.
                    console.print(
                        f"[yellow]Could not compare version {version} "
                        f"with registry's {latest} — proceeding anyway.[/yellow]"
                    )
        # If the request failed or returned non-200, fall through silently.
        # The registry itself is the final authority — it will reject on
        # upload if there's a real conflict.

    # Step 2c: Auto-register on first publish (closes issue #5).
    #
    # When the version pre-flight GET returned 404, the toolkit name
    # isn't yet registered. Prompt the user, then POST /api/toolkits
    # with metadata from toolkit.yaml so we don't bail out at upload
    # time with an opaque 404. Done before the tarball build so a
    # decline ("n") or registration collision (409) doesn't waste work.
    was_just_registered = False
    if needs_registration:
        was_just_registered = _publish_auto_register(
            toolkit_name=toolkit_name,
            version=version,
            config=config,
            mode=mode,
        )

    # Step 3: Create tarball. We do this before reading the token so that
    # `--dry-run` (whose whole purpose is "test the package without
    # uploading") doesn't require the user to have authenticated yet.
    console.print("Creating tarball...")

    tarball_name = f"{toolkit_name}-{version}.tar.gz"
    tarball_path = Path(tempfile.gettempdir()) / tarball_name

    try:
        create_tarball(Path.cwd(), tarball_path, toolkit_name)
        console.print(
            f"[green]✓ Created {tarball_name} "
            f"({_format_bytes(tarball_path.stat().st_size)})[/green]\n"
        )
    except Exception as e:
        console.print(f"[red]✗ Error creating tarball: {e}[/red]")
        sys.exit(1)

    if dry_run:
        console.print("[yellow]Dry run mode — skipping auth and upload[/yellow]")
        console.print(f"Tarball created at: {tarball_path}")
        console.print("\nTo publish for real, run: [cyan]toolbase publish[/cyan]")
        return

    # Step 4: Read authentication token (real publishes only).
    #
    # Resolution order (per docs/PER_USER_TOKEN_DESIGN.md):
    #   1. ~/.toolbase/token              — per-user CLI token (preferred)
    #   2. ~/.toolbase/<toolkit>/token    — legacy per-toolkit fallback
    #
    # The backend accepts both during the migration window; the CLI just
    # picks the per-user one when available.
    from . import auth as _auth

    # Stale-token pre-flight (post-2026-05-15 rollover). Catches an
    # tb_user_ token in ~/.toolbase/token before the upload hits
    # the backend's 401. (The legacy per-toolkit fallback isn't
    # affected — that's a separate deprecation track.)
    _abort_if_stored_token_is_retired()

    token, source = _auth.load_token_for_publish(toolkit_name)
    if token is None:
        console.print(
            f"[red]✗ Error: No authentication token found for '{toolkit_name}'[/red]"
        )
        console.print(
            "\nRun [cyan]toolbase login[/cyan] to authenticate "
            "(per-user, recommended)."
        )
        console.print(
            f"Or [cyan]toolbase login {toolkit_name} --token <stk_...>[/cyan] "
            "for a legacy per-toolkit token."
        )
        sys.exit(1)

    if source == "user":
        console.print(
            f"Using per-user token from: [dim]{_auth.USER_TOKEN_PATH}[/dim]\n"
        )
    else:
        console.print(
            "Using legacy per-toolkit token from: "
            f"[dim]{_auth.legacy_token_path(toolkit_name)}[/dim]"
        )
        console.print(
            "[dim]Per-toolkit tokens are being phased out. Run "
            "[cyan]toolbase login[/cyan] to consolidate.[/dim]\n"
        )

    # Step 5: Upload to backend
    console.print("Uploading to registry...")

    api_url = _api_url()
    upload_url = f"{api_url}/api/toolkits/{toolkit_name}/publish"

    try:
        with open(tarball_path, 'rb') as f:
            files = {'file': (tarball_name, f, 'application/gzip')}
            headers = {'Authorization': f'Bearer {token}'}

            # Show progress
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Uploading...", total=None)

                response = requests.post(
                    upload_url,
                    files=files,
                    headers=headers,
                    timeout=300  # 5 minutes
                )

        # Clean up temp file
        tarball_path.unlink()

        if response.status_code == 201:
            data = response.json()
            console.print("\n[bold green]✓ Successfully published![/bold green]\n")
            console.print(f"Toolkit:   {data['toolkit_name']}")
            console.print(f"Version:   {data['version']}")
            console.print(f"Size:      {data['file_size'] / (1024*1024):.2f} MB")
            console.print(f"Published: {data['published_at']}")
            console.print(f"\nView at: [link]https://toolbase-ai.com/toolkit/{toolkit_name}[/link]")

        elif response.status_code == 409:
            from .versioning import suggest_next_version
            console.print(
                f"\n[red]✗ Version {version} already exists for {toolkit_name}.[/red]"
            )
            suggested = suggest_next_version(version)
            if suggested:
                console.print(
                    f"Bump the version in [cyan]toolkit.yaml[/cyan] "
                    f"(e.g. [bold]version: {suggested}[/bold]) and re-run "
                    "[cyan]toolbase publish[/cyan]."
                )
            else:
                console.print(
                    "Bump the version in [cyan]toolkit.yaml[/cyan] and "
                    "re-run [cyan]toolbase publish[/cyan]."
                )
            console.print(
                "[dim]Tip: run `toolbase validate` before publishing — "
                "it catches version-already-exists locally.[/dim]"
            )
            sys.exit(1)

        elif response.status_code == 401:
            console.print("\n[red]✗ Authentication failed. Invalid token.[/red]")
            if source == "user":
                console.print(
                    "Run [cyan]toolbase login[/cyan] to re-authenticate. "
                    "Use [cyan]toolbase whoami[/cyan] to check who the "
                    "current token belongs to."
                )
            else:
                console.print(
                    f"Run [cyan]toolbase login[/cyan] to switch to a "
                    f"per-user token, or [cyan]toolbase login "
                    f"{toolkit_name} --token <new>[/cyan] to update the "
                    "legacy per-toolkit token."
                )
            sys.exit(1)
        elif response.status_code == 403:
            console.print(
                "\n[red]✗ You don't have permission to publish "
                f"{toolkit_name}.[/red]"
            )
            console.print(
                "Ask the toolkit's owner to add you as a collaborator, "
                "or check [cyan]toolbase whoami[/cyan] to confirm which "
                "account this token authenticates as."
            )
            sys.exit(1)

        else:
            try:
                error = response.json()
                error_msg = error.get('detail', 'Unknown error')
            except Exception:
                error_msg = response.text or 'Unknown error'

            console.print(f"\n[red]✗ Upload failed: {error_msg}[/red]")
            console.print(f"Status code: {response.status_code}")
            if was_just_registered:
                console.print(
                    f"[yellow]The toolkit '{toolkit_name}' was just "
                    "registered.[/yellow] Fix the issue and re-run "
                    "[cyan]toolbase publish[/cyan] (no need to register "
                    "again)."
                )
            sys.exit(1)

    except requests.exceptions.RequestException as e:
        console.print(f"\n[red]✗ Network error: {e}[/red]")
        console.print("Please check your internet connection and try again.")
        if was_just_registered:
            console.print(
                f"[yellow]The toolkit '{toolkit_name}' was just "
                "registered.[/yellow] Re-run [cyan]toolbase publish[/cyan] "
                "once the network is back (no need to register again)."
            )
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]✗ Unexpected error: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


@main.command()
@click.argument('query', required=False)
@click.option('--category', '-c', help='Filter by category (astro, hep, quantum, etc.)')
def search(query, category):
    """
    Search for toolkits in the registry.

    Example:
        toolbase search exoplanet
        toolbase search --category astro
        toolbase search transit --category astro
    """
    console.print("[yellow]The search command is not yet implemented.[/yellow]")
    console.print("This will be added in Phase 3 of the development.")
    sys.exit(1)


def get_current_python() -> str:
    """
    Get current Python version in 'X.Y' format.

    Returns:
        str: Python version (e.g., '3.11')
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def has_conda() -> bool:
    """
    Check if conda or mamba is available.

    Returns:
        bool: True if conda/mamba is available
    """
    return shutil.which('conda') is not None or shutil.which('mamba') is not None


def load_toolkit_yaml(toolkit_path: Path) -> dict:
    """
    Load and parse toolkit.yaml.

    Args:
        toolkit_path: Path to toolkit directory

    Returns:
        dict: Parsed toolkit configuration

    Raises:
        FileNotFoundError: If toolkit.yaml doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    yaml_path = toolkit_path / 'toolkit.yaml'
    if not yaml_path.exists():
        raise FileNotFoundError(f"Missing toolkit.yaml in {toolkit_path}")

    with open(yaml_path) as f:
        return yaml.safe_load(f)


def detect_environment_type(toolkit_path: Path, config: dict) -> tuple:
    """
    Detect the appropriate environment type for a toolkit.

    Implements auto-detection logic:
    - Explicit docker_required: true → docker
    - Has Dockerfile → docker
    - Different Python version + conda available → conda
    - Different Python version + no conda → docker (with warning)
    - Same Python version → venv

    Args:
        toolkit_path: Path to toolkit directory
        config: Parsed toolkit.yaml configuration

    Returns:
        tuple: (env_type, python_version)
            env_type: 'venv', 'conda', or 'docker'
            python_version: Required Python version (e.g., '3.11')
    """
    env_config = config.get('environment', {})

    # 1. Explicit docker requirement
    if env_config.get('docker_required'):
        python_version = env_config.get('python', get_current_python())
        return ('docker', python_version)

    # 2. Has Dockerfile
    if (toolkit_path / 'Dockerfile').exists():
        python_version = env_config.get('python', get_current_python())
        return ('docker', python_version)

    # 3. Check Python version requirement
    required_py = env_config.get('python', get_current_python())
    current_py = get_current_python()

    if required_py != current_py:
        # Different Python version needed
        if has_conda():
            return ('conda', required_py)
        else:
            # No conda available, will need Docker
            return ('docker', required_py)

    # 4. Default: venv (same Python version, pure Python deps)
    return ('venv', current_py)


def _run_pip_with_progress(
    cmd: list,
    console: Console,
    label: str,
) -> None:
    """Run a pip subprocess, streaming live status messages from its output.

    Pip emits ``Collecting <pkg>`` and ``Installing collected packages: ...``
    lines on stdout. We stream those into a Rich ``Status`` spinner so the
    user can see what's happening during long installs (otherwise the step
    looks frozen for 30+ seconds while building wheels).

    Pip has no machine-readable progress; this is best-effort cosmetic. Full
    output is buffered and replayed on failure so the diagnostic is still
    available.
    """
    import re

    collecting_re = re.compile(r"^Collecting\s+([A-Za-z0-9._\-]+)")
    installing_re = re.compile(r"^Installing collected packages:\s*(.+)$")
    building_re = re.compile(r"^Building wheel for\s+([A-Za-z0-9._\-]+)")

    captured: list[str] = []
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    with console.status(f"[bold blue]{label}...") as status:
        assert proc.stdout is not None
        for line in proc.stdout:
            captured.append(line)
            line = line.rstrip()
            if not line:
                continue
            m = collecting_re.match(line)
            if m:
                status.update(f"[bold blue]{label}: collecting {m.group(1)}...")
                continue
            m = building_re.match(line)
            if m:
                status.update(f"[bold blue]{label}: building wheel for {m.group(1)}...")
                continue
            m = installing_re.match(line)
            if m:
                pkgs = m.group(1).strip()
                # Truncate long lists so the status line doesn't wrap.
                if len(pkgs) > 60:
                    pkgs = pkgs[:57] + "..."
                status.update(f"[bold blue]{label}: installing {pkgs}...")
                continue

    rc = proc.wait()
    if rc != 0:
        # Replay the captured output so the user sees pip's actual error.
        console.print("".join(captured))
        raise subprocess.CalledProcessError(rc, cmd, output="".join(captured))


def setup_venv_environment(toolkit_path: Path, console: Console) -> Path:
    """
    Create virtual environment and install dependencies.

    Args:
        toolkit_path: Path to toolkit directory
        console: Rich console for output

    Returns:
        Path: Path to Python executable in venv

    Raises:
        subprocess.CalledProcessError: If venv creation or pip install fails
    """
    venv_path = toolkit_path / '.venv'
    requirements_path = toolkit_path / 'requirements.txt'

    # Create venv
    with console.status("[bold blue]Creating virtual environment..."):
        subprocess.run(
            [sys.executable, '-m', 'venv', str(venv_path)],
            check=True,
            capture_output=True
        )
    console.print("[green]✓ Virtual environment created[/green]")

    # Get pip and python paths (platform-specific)
    if sys.platform == 'win32':
        pip_path = venv_path / 'Scripts' / 'pip.exe'
        python_path = venv_path / 'Scripts' / 'python.exe'
    else:
        pip_path = venv_path / 'bin' / 'pip'
        python_path = venv_path / 'bin' / 'python'

    # Upgrade pip first
    with console.status("[bold blue]Upgrading pip..."):
        subprocess.run(
            [str(pip_path), 'install', '--upgrade', 'pip', '--quiet'],
            check=True,
            capture_output=True
        )

    # Install dependencies from requirements.txt
    if requirements_path.exists():
        _run_pip_with_progress(
            [str(pip_path), 'install', '-r', str(requirements_path)],
            console,
            "Installing dependencies",
        )
        console.print("[green]✓ Dependencies installed[/green]")
    else:
        console.print("[dim]No requirements.txt — toolkit has no dependencies[/dim]")

    # Install orchestral-ai + mcp SDK. Both are required: orchestral provides
    # the @define_tool decorator and tool plumbing; mcp is what the per-toolkit
    # subprocess (toolbase serve's host) uses to expose tools over HTTP.
    _run_pip_with_progress(
        [str(pip_path), 'install', 'orchestral-ai', 'mcp'],
        console,
        "Installing orchestral-ai and mcp",
    )
    console.print("[green]✓ Orchestral + MCP SDK installed[/green]")

    return python_path


def verify_conda_available():
    """
    Check if conda or mamba is available.

    Raises:
        click.ClickException: If conda/mamba not found
    """
    if not has_conda():
        raise click.ClickException(
            "Conda/Mamba not found!\n\n"
            "This toolkit requires a different Python version.\n"
            "Please install conda or mamba:\n"
            "  - Miniconda: https://docs.conda.io/en/latest/miniconda.html\n"
            "  - Mamba: https://mamba.readthedocs.io/\n\n"
            "Alternatively, Docker mode (Phase 3B) will support this toolkit."
        )


def cleanup_conda_environment(env_name: str):
    """
    Remove conda environment if it exists.

    Args:
        env_name: Name of conda environment to remove
    """
    conda_cmd = 'mamba' if shutil.which('mamba') else 'conda'
    try:
        subprocess.run(
            [conda_cmd, 'env', 'remove', '-n', env_name, '-y', '--quiet'],
            capture_output=True
        )
    except Exception:
        pass  # Best effort cleanup


def setup_conda_environment(
    toolkit_path: Path,
    toolkit_name: str,
    python_version: str,
    console: Console
) -> str:
    """
    Create conda environment and install dependencies.

    Args:
        toolkit_path: Path to toolkit directory
        toolkit_name: Name of toolkit
        python_version: Required Python version (e.g., '3.9')
        console: Rich console for output

    Returns:
        str: Conda environment name

    Raises:
        subprocess.CalledProcessError: If conda commands fail
    """
    env_name = f"toolbase-{toolkit_name}"
    requirements_path = toolkit_path / 'requirements.txt'

    # Prefer mamba (faster) if available, fallback to conda
    conda_cmd = 'mamba' if shutil.which('mamba') else 'conda'

    with console.status(f"[bold blue]Creating conda environment '{env_name}'..."):
        # Create conda environment with specific Python version
        try:
            subprocess.run(
                [conda_cmd, 'create', '-n', env_name, f'python={python_version}', '-y', '--quiet'],
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Failed to create conda environment[/red]")
            if e.stderr:
                console.print(f"[red]Error: {e.stderr[:500]}[/red]")
            raise

    console.print(f"[green]✓ Conda environment '{env_name}' created (Python {python_version})[/green]")

    # Install dependencies from requirements.txt
    if requirements_path.exists():
        try:
            _run_pip_with_progress(
                [conda_cmd, 'run', '-n', env_name, 'pip', 'install',
                 '-r', str(requirements_path)],
                console,
                f"Installing dependencies in '{env_name}'",
            )
            console.print("[green]✓ Dependencies installed[/green]")
        except subprocess.CalledProcessError:
            console.print(f"[yellow]Some dependencies failed to install[/yellow]")
            # Don't raise - might be non-critical
    else:
        console.print("[dim]No requirements.txt found[/dim]")

    # Install orchestral-ai + mcp SDK (see venv setup for rationale).
    try:
        _run_pip_with_progress(
            [conda_cmd, 'run', '-n', env_name, 'pip', 'install',
             'orchestral-ai', 'mcp'],
            console,
            f"Installing orchestral + mcp in '{env_name}'",
        )
    except subprocess.CalledProcessError:
        console.print(f"[red]✗ Failed to install orchestral/mcp[/red]")
        raise

    console.print("[green]✓ Orchestral + MCP SDK installed[/green]")

    return env_name


# Source-dir entries symlinked into an editable cache slot. ``tools`` is
# a directory symlink so new ``.py`` files added under it appear live on
# the next serve; the rest are the files serve / the host read directly
# off the slot. ``.venv`` is intentionally NOT in this list — it's built
# as a real subdir of the slot so it never lands in the user's source.
_EDITABLE_SYMLINK_ENTRIES = (
    "toolkit.yaml",
    "tools",
    "skills",
    "setup.py",
    "requirements.txt",
    "environment.yml",
    "README.md",
)

# Cache-slot version sentinel for editable installs. Unparseable by
# ``parse_version`` (so it sorts last in ``tb list``) and disjoint from
# any real semver, so it can never collide with a registry version slot.
EDITABLE_VERSION = "editable"


def _resolve_install_source_path(arg: str) -> Optional[Path]:
    """Return a resolved Path if ``arg`` is a local path, else None.

    pip-style disambiguation: an argument is a path when it is ``.`` /
    ``..``, contains a path separator, or resolves to an existing
    directory. Otherwise it's a registry name. The returned path is
    resolved but not validated for toolkit.yaml here — callers do that
    with a target-specific error message.
    """
    if arg in (".", ".."):
        return Path(arg).resolve()
    if "/" in arg or os.sep in arg or (os.altsep and os.altsep in arg):
        return Path(arg).resolve()
    candidate = Path(arg)
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()
    return None


def _pin_after_install(name: str, version: str, *, local_scope: bool) -> None:
    """Pin (name, version) into the global or active-project manifest.

    ``local_scope=False`` (the -g default) pins into the global
    default-project manifest. ``local_scope=True`` (-l) pins into the
    active project's manifest, creating ``.toolbase/`` in cwd if no
    project is found above it. Best-effort: a pin failure warns but
    doesn't fail the install (the cache slot is already usable; serve
    falls back to walking the cache).
    """
    try:
        from .envs import (
            project_manifest_path as _project_manifest_path,
            add_pin as _add_pin,
            default_project_root as _default_project_root,
        )
        if local_scope:
            # -l: pin into THIS project. find_project_root walks up for
            # an existing .toolbase/; if none, create one in cwd.
            from .envs import find_project_root as _find_project_root
            found = _find_project_root(cwd=Path.cwd())
            if found is None:
                project_root = Path.cwd().resolve()
                _materialize_project_dir(project_root)
            else:
                project_root = found
            manifest_path = _project_manifest_path(project_root)
            _add_pin(manifest_path, name, version)
            try:
                rel = manifest_path.relative_to(Path.cwd())
                display = f"./{rel}"
            except ValueError:
                display = str(manifest_path)
            console.print(f"[dim]Pinned to this project: {display}[/dim]")
        else:
            # -g (default): pin into the global default-project.
            project_root = _default_project_root()
            manifest_path = _project_manifest_path(project_root)
            _add_pin(manifest_path, name, version)
    except Exception as e:
        console.print(
            f"[dim]Note: could not pin {name} to the manifest: {e}[/dim]"
        )


def _install_from_path(
    source_path: Path,
    *,
    editable: bool,
    local_scope: bool,
    no_skills: bool,
    mode: str,
) -> None:
    """Install a toolkit from a local source directory.

    Covers two cases:
      - ``-e`` (editable): symlink the source's content into a cache slot
        keyed ``editable``, build the venv from the source's
        requirements, and do NOT pin into the committed manifest (the
        path is machine-specific). Live: edits to source ``.py`` files
        appear on the next serve.
      - ``-g``/``-l`` from a path (non-editable): copy the source into a
        normal versioned cache slot (version read from toolkit.yaml) and
        pin per scope, same as a registry install.
    """
    yaml_path = source_path / "toolkit.yaml"
    if not yaml_path.exists():
        console.print(
            f"[red]✗ No toolkit.yaml found at {source_path}. "
            "Is this a toolkit directory?[/red]"
        )
        sys.exit(1)

    try:
        with open(yaml_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        console.print(f"[red]✗ Could not read toolkit.yaml: {e}[/red]")
        sys.exit(1)

    name = (config.get("name") or "").strip()
    if not name:
        console.print(
            "[red]✗ toolkit.yaml is missing a 'name' field.[/red]"
        )
        sys.exit(1)

    from .envs import cache_dir as _envs_cache_dir

    if editable:
        version = EDITABLE_VERSION
        console.print(
            f"\n[bold blue]Installing {name} (editable) from "
            f"{source_path}[/bold blue]\n"
        )
    else:
        version = (config.get("version") or "").strip()
        if not version:
            console.print(
                "[red]✗ toolkit.yaml is missing a 'version' field.[/red]"
            )
            sys.exit(1)
        console.print(
            f"\n[bold blue]Installing {name} v{version} from "
            f"{source_path}[/bold blue]\n"
        )

    slot = _envs_cache_dir(name, version)

    # Detect env type up front (refuse docker before doing work).
    try:
        env_type, python_version = detect_environment_type(source_path, config)
    except Exception as e:
        console.print(f"[red]✗ Environment detection error: {e}[/red]")
        sys.exit(1)
    if env_type == "docker":
        console.print(
            "[red]✗ This toolkit requires Docker mode, which is not yet "
            "supported.[/red]"
        )
        sys.exit(1)

    # Reinstall handling: a pre-existing slot is replaced. For editable
    # this is the normal "I changed deps, rebuild" path.
    if slot.exists() or slot.is_symlink():
        if not editable:
            console.print(
                f"[yellow]{name} v{version} is already installed.[/yellow]"
            )
            if not _confirm("Reinstall?", default=True, mode=mode):
                sys.exit(0)
        _remove_slot(slot)

    slot.mkdir(parents=True, exist_ok=True)

    # Materialize the source into the slot.
    if editable:
        _symlink_source_into_slot(source_path, slot)
        env_source_dir = slot  # symlinks resolve to live source
    else:
        # Non-editable path install: copy the tree so the slot is a
        # frozen snapshot, exactly like a registry tarball extract.
        for item in source_path.iterdir():
            if item.name in (".venv", ".git", "__pycache__"):
                continue
            dest = slot / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".git",
                ))
            else:
                shutil.copy2(item, dest)
        env_source_dir = slot

    # Build the environment in the slot (venv lands at <slot>/.venv).
    console.print()
    python_path = None
    env_name = None
    try:
        if env_type == "venv":
            console.print("[bold blue]Setting up environment...[/bold blue]\n")
            python_path = setup_venv_environment(env_source_dir, console)
        elif env_type == "conda":
            verify_conda_available()
            console.print("[bold blue]Setting up environment...[/bold blue]\n")
            env_name = setup_conda_environment(
                env_source_dir, name, python_version, console,
            )
    except Exception as e:
        console.print(f"\n[red]✗ Environment setup failed: {e}[/red]")
        _remove_slot(slot)
        raise click.ClickException("Installation failed")

    # Write metadata. For editable, record editable: true + source_path
    # so tb list can render the live-link indicator and serve can read
    # the venv interpreter.
    _write_path_install_meta(
        slot,
        name=name,
        version=version,
        env_type=env_type,
        python_version=python_version,
        python_path=python_path,
        env_name=env_name,
        config=config,
        editable=editable,
        source_path=source_path if editable else None,
    )

    console.print(
        f"\n[bold green]✓ Successfully installed {name} "
        f"{'(editable)' if editable else 'v' + version}[/bold green]\n"
    )
    if editable:
        console.print(f"Source: [cyan]{source_path}[/cyan] (live link)")
        console.print(
            "[dim]Edits to tool source appear on the next `toolbase "
            "serve`. If you change dependencies, re-run "
            "`toolbase install -e .` to rebuild the env.[/dim]"
        )
    if env_type == "venv":
        console.print(f"Environment: venv (Python {python_version})")
    elif env_type == "conda":
        console.print(
            f"Environment: conda env '{env_name}' (Python {python_version})"
        )

    # Skills surfacing (same as registry install). Reads from the slot,
    # which for editable resolves through the symlink to live source.
    _surface_skills_best_effort(name, slot, no_skills)

    # Pinning. Editable installs deliberately stay OUT of the committed
    # manifest — a machine-specific path won't resolve on a collaborator's
    # clone. The editable: true + source_path in .install_meta.yaml is the
    # only place an editable install is tracked.
    if not editable:
        _pin_after_install(name, version, local_scope=local_scope)
    else:
        console.print(
            "[dim]Editable installs are not pinned into the project "
            "manifest (the path is machine-specific).[/dim]"
        )

    console.print(f"\n[bold]Ready to use! Try:[/bold]")
    console.print(f"  [cyan]tb list[/cyan]")
    console.print(f"  [cyan]tb serve {name}[/cyan]")
    console.print()


def _remove_slot(slot: Path) -> None:
    """Remove a cache slot, whether it's a real dir or a symlink."""
    if slot.is_symlink():
        slot.unlink()
    elif slot.exists():
        shutil.rmtree(slot)


def _symlink_source_into_slot(source_path: Path, slot: Path) -> None:
    """Symlink the toolkit's source entries into an editable cache slot.

    ``tools/`` is linked as a directory symlink so newly-added tool
    modules appear live. Only entries that exist in the source are
    linked. ``.venv`` is never linked — it's built as a real subdir of
    the slot so the user's source tree stays clean.
    """
    for entry in _EDITABLE_SYMLINK_ENTRIES:
        src = source_path / entry
        if not src.exists():
            continue
        link = slot / entry
        try:
            link.symlink_to(src.resolve(), target_is_directory=src.is_dir())
        except OSError as e:
            # Windows without symlink privilege, or an exotic FS. Fall
            # back to a copy with a clear staleness note.
            console.print(
                f"[yellow]Could not symlink {entry} ({e}); copying "
                "instead. Source edits will NOT be live for this "
                "entry until you re-run install -e.[/yellow]"
            )
            if src.is_dir():
                shutil.copytree(src, link)
            else:
                shutil.copy2(src, link)


def _write_path_install_meta(
    slot: Path,
    *,
    name: str,
    version: str,
    env_type: str,
    python_version: str,
    python_path,
    env_name,
    config: dict,
    editable: bool,
    source_path: Optional[Path],
) -> None:
    """Write .tb_meta.json + .install_meta.yaml for a path/editable install."""
    from .envs import (
        write_legacy_meta as _write_legacy_meta,
        write_install_meta as _write_install_meta,
        compute_and_write_disk_size as _compute_and_write_disk_size,
    )

    skills_dir = slot / "skills"
    if skills_dir.exists():
        skill_files = sorted(
            p for p in skills_dir.glob("*.md") if not p.name.startswith("._")
        )
    else:
        skill_files = []
    tools_count = len(config.get("tools", []) or [])
    has_setup_script = (slot / "setup.py").exists()

    meta = {
        "name": name,
        "version": version,
        "environment": env_type,
        "python_version": python_version,
        "tools_count": tools_count,
        "has_skills": len(skill_files) > 0,
        "skills_count": len(skill_files),
        "has_setup_script": has_setup_script,
        "needs_setup": has_setup_script,
        "installed_at": datetime.now().isoformat(),
    }
    if editable:
        meta["editable"] = True
        meta["source_path"] = str(source_path)
    if env_type == "venv":
        meta["python_path"] = str(python_path)
    elif env_type == "conda":
        meta["env_name"] = env_name
    _write_legacy_meta(slot, meta)

    extras: dict = {}
    if env_type == "venv":
        extras["python_path"] = str(python_path)
    elif env_type == "conda":
        extras["env_name"] = env_name
    extras["tools_count"] = tools_count
    extras["has_skills"] = len(skill_files) > 0
    extras["skills_count"] = len(skill_files)
    extras["has_setup_script"] = has_setup_script
    if editable:
        extras["editable"] = True
        extras["source_path"] = str(source_path)
    _write_install_meta(
        slot,
        name=name,
        version=version,
        install_method=env_type or "venv",
        python_version=python_version or "?",
        extras=extras,
    )

    try:
        _compute_and_write_disk_size(slot)
    except Exception:
        pass


def _surface_skills_best_effort(name: str, slot: Path, no_skills: bool) -> None:
    """Surface a toolkit's skills into ~/.claude/skills/ (best-effort)."""
    skills_dir = slot / "skills"
    if not skills_dir.exists() or no_skills:
        return
    skill_files = sorted(
        p for p in skills_dir.glob("*.md") if not p.name.startswith("._")
    )
    if not skill_files:
        return
    console.print(
        f"Skills: {len(skill_files)} "
        f"guide{'s' if len(skill_files) != 1 else ''} available"
    )
    try:
        from .skills import install_skills_for_toolkit, CLAUDE_SKILLS_DIR
        surfaced = install_skills_for_toolkit(name, slot)
        if surfaced:
            console.print(
                f"[dim]Surfaced to {CLAUDE_SKILLS_DIR}/ "
                f"({len(surfaced)} entr"
                f"{'ies' if len(surfaced) != 1 else 'y'})[/dim]"
            )
    except Exception as e:
        console.print(
            f"[yellow]Could not surface skills to ~/.claude/skills: {e}[/yellow]"
        )


@main.command()
@click.argument('name')
@click.option('--version', '-v', help='Specific version to install (default: latest)')
@click.option(
    '--global', '-g', 'global_scope', is_flag=True, default=False,
    help=(
        'Global install (the default): pin into the global default-project '
        'manifest. Accepts a registry name or a path to a toolkit dir.'
    ),
)
@click.option(
    '--local', '-l', 'local_scope', is_flag=True, default=False,
    help=(
        "Local install: pin into THIS project's manifest "
        "(<project>/.toolbase/manifest.yaml), creating the project if "
        "needed. Binary still lives in the global cache. Accepts a "
        "registry name or a path to a toolkit dir."
    ),
)
@click.option(
    '--editable', '-e', 'editable', is_flag=True, default=False,
    help=(
        'Editable install: symlink a local toolkit source dir into the '
        'cache so serve loads tools live. Path only (no registry name). '
        'Not pinned into the committed manifest.'
    ),
)
@click.option(
    '--no-skills', 'no_skills', is_flag=True, default=False,
    help="Don't surface the toolkit's skills into ~/.claude/skills/.",
)
@_interactive_options
def install(name, version, global_scope, local_scope, editable, no_skills, yes, no_, no_input):
    """
    Install a toolkit — from the registry or a local source directory.

    \b
    Scope/source flags (mutually exclusive; -g is the default):
      -g / --global    Pin into the global default-project (the default).
      -l / --local     Pin into THIS project's manifest (.toolbase/).
      -e / --editable  Live symlink to a local source dir (path only).

    \b
    The toolkit binary (venv/conda env + tools) always lives in the
    global cache at ~/.toolbase/cache/<name>/<version>/, regardless
    of flag. -g vs -l only changes which manifest gets the pin; -e
    additionally points the cache slot at your live source folder.

    \b
    The argument is a registry name OR a local path. It's treated as a
    path when it is ``.``/``..``, contains a path separator, or resolves
    to an existing directory; otherwise as a registry name. A path
    target must contain a toolkit.yaml. (-e requires a path.)

    \b
    This will:
      1. Acquire the toolkit (download from registry, or read a local path)
      2. Create an isolated environment (venv or conda, auto-detected)
      3. Install dependencies, then orchestral-ai + mcp
      4. Surface the toolkit's skills into ~/.claude/skills/ (unless --no-skills)
      5. Pin into the appropriate manifest (-g default-project, -l this project)

    \b
    Examples:
        toolbase install aster                   # global, latest
        toolbase install aster@1.2.0             # pin a version via @ syntax
        toolbase install aster --version 1.2.0   # pin a version via flag
        toolbase install -l aster                # pin into this project
        toolbase install .                        # global install from cwd
        toolbase install -e .                     # editable: live link to cwd
        toolbase install aster --no-skills        # don't touch ~/.claude/skills/
    """
    mode = _resolve_prompt_mode(yes, no_, no_input)

    # Flag exclusivity. -e/-l/-g pick one scope/source; -g is the
    # default when none is given.
    if sum(int(b) for b in (editable, local_scope, global_scope)) > 1:
        raise click.UsageError(
            "-e, -l, and -g are mutually exclusive. Pick one."
        )

    # Resolve the argument to either a registry name or a local path,
    # following pip-style disambiguation. ``-e`` forces path semantics
    # and rejects a bare name.
    source_path = _resolve_install_source_path(name)
    if editable and source_path is None:
        raise click.UsageError(
            "Editable installs require a path to a local toolkit "
            "directory.\n  Usage: tb install -e <path-to-toolkit>"
        )
    if editable and version:
        raise click.UsageError(
            "--version is meaningless with -e (editable has no registry "
            "version). Drop --version."
        )

    # Path-source branch (covers -e always, and -g/-l when the arg is a
    # path). Builds the cache slot from the local dir and pins per scope.
    if source_path is not None:
        _install_from_path(
            source_path,
            editable=editable,
            local_scope=local_scope,
            no_skills=no_skills,
            mode=mode,
        )
        return

    # Registry-name branch below (the common case).
    # Parse name@version syntax. Both `aster@1.2.0` and `aster --version 1.2.0`
    # work; conflict (both forms specifying versions) raises.
    if "@" in name:
        bare_name, _, suffix_version = name.partition("@")
        if not bare_name or not suffix_version:
            raise click.UsageError(
                f"Bad name@version syntax: {name!r} (need both sides of @)."
            )
        if version and version != suffix_version:
            raise click.UsageError(
                f"Conflicting versions: '@{suffix_version}' vs --version {version}."
            )
        name = bare_name
        if not version:
            version = suffix_version

    console.print(f"\n[bold blue]Installing toolkit: {name}[/bold blue]\n")

    # Step 1: Fetch toolkit metadata from registry
    console.print("Fetching toolkit metadata...")

    api_url = _api_url()

    try:
        response = requests.get(f"{api_url}/api/toolkits/{name}", timeout=10)

        if response.status_code == 404:
            console.print(f"[red]✗ Toolkit '{name}' not found in registry[/red]")
            console.print("\nSearch for toolkits: [cyan]toolbase search {query}[/cyan]")
            sys.exit(1)

        if response.status_code != 200:
            console.print(f"[red]✗ Error fetching metadata (status {response.status_code})[/red]")
            sys.exit(1)

        toolkit_meta = response.json()

        # Determine version to install
        if not version:
            version = toolkit_meta.get('latest_version')
            if not version:
                console.print(f"[red]✗ Toolkit has no published versions[/red]")
                sys.exit(1)
            console.print(f"[green]✓ Found {name} v{version} (latest)[/green]")
        else:
            # Verify version exists.
            available_versions = toolkit_meta.get('versions', [])
            version_numbers = [
                v.get('version') for v in available_versions
                if isinstance(v, dict) and v.get('version')
            ]
            if version not in version_numbers:
                console.print(f"[red]✗ Version {version} not found for {name}.[/red]")
                if version_numbers:
                    # Show newest first; users almost always want recent.
                    from .versioning import parse_version
                    sorted_versions = sorted(
                        version_numbers,
                        key=lambda v: parse_version(v) or (0, 0, 0),
                        reverse=True,
                    )
                    shown = sorted_versions[:5]
                    extra = (
                        f" (and {len(sorted_versions) - 5} older)"
                        if len(sorted_versions) > 5 else ""
                    )
                    console.print(
                        f"Available versions: {', '.join(shown)}{extra}"
                    )
                    console.print(
                        f"\nInstall the latest with: "
                        f"[cyan]toolbase install {name}[/cyan]"
                    )
                else:
                    console.print(f"This toolkit has no published versions yet.")
                sys.exit(1)
            console.print(f"[green]✓ Found {name} v{version}[/green]")

    except requests.exceptions.RequestException as e:
        console.print(f"[red]✗ Network error: {e}[/red]")
        sys.exit(1)

    # Step 2: Check if this specific (name, version) is already installed.
    #
    # Phase 2 cache layout: each (name, version) lives in its own slot at
    # ``~/.toolbase/cache/<name>/<version>/``. Different versions of the
    # same toolkit coexist side-by-side. We only collide with this version's
    # own slot here; other versions are left alone.
    from .envs import cache_dir as _envs_cache_dir
    toolkit_dir = _envs_cache_dir(name, version)

    if toolkit_dir.exists():
        # Reinstall of an existing slot is benign — same version, re-fetched.
        console.print(f"[yellow]{name} v{version} is already installed.[/yellow]")
        if not _confirm("Reinstall?", default=True, mode=mode):
            sys.exit(0)
        # Remove the existing slot so the extract path is clean.
        shutil.rmtree(toolkit_dir)

    # Step 3: Download tarball. The progress bar self-announces ("Downloading
    # <name>...") and is transient — once it completes, the "✓ Downloaded"
    # line below takes its place. So no separate "Downloading toolkit..."
    # heading is needed.

    # Get tarball URL from version info
    tarball_url = None
    for v in toolkit_meta.get('versions', []):
        if isinstance(v, dict) and v.get('version') == version:
            tarball_url = v.get('tarball_url')
            break

    if not tarball_url:
        # Fallback: construct URL
        tarball_url = f"{api_url}/api/toolkits/{name}/download/{version}"

    try:
        import tempfile
        from rich.progress import Progress, DownloadColumn, BarColumn, TransferSpeedColumn, TextColumn

        tarball_response = requests.get(tarball_url, stream=True, timeout=60)

        if tarball_response.status_code != 200:
            console.print(f"[red]✗ Download failed (status {tarball_response.status_code})[/red]")
            sys.exit(1)

        # Get file size
        total_size = int(tarball_response.headers.get('content-length', 0))

        # Download with progress bar
        tarball_path = Path(tempfile.gettempdir()) / f"{name}-{version}.tar.gz"

        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=console,
            # Erase the progress bar on completion so the persistent
            # "✓ Downloaded ..." line just below it isn't redundant with
            # a stale bar in the scrollback.
            transient=True,
        ) as progress:
            task = progress.add_task(f"Downloading {name}-{version}.tar.gz", total=total_size)

            with open(tarball_path, 'wb') as f:
                for chunk in tarball_response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

        console.print(
            f"[green]✓ Downloaded {name}-{version}.tar.gz "
            f"({_format_bytes(tarball_path.stat().st_size)})[/green]"
        )

    except requests.exceptions.RequestException as e:
        console.print(f"[red]✗ Download error: {e}[/red]")
        sys.exit(1)

    # Step 4: Extract tarball
    console.print(f"Extracting to {toolkit_dir}...")

    toolkit_dir.mkdir(parents=True, exist_ok=True)

    try:
        import tarfile
        with tarfile.open(tarball_path, 'r:gz') as tar:
            tar.extractall(path=toolkit_dir)

        file_count = sum(1 for _ in toolkit_dir.rglob('*'))
        console.print(f"[green]✓ Extracted {file_count} files[/green]\n")

        # Clean up tarball
        tarball_path.unlink()

    except Exception as e:
        console.print(f"[red]✗ Extraction error: {e}[/red]")
        sys.exit(1)

    # Step 5: Detect environment type
    console.print("Detecting environment requirements...")

    try:
        toolkit_config = load_toolkit_yaml(toolkit_dir)
        env_type, python_version = detect_environment_type(toolkit_dir, toolkit_config)

        console.print(f"[green]✓ Environment: {env_type} (Python {python_version})[/green]")

        # Special messages for conda/docker
        if env_type == 'conda' and not has_conda():
            console.print("[yellow]Warning: conda not detected. Install conda/mamba or use Docker mode.[/yellow]\n")
        elif env_type == 'docker':
            if (toolkit_dir / 'Dockerfile').exists():
                console.print("[blue]Docker mode: toolkit has custom Dockerfile[/blue]")
            else:
                current_py = get_current_python()
                if python_version != current_py:
                    console.print(f"[blue]Docker mode: requires Python {python_version} (current: {current_py})[/blue]")
            console.print("[yellow]Docker mode will be available in Phase 3B[/yellow]\n")

    except FileNotFoundError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        sys.exit(1)
    except yaml.YAMLError as e:
        console.print(f"[red]✗ Invalid toolkit.yaml: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ Environment detection error: {e}[/red]")
        sys.exit(1)

    # Step 6: Refuse Docker mode (Phase 3B) before doing any work
    if env_type == 'docker':
        console.print(
            "[red]✗ This toolkit requires Docker mode, which is not yet supported.[/red]\n"
            "[yellow]  Docker mode is planned for Phase 3B.[/yellow]"
        )
        # Roll back the extracted toolkit so we don't leave a broken install behind
        shutil.rmtree(toolkit_dir, ignore_errors=True)
        sys.exit(1)

    # Tier-2 toolkit detection. setup.py at root + setup_script: true
    # in toolkit.yaml means the Tier-2 setup runner will be invoked
    # after env setup. Toolkits with only one of the two are surfaced
    # at ``toolbase validate`` time but installed cleanly.
    has_setup_script = (toolkit_dir / 'setup.py').exists()

    # Step 7: Setup environment
    console.print()
    python_path = None
    env_name = None

    try:
        if env_type == 'venv':
            console.print("[bold blue]Setting up environment...[/bold blue]\n")
            python_path = setup_venv_environment(toolkit_dir, console)

        elif env_type == 'conda':
            verify_conda_available()
            console.print("[bold blue]Setting up environment...[/bold blue]\n")
            env_name = setup_conda_environment(toolkit_dir, name, python_version, console)

    except Exception as e:
        console.print(f"\n[red]✗ Environment setup failed: {e}[/red]")

        # Show error details if it's a subprocess error
        if isinstance(e, subprocess.CalledProcessError):
            if e.stderr:
                error_output = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)
                console.print(f"[red]Error output: {error_output[:500]}[/red]")  # Limit to 500 chars

        # Clean up partial installation
        console.print(f"[yellow]Cleaning up {toolkit_dir}...[/yellow]")
        if env_type == 'conda' and env_name:
            cleanup_conda_environment(env_name)
        shutil.rmtree(toolkit_dir)
        raise click.ClickException("Installation failed")

    # Step 8: Save metadata with everything serve will need
    skills_dir = toolkit_dir / 'skills'
    if skills_dir.exists():
        # Filter out macOS AppleDouble metadata files ("._foo.md")
        skill_files = sorted(
            p for p in skills_dir.glob('*.md') if not p.name.startswith('._')
        )
    else:
        skill_files = []
    tools_count = len(toolkit_config.get('tools', []) or [])

    meta = {
        'name': name,
        'version': version,
        'environment': env_type,
        'python_version': python_version,
        'tools_count': tools_count,
        'has_skills': len(skill_files) > 0,
        'skills_count': len(skill_files),
        'has_setup_script': has_setup_script,
        # ``needs_setup`` was the 3C-1 placeholder used to skip Tier-2
        # toolkits at serve startup; 3C-2 lifts that gate by running
        # ``validate(ctx)`` instead. Keep the field on disk for backward
        # compat with anything that might inspect old metadata, but
        # serve no longer consults it.
        'needs_setup': has_setup_script,
        'installed_at': datetime.now().isoformat(),
    }

    # Environment-specific fields (consumed by serve to launch the per-toolkit subprocess)
    if env_type == 'venv':
        meta['python_path'] = str(python_path)
    elif env_type == 'conda':
        meta['env_name'] = env_name

    # Write the legacy ``.tb_meta.json`` (consumed by serve / setup runner)
    # AND the new ``.install_meta.yaml`` (canonical, schema-versioned).
    # The legacy file stays until later phases migrate serve / runner.
    from .envs import (
        write_legacy_meta as _write_legacy_meta,
        write_install_meta as _write_install_meta,
        compute_and_write_disk_size as _compute_and_write_disk_size,
    )
    _write_legacy_meta(toolkit_dir, meta)

    install_extras: dict = {}
    if env_type == 'venv':
        install_extras["python_path"] = str(python_path)
    elif env_type == 'conda':
        install_extras["env_name"] = env_name
    install_extras["tools_count"] = tools_count
    install_extras["has_skills"] = len(skill_files) > 0
    install_extras["skills_count"] = len(skill_files)
    install_extras["has_setup_script"] = has_setup_script
    _write_install_meta(
        toolkit_dir,
        name=name,
        version=version,
        install_method=env_type or "venv",
        python_version=python_version or "?",
        extras=install_extras,
    )

    # Best-effort: walk the slot, write ``.disk_size`` for fast ``tb list``.
    # If the walk blows the budget, ``compute_and_write_disk_size`` returns
    # None and the file is skipped — ``tb list`` shows "—" instead of
    # slowing down on demand.
    try:
        _compute_and_write_disk_size(toolkit_dir)
    except Exception:
        pass

    # Pin into the appropriate manifest. -g (default) pins into the
    # global default-project; -l pins into THIS project's manifest
    # (creating it if needed). The cache slot itself is always in the
    # global cache and project-agnostic — only the pin is scoped. There
    # is deliberately no "where do you want this?" prompt: the flag (or
    # its -g default) carries that intent now.
    _pin_after_install(name, version, local_scope=local_scope)

    # Step 9: Success message
    console.print(f"\n[bold green]✓ Successfully installed {name} v{version}[/bold green]\n")

    if env_type == 'venv':
        console.print(f"Environment: venv (Python {python_version})")
    elif env_type == 'conda':
        console.print(f"Environment: conda env '{env_name}' (Python {python_version})")

    if tools_count > 0:
        console.print(f"Tools: {tools_count} available")

    if skill_files:
        console.print(f"Skills: {len(skill_files)} guide{'s' if len(skill_files) != 1 else ''} available")
        # List each skill (strip .md extension for readability)
        for skill_file in skill_files:
            console.print(f"  [dim]•[/dim] {skill_file.stem}")

        # Surface skills into ~/.claude/skills/ so Claude Code picks them
        # up automatically. Best-effort — failures here don't fail install.
        if not no_skills:
            try:
                from .skills import install_skills_for_toolkit, CLAUDE_SKILLS_DIR
                surfaced = install_skills_for_toolkit(name, toolkit_dir)
                if surfaced:
                    console.print(
                        f"[dim]Surfaced to {CLAUDE_SKILLS_DIR}/ "
                        f"({len(surfaced)} entr{'ies' if len(surfaced) != 1 else 'y'})[/dim]"
                    )
            except Exception as e:
                console.print(
                    f"[yellow]Could not surface skills to ~/.claude/skills: {e}[/yellow]"
                )

    # Phase 3C-1: Tier-1 declarative setup. If toolkit.yaml has a
    # ``config:`` block, walk it and prompt the user (TTY) or fill
    # defaults (--no-input). Always succeeds: required fields the user
    # can't supply land as ``<NEEDS VALUE>`` and ``serve`` will refuse
    # the toolkit until they're filled.
    config_block = toolkit_config.get('config') or []
    if config_block:
        try:
            from .setup import parse_config_block, run_install_setup
            config_schema = parse_config_block(config_block)
            run_install_setup(name, config_schema, mode=mode)
        except Exception as e:
            # The block was already validated by `validate_toolkit`
            # before download (we wouldn't have reached this point if
            # it were malformed), so a failure here is unusual. Don't
            # fail the install — config can be filled in later.
            console.print(
                f"[yellow]Warning: configuration setup hit an error: {e}[/yellow]"
            )
            console.print(
                "[yellow]The toolkit is installed, but you'll need to "
                "fill in its configuration manually before running "
                "`toolbase serve`.[/yellow]"
            )

    # Phase 3C-2: Tier-2 setup.py. If the toolkit ships a setup.py at
    # root AND declares setup_script: true, invoke its setup(ctx) now.
    # The runner spawns the toolkit's venv-Python and routes ctx.* RPCs
    # back to this process. Like Tier-1, install never fails because of
    # a setup.py error — the user can re-run via ``toolbase setup``.
    declares_setup_script = bool(toolkit_config.get('setup_script'))
    if has_setup_script and declares_setup_script:
        try:
            from .setup import run_setup_script as _run_setup
            console.print(
                f"\n[bold blue]Running {name} setup script...[/bold blue]"
            )
            sresult = _run_setup(name, prompt_mode=mode)
            if sresult.ok:
                console.print(
                    f"[green]✓[/green] {name} setup script complete."
                )
            else:
                # Render a one-line summary; full traceback in log file.
                console.print(
                    f"[yellow]Setup script reported failure.[/yellow]"
                )
                if sresult.message:
                    console.print(f"[yellow]  {sresult.message}[/yellow]")
                if sresult.log_path:
                    console.print(
                        f"[yellow]  Full log: {sresult.log_path}[/yellow]"
                    )
                console.print(
                    "[yellow]The toolkit is installed but `serve` will "
                    "skip it until validate(ctx) passes. Fix the issue "
                    f"and run [cyan]toolbase setup {name}[/cyan].[/yellow]"
                )
        except Exception as e:
            console.print(
                f"[yellow]Warning: setup script invocation failed: "
                f"{e}[/yellow]"
            )
            console.print(
                f"[yellow]Run [cyan]toolbase setup {name}[/cyan] to "
                "retry.[/yellow]"
            )
    elif has_setup_script and not declares_setup_script:
        # The toolkit ships setup.py but didn't opt in via setup_script:
        # true. Could be intentional (author hasn't migrated) or an
        # oversight. Surface as a hint, don't run.
        console.print(
            f"[dim]Note: {name} ships a setup.py but doesn't declare "
            "setup_script: true in toolkit.yaml. Skipping setup. If you "
            "want to run it, ask the author to enable setup_script.[/dim]"
        )

    # Expected toolkits — companion installs the author flagged. No runtime
    # coupling; we just prompt (TTY) or message (skip mode) and install
    # accepted ones recursively. Already-installed ones are silently skipped.
    expected = toolkit_config.get('expected_toolkits') or []
    expected = [e for e in expected if isinstance(e, str)]
    if expected:
        # Companion installs: "installed" now means "has any version in
        # the cache" — the multi-version model means a companion at any
        # pin still counts.
        from .envs import list_versions as _list_versions
        not_installed = [
            e for e in expected if not _list_versions(e)
        ]
        if not_installed:
            console.print(
                f"\n[bold]{name} is designed to work with:[/bold] "
                f"{', '.join(expected)}"
            )
            if mode == "skip":
                console.print(
                    f"[dim]Not installed: {', '.join(not_installed)}.[/dim]"
                )
                console.print(
                    f"[dim]Install with: [cyan]toolbase install "
                    f"{' '.join(not_installed)}[/cyan][/dim]"
                )
            else:
                console.print(
                    f"[dim]The following are not yet installed: "
                    f"{', '.join(not_installed)}[/dim]"
                )
                if _confirm(
                    "Install them now?", default=True, mode=mode,
                ):
                    runner_ctx = click.get_current_context()
                    for companion in not_installed:
                        console.print(
                            f"\n[dim]── Installing companion toolkit: "
                            f"{companion} ──[/dim]"
                        )
                        # Re-invoke install with the same skip/yes mode.
                        # We forward --no-input rather than --yes so the
                        # companion's own consequential prompts (replace,
                        # etc.) still abort safely.
                        try:
                            runner_ctx.invoke(
                                install,
                                name=companion,
                                version=None,
                                no_skills=no_skills,
                                yes=False,
                                no_=False,
                                no_input=True,
                            )
                        except SystemExit as e:
                            if e.code not in (0, None):
                                console.print(
                                    f"[yellow]Companion toolkit "
                                    f"'{companion}' did not install "
                                    f"cleanly (exit {e.code}); skipping.[/yellow]"
                                )

    console.print(f"\n[bold]Ready to use! Try:[/bold]")
    console.print(f"  [cyan]tb list[/cyan]")
    console.print(f"  [cyan]tb serve {name}[/cyan]")
    console.print()


@main.command(name='list')
@click.option(
    "--json", "as_json", is_flag=True, default=False,
    help=(
        "Emit a flat JSON array of {name, version, last_used_iso, "
        "size_bytes, pinned_in_project} for agent / scripting consumption. "
        "Suppresses the tree output and the legacy-layout heads-up."
    ),
)
def list_cmd(as_json):
    """
    List all installed toolkits.

    Walks ``~/.toolbase/cache/<name>/<version>/`` and renders one
    entry per (name, version) slot, grouped by name:

    \b
        $ tb list
        heptapod
          - 0.1     (used 3 days ago, 8.2 GB)
          - 0.3 *   (used yesterday, 8.4 GB)
        arxiv-search
          - 0.2 *   (used 2 hours ago, 180 MB)

        * = pinned in this project (./.toolbase/manifest.yaml)

    \b
    Per-entry fields:
      - ``last_used``: human-friendly delta from the per-slot
        ``.last_used`` file (touched on every ``tb serve`` spawn).
        ``"never"`` if missing.
      - ``disk_size``: bytes from the per-slot ``.disk_size`` file
        (computed once at install time). ``"—"`` if missing.
      - ``*``: marks the version pinned in the active project's
        manifest (whichever ``.toolbase/manifest.yaml`` discovery
        resolves to). Legend printed only when at least one pin
        applies. Default-project pins are flagged the same way; the
        legend points at the resolved manifest path.

    With ``--json``, output is a flat array of objects:

    \b
        [
          {"name": "heptapod", "version": "0.1.0",
           "last_used_iso": "2026-05-09T14:23:00", "size_bytes": 8200000000,
           "pinned_in_project": false},
          ...
        ]

    Examples:
        toolbase list
        tb list --json
    """
    from .envs import walk_cache

    entries = walk_cache()

    # Resolve the active project so we can mark pinned versions. Reads
    # never fall back to interactive prompts; the helper silently uses
    # default-project when no .toolbase/ is found.
    pin_map, manifest_path = _list_resolve_pin_map(entries)

    if as_json:
        payload = [
            {
                "name": e.name,
                "version": e.version,
                "last_used_iso": e.last_used_iso,
                "size_bytes": e.disk_size_bytes,
                "pinned_in_project": pin_map.get(e.name) == e.version,
            }
            for e in _list_sorted_entries(entries)
        ]
        click.echo(json.dumps(payload, indent=2))
        return

    if not entries:
        console.print("[dim]No toolkits installed.[/dim]")
        console.print(
            "\nTry: [cyan]tb install arxiv-search[/cyan]"
        )
        return

    # Group entries by toolkit name; within a name, sort by version desc.
    from .versioning import parse_version
    grouped: dict[str, list] = {}
    for e in entries:
        grouped.setdefault(e.name, []).append(e)
    for k in grouped:
        grouped[k].sort(
            key=lambda e: parse_version(e.version) or (0, 0, 0),
            reverse=True,
        )

    any_pin_applied = False
    for name in sorted(grouped):
        console.print(f"[cyan]{name}[/cyan]")
        for entry in grouped[name]:
            pinned = pin_map.get(name) == entry.version
            if pinned:
                any_pin_applied = True
            marker = " [yellow]*[/yellow]" if pinned else ""
            last_used = _format_last_used(entry.last_used_iso)
            size = _format_disk_size(entry.disk_size_bytes)
            # Editable slots show a "-> <source>" indicator so it's
            # obvious the slot is a live link, not a frozen install.
            meta = entry.install_meta or {}
            editable_src = meta.get("source_path") if meta.get("editable") else None
            # Version column padded a little so the parenthetical
            # aligns across rows that have / don't have the pin marker.
            ver_cell = f"{entry.version}{marker}"
            if editable_src:
                console.print(
                    f"  - {ver_cell}   "
                    f"[dim](-> {editable_src}, used {last_used}, {size})[/dim]"
                )
            else:
                console.print(
                    f"  - {ver_cell}   "
                    f"[dim](used {last_used}, {size})[/dim]"
                )

    if any_pin_applied and manifest_path is not None:
        # Render the manifest path relative to cwd when possible — keeps
        # the legend readable in real-project usage. Falls back to the
        # absolute path for default-project or when relative-resolution
        # fails (e.g. across drive letters on Windows).
        try:
            rel = manifest_path.relative_to(Path.cwd())
            display = f"./{rel}"
        except ValueError:
            display = str(manifest_path)
        console.print()
        console.print(
            f"[dim]* = pinned in this project ({display})[/dim]"
        )


def _list_sorted_entries(entries):
    """Return entries deterministically sorted by (name asc, version desc)."""
    from .versioning import parse_version
    return sorted(
        entries,
        key=lambda e: (
            e.name,
            tuple(-x for x in (parse_version(e.version) or (0, 0, 0))),
        ),
    )


def _list_resolve_pin_map(entries):
    """Return ``(pin_map, manifest_path)`` for the active project.

    ``pin_map`` is ``{toolkit_name: pinned_version}`` for every entry
    pinned in the active project's manifest. Returns an empty dict
    (and ``None`` manifest path) when no entries are pinned or the
    manifest is unreadable. Read-only; never creates a project dir.
    """
    if not entries:
        return {}, None
    try:
        from .envs import (
            project_manifest_path as _project_manifest_path,
            load_manifest as _load_manifest,
        )
        project_root, _source = _resolve_active_project_root()
        manifest_path = _project_manifest_path(project_root)
        if not manifest_path.exists():
            return {}, None
        manifest = _load_manifest(manifest_path)
        return (
            {e.name: e.version for e in manifest.toolkits},
            manifest_path,
        )
    except Exception:
        # Manifest read errors (schema-too-new, malformed) shouldn't
        # break list. Skip the pin indicator and proceed.
        return {}, None


def _format_last_used(
    iso_stamp: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> str:
    """Render an ISO-8601 timestamp as a human-friendly 'X ago'.

    Phase-5 forms (spec'd in the Environments brief):
      - missing stamp → ``"never"``
      - <5s →            ``"just now"``
      - <60s →           ``"N seconds ago"`` / ``"1 second ago"``
      - <60m →           ``"N minutes ago"`` / ``"1 minute ago"``
      - <24h →           ``"N hours ago"`` / ``"1 hour ago"``
      - <2d →            ``"yesterday"``
      - <14d →           ``"N days ago"``
      - <8w →            ``"N weeks ago"`` / ``"1 week ago"``
      - >=8w →           ``"N months ago"`` (rough; 30-day months)

    ``now`` is injectable for deterministic tests. Never raises —
    formatting issues fall back to the raw stamp.
    """
    if not iso_stamp:
        return "never"
    try:
        ts = datetime.fromisoformat(iso_stamp)
    except (TypeError, ValueError):
        return iso_stamp
    reference = now if now is not None else datetime.now()
    delta = reference - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"  # clock skew
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs} seconds ago" if secs != 1 else "1 second ago"
    if secs < 3600:
        m = secs // 60
        return f"{m} minutes ago" if m != 1 else "1 minute ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hours ago" if h != 1 else "1 hour ago"
    days = secs // 86400
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    weeks = days // 7
    if weeks < 8:
        return f"{weeks} weeks ago" if weeks != 1 else "1 week ago"
    months = days // 30
    return f"{months} months ago" if months != 1 else "1 month ago"


def _format_disk_size(size_bytes: Optional[int]) -> str:
    """Render a byte count as a human-friendly string. ``None`` → '—'."""
    if size_bytes is None:
        return "—"
    n = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@main.command()
@click.argument('name')
@_interactive_options
def uninstall(name, yes, no_, no_input):
    """
    Uninstall a toolkit.

    \b
    Two forms:
      tb uninstall aster              — removes ALL installed versions
      tb uninstall aster@1.2.0        — removes one version slot only

    Also removes the corresponding pin from the active project's
    manifest (Phase 2: default-project; Phase 3 wires real per-project).

    Conda environments are torn down before the cache slot is removed
    (so a failure here doesn't leave orphan conda envs behind).

    \b
    Examples:
        toolbase uninstall aster
        toolbase uninstall aster@1.2.0
        toolbase uninstall aster --yes
    """
    from .envs import (
        walk_cache as _walk_cache,
        list_versions as _list_versions,
        find_slot as _find_slot,
        project_manifest_path as _project_manifest_path,
        remove_pin as _remove_pin,
    )

    mode = _resolve_prompt_mode(yes, no_, no_input)

    # Parse name@version: if @ is present, target one slot; otherwise all versions.
    target_version: Optional[str] = None
    if "@" in name:
        bare, _, ver = name.partition("@")
        if not bare or not ver:
            raise click.UsageError(
                f"Bad name@version syntax: {name!r} (need both sides of @)."
            )
        name = bare
        target_version = ver

    versions = _list_versions(name)
    if not versions:
        console.print(f"[red]✗ Toolkit '{name}' is not installed.[/red]")
        console.print("\nList installed toolkits: [cyan]toolbase list[/cyan]")
        sys.exit(1)

    if target_version is not None:
        slot = _find_slot(name, target_version)
        if slot is None:
            console.print(
                f"[red]✗ {name} v{target_version} is not installed.[/red]"
            )
            console.print(
                f"Installed versions of {name}: {', '.join(sorted(versions))}"
            )
            sys.exit(1)
        targets = [slot]
        plural = f"{name} v{target_version}"
    else:
        targets = [_find_slot(name, v) for v in versions]
        targets = [t for t in targets if t is not None]
        if len(targets) == 1:
            plural = f"{name} v{targets[0].version}"
        else:
            plural = f"{name} ({len(targets)} versions: {', '.join(t.version for t in targets)})"

    console.print(f"\n[bold]Uninstalling {plural}[/bold]")
    for slot in targets:
        console.print(f"  Directory: [dim]{slot.path}[/dim]")
        meta = slot.install_meta or slot.legacy_meta
        env_type = meta.get('install_method') or meta.get('environment')
        env_name = meta.get('env_name')
        if env_type == 'conda' and env_name:
            console.print(f"  Conda env: [dim]{env_name}[/dim]")

    # Uninstall is consequential.
    if not _confirm(
        "\nProceed?", default=False, mode=mode, consequential=True,
    ):
        console.print("[dim]Cancelled.[/dim]")
        sys.exit(0)

    for slot in targets:
        meta = slot.install_meta or slot.legacy_meta
        env_type = meta.get('install_method') or meta.get('environment')
        env_name = meta.get('env_name')
        # Remove conda env first.
        if env_type == 'conda' and env_name:
            with console.status(f"[bold blue]Removing conda environment '{env_name}'..."):
                cleanup_conda_environment(env_name)
            console.print(f"[green]✓ Removed conda environment '{env_name}'[/green]")
        try:
            shutil.rmtree(slot.path)
            console.print(f"[green]✓ Removed {slot.path}[/green]")
        except OSError as e:
            console.print(f"[red]✗ Could not remove {slot.path}: {e}[/red]")
            sys.exit(1)

    # If we removed all versions, prune the empty parent dir too.
    from .envs import cache_root as _cache_root
    name_dir = _cache_root() / name
    if name_dir.exists() and not any(name_dir.iterdir()):
        try:
            name_dir.rmdir()
        except OSError:
            pass

    # Update the active project's manifest.
    #
    # - ``uninstall <name>``: remove the pin entirely.
    # - ``uninstall <name>@<ver>``: if any other version remains, leave
    #   the pin alone (still valid, even if we just unpinned one slot).
    #   If no versions remain, remove the pin.
    #
    # Uninstall never implicitly creates a project: if cwd isn't in one,
    # we silently fall back to default-project (which is where ``install``
    # would have pinned in the no-project case anyway).
    try:
        project_root, _source = _resolve_active_project_root()
        manifest_path = _project_manifest_path(project_root)
        remaining = _list_versions(name)
        if not remaining:
            _remove_pin(manifest_path, name)
    except Exception as e:
        console.print(
            f"[dim]Note: could not update project manifest: {e}[/dim]"
        )

    # Skills cleanup — only when ALL versions are gone.
    if not _list_versions(name):
        try:
            from .skills import uninstall_skills_for_toolkit
            removed_skills = uninstall_skills_for_toolkit(name)
            if removed_skills:
                console.print(
                    f"[green]✓[/green] Removed {len(removed_skills)} skill"
                    f"{'s' if len(removed_skills) != 1 else ''} from ~/.claude/skills/"
                )
        except Exception as e:
            console.print(
                f"[yellow]Could not clean up ~/.claude/skills entries: {e}[/yellow]"
            )

    console.print(f"\n[bold green]✓ Uninstalled {plural}[/bold green]")


# ── tb reset ───────────────────────────────────────────────────────


@main.command(name="reset")
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Show what would be deleted, then exit. Removes nothing.",
)
@click.option(
    "--all", "all_mode", is_flag=True, default=False,
    help=(
        "Scorched-earth: remove cache/, toolkits/, downloads/, and "
        "default-project/. Preserves config.json (login state) and logs/. "
        "Preserves config/ unless --include-config is also passed."
    ),
)
@click.option(
    "--include-config", is_flag=True, default=False,
    help=(
        "Only with --all: also remove config/ (user-level toolkit "
        "config). Use when starting completely fresh."
    ),
)
@_interactive_options
def reset(dry_run, all_mode, include_config, yes, no_, no_input):
    """
    Clean up Toolbase state under ``~/.toolbase/``.

    \b
    Modes:
      tb reset                        Cutover mode. Removes the legacy
                                       0.4.x ``~/.toolbase/toolkits/``
                                       dir only. Preserves cache/,
                                       config/, default-project/,
                                       serve.yaml, logs/, config.json.

    \b
      tb reset --all                  Scorched-earth. Removes cache/,
                                       toolkits/, downloads/, and
                                       default-project/. Preserves
                                       config.json (login state) and
                                       logs/. Preserves config/ unless
                                       --include-config is also passed.
                                       Requires extra confirmation.

    \b
      tb reset --all --include-config Full fresh-start. As --all, plus
                                       removes config/ (user-level
                                       toolkit config / secrets).

    \b
    Common flags:
      --dry-run    List paths that would be deleted; remove nothing.
      --yes / -y   Skip confirmation prompts. Required for CI.
      --no         Refuse any confirmation (effectively a no-op).
      --no-input   Use defaults for prompts (default-N for reset
                   confirms, so reset becomes a no-op without --yes).

    Use the existing ``tb uninstall <name>`` for per-toolkit removal.
    ``tb reset`` is the bulk-clean / start-fresh hammer.
    """
    from .envs import legacy_toolkits_dir

    if include_config and not all_mode:
        raise click.UsageError(
            "--include-config requires --all. Use `tb reset --all "
            "--include-config` for a total fresh start."
        )

    mode = _resolve_prompt_mode(yes, no_, no_input)
    user_root = toolbase_config_dir()
    legacy_dir = legacy_toolkits_dir()

    # Build the deletion list for the current mode. Order matters
    # only for display; the actual rmtree is independent per entry.
    targets: List[Tuple[str, Path]] = []

    if all_mode:
        # Scorched earth. Always preserves config.json and logs/.
        targets.append(("cache/ (installed toolkit binaries)", user_root / "cache"))
        targets.append(("toolkits/ (legacy 0.4.x layout)", legacy_dir))
        targets.append(("downloads/ (cached downloads)", user_root / "downloads"))
        targets.append(("default-project/ (implicit project)", user_root / "default-project"))
        if include_config:
            targets.append(("config/ (user-level toolkit config + secrets)", user_root / "config"))
    else:
        # Cutover mode — only the 0.4.x layout.
        targets.append(("toolkits/ (legacy 0.4.x layout)", legacy_dir))

    # Filter to paths that actually exist on disk.
    existing = [(label, p) for label, p in targets if p.exists()]

    if not existing:
        if all_mode:
            console.print("[dim]Nothing to reset — none of the targeted directories exist.[/dim]")
        else:
            console.print(
                "[dim]Nothing to reset — no legacy 0.4.x install layout found.[/dim]"
            )
            console.print(
                "Try [cyan]tb reset --all[/cyan] for a full fresh-start, "
                "or [cyan]tb uninstall <name>[/cyan] for a single toolkit."
            )
        return

    # Always list paths before asking — no hidden deletions, per the brief.
    if dry_run:
        console.print(f"[bold]Dry-run: the following would be removed[/bold]")
    elif all_mode:
        console.print(f"[bold red]This will remove the following:[/bold red]")
    else:
        console.print(f"[bold]This will remove the legacy 0.4.x layout:[/bold]")

    for label, path in existing:
        console.print(f"  [yellow]{label}[/yellow]")
        console.print(f"    [dim]{path}[/dim]")

    # Always-preserved paths (helpful reassurance).
    if all_mode:
        preserved = ["config.json (login state)", "logs/"]
        if not include_config:
            preserved.append("config/ (user-level toolkit config)")
        console.print(f"\n[dim]Preserved: {', '.join(preserved)}[/dim]")

    if dry_run:
        console.print(f"\n[dim]Dry-run: nothing was deleted.[/dim]")
        return

    # Cutover-mode confirmation: default-N, consequential.
    if not all_mode:
        if not _confirm(
            "\nProceed with cutover cleanup?",
            default=False,
            mode=mode,
            consequential=True,
        ):
            console.print("[dim]Cancelled.[/dim]")
            return
    else:
        # Scorched-earth requires extra confirmation on top of the
        # standard one. Two prompts; both default-N; both consequential.
        if not _confirm(
            "\nProceed with full reset?",
            default=False,
            mode=mode,
            consequential=True,
        ):
            console.print("[dim]Cancelled.[/dim]")
            return
        if not _confirm(
            "Are you sure? This cannot be undone.",
            default=False,
            mode=mode,
            consequential=True,
        ):
            console.print("[dim]Cancelled.[/dim]")
            return

    # Suppress the heads-up message on subsequent tb invocations
    # within this Python process — purely cosmetic for tests that
    # invoke reset followed by another command.
    os.environ["TOOLBASE_SUPPRESS_LEGACY_WARNING"] = "1"

    errors = 0
    for label, path in existing:
        try:
            shutil.rmtree(path)
            console.print(f"[green]✓[/green] Removed {path}")
        except OSError as e:
            errors += 1
            console.print(f"[red]✗[/red] Could not remove {path}: {e}")

    if errors:
        console.print(
            f"\n[red]Done with {errors} error(s).[/red] Some paths could not be removed."
        )
        sys.exit(1)
    if all_mode:
        console.print(f"\n[bold green]✓ Reset complete.[/bold green]")
        console.print(
            "Reinstall toolkits with [cyan]tb install <name>[/cyan]."
        )
    else:
        console.print(f"\n[bold green]✓ Legacy layout removed.[/bold green]")
        console.print(
            "Reinstall toolkits with [cyan]tb install <name>[/cyan] "
            "to populate the new cache layout."
        )


def toolbase_config_dir() -> Path:
    """Return the active ``~/.toolbase/`` root.

    Reads ``toolbase.config.CONFIG_DIR`` at call time so test
    monkeypatching is respected (HANDOFF.md gotcha #12).
    """
    from . import config as _config_mod
    return _config_mod.CONFIG_DIR


class _ServeGroup(click.Group):
    """``serve``-specific group that lets toolkit names appear as positional
    args without colliding with subcommand dispatch.

    Click's normal behavior is to interpret the first positional after the
    group name as a subcommand. Without this override, ``tb serve
    arxiv-search`` would error with "No such command 'arxiv-search'."
    Forcing users to write ``-t arxiv-search`` is mechanically correct but
    unfriendly.

    Strategy: rewrite ``args`` early in ``parse_args``. Bare positional
    names that aren't reserved subcommand names get rewritten as
    ``-t NAME`` pairs. Subcommand calls (``serve enable foo``,
    ``serve config --show``) take the unmodified path.

    The reserved subcommand names are listed in ``RESERVED`` below; any
    toolkit colliding with one of those would still need ``-t``. The
    validator's name shape allows ``config`` etc. as toolkit names so
    this is technically possible, but rare.
    """

    RESERVED = {"enable", "disable", "enable-tool", "disable-tool", "config"}

    def parse_args(self, ctx, args):
        # Walk leading non-flag tokens. If none of them match a reserved
        # subcommand, treat them all as ``-t`` values. If any of them
        # *does* match a reserved name, we leave args untouched and let
        # Click's normal subcommand dispatch handle it.
        leading: list[str] = []
        rest_idx = 0
        while rest_idx < len(args) and not args[rest_idx].startswith("-"):
            leading.append(args[rest_idx])
            rest_idx += 1
        if leading and not any(t in self.RESERVED for t in leading):
            rewritten: list[str] = []
            for name in leading:
                rewritten.extend(["-t", name])
            rewritten.extend(args[rest_idx:])
            args = rewritten
        return super().parse_args(ctx, args)


@main.group(cls=_ServeGroup, invoke_without_command=True)
@click.option(
    '--toolkit', '-t', 'toolkits_flag', multiple=True, metavar='NAME',
    help=(
        'Serve only this toolkit (repeatable). Replaces the default set for '
        'this invocation. Bare positional names also work: '
        'tb serve aster heptapod is equivalent to -t aster -t heptapod.'
    ),
)
@click.option(
    '--group', 'group_name', default=None,
    help='Serve a named tool group from ~/.toolbase/serve.yaml (one-shot).',
)
@click.option(
    '--enable-tool', 'enable_tool', multiple=True, metavar='TOOLKIT__TOOL',
    help=(
        'Enable a single tool, switching to allowlist mode for its toolkit '
        '(only listed tools serve). One-shot, does not persist. Repeatable.'
    ),
)
@click.option(
    '--disable-tool', 'disable_tool', multiple=True, metavar='TOOLKIT__TOOL',
    help=(
        'Disable a single tool from this serve session (one-shot, does not '
        'persist). Wins over --enable-tool for the same tool. Repeatable.'
    ),
)
@click.option(
    '--enable-group', 'enable_group', multiple=True, metavar='TOOLKIT__GROUP',
    help=(
        'Serve only tools belonging to the named tool group within that '
        'toolkit (one-shot). Requires the toolkit to declare a '
        'tool_groups: block in its toolkit.yaml. If the group is currently '
        'unavailable (missing required config keys) or undeclared, a clear '
        'reason is surfaced at startup without crashing. Repeatable.'
    ),
)
@click.option(
    '--dry-run', '-d', 'dry_run', is_flag=True, default=False,
    help='Print the resolved serve set and exit without starting the server.',
)
@click.option(
    '--call-timeout', 'call_timeout', type=float, default=None,
    metavar='SECONDS',
    help=(
        'Per-tool-call timeout in seconds. Defaults to 60. Bump this for '
        'long-running scientific workflows; the orchestrator will fail a '
        'call rather than block the agent forever if a tool wedges.'
    ),
)
@click.option(
    '--no-tui', is_flag=True, default=True,
    help='Run without TUI. Currently the only supported mode.',
)
@click.pass_context
def serve(ctx, toolkits_flag, group_name, enable_tool, disable_tool, enable_group, dry_run, call_timeout, no_tui):
    """
    Start the MCP server for installed toolkits.

    With no arguments, serves all installed toolkits (minus anything in
    ~/.toolbase/serve.yaml's default.toolkits.disabled). Pass toolkit
    names positionally, or use --toolkit / -t (repeatable), or --group to
    narrow the set for this invocation. Use the subcommands below to
    persistently edit defaults; see also "toolbase groups" for managing
    named subsets.

    \b
    Examples:
        tb serve                              # all installed
        tb serve aster                        # one toolkit (positional)
        tb serve aster arxiv-search           # several
        tb serve -t aster -t arxiv-search     # same, via flags
        tb serve --group exoplanet-pipeline   # named group
        tb serve aster --enable-tool aster__transit
        tb serve --disable-tool aster__heavy
        tb serve --dry-run                    # preview, then exit

    \b
    Persistent configuration lives in ~/.toolbase/serve.yaml. Edit
    directly, or use the subcommands below.

    Configure Claude Code with (use the canonical "toolbase" command in
    config files; "tb" works too but is documented as a convenience alias):

    \b
        {"mcpServers": {"toolbase": {"command": "toolbase",
                                       "args": ["serve"]}}}
    """
    toolkits = toolkits_flag
    # Subcommand path: don't run the server, defer to the subcommand.
    if ctx.invoked_subcommand is not None:
        return

    if call_timeout is not None and call_timeout <= 0:
        console.print(
            "[red]✗ --call-timeout must be a positive number of seconds.[/red]"
        )
        sys.exit(2)

    from .serve.config import (
        load_serve_config,
        resolve_serve_set,
        ServeConfigError,
        SERVE_CONFIG_PATH,
    )
    from .serve.orchestrator import discover_toolkits, serve as _serve_entry

    # Claim serve.log mirroring before anything else can take the
    # singleton. Project-discovery during ``discover_toolkits`` calls
    # ``get_logger()`` with no kwarg and would otherwise lock the
    # logger into ``serve_log=False`` for the rest of the process.
    # The orchestrator also calls ``get_logger(serve_log=True)`` later;
    # it's idempotent.
    from .logging.logger import get_logger as _get_logger
    _get_logger(serve_log=True)

    try:
        cfg = load_serve_config()
    except ServeConfigError as e:
        console.print(f"[red]Error in serve config:[/red] {e}")
        console.print(f"[dim]Edit {SERVE_CONFIG_PATH} or remove it to reset.[/dim]")
        sys.exit(1)

    discoveries = discover_toolkits()
    installed_names = [d.name for d in discoveries if d.skip_reason is None]

    try:
        resolved = resolve_serve_set(
            installed_toolkits=installed_names,
            config=cfg,
            positional_toolkits=list(toolkits),
            group_name=group_name,
            enable_tools=list(enable_tool),
            disable_tools=list(disable_tool),
            enable_groups=list(enable_group),
        )
    except ServeConfigError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)

    if dry_run:
        _print_resolution(resolved, discoveries)
        return

    # Bare serve flow with no narrowing flags should keep legacy behavior
    # (serve everything, no resolver). Only thread the resolved set when
    # the user actually narrowed something.
    narrowed = bool(toolkits or group_name or enable_tool or disable_tool
                    or enable_group
                    or cfg.default.disabled_toolkits or cfg.default.disabled_tools)

    # The MCP stdio protocol owns this process's stdin/stdout, so we do NOT
    # use the module-level `console` here (which writes to stdout). The
    # orchestrator builds its own stderr-bound Console.
    from .serve.orchestrator import DEFAULT_CALL_TIMEOUT_S
    timeout_s = call_timeout if call_timeout is not None else DEFAULT_CALL_TIMEOUT_S
    rc = _serve_entry(
        no_tui=True,
        resolved=resolved if narrowed else None,
        call_timeout_s=timeout_s,
    )
    sys.exit(rc)


def _print_resolution(resolved, discoveries) -> None:
    """Render --dry-run output: what would be served and how we got there.

    Two output modes, picked automatically:
      - Compact: when nothing is overriding the default (no positional,
        no group, no per-tool flag, no default disables). One-line
        resolution. Useful when the user is just confirming defaults.
      - Detailed: when overrides are layered. Per-toolkit tool counts and
        a step-by-step resolution path for debuggability.
    """
    installed_meta = {d.name: d for d in discoveries}

    # "No overrides" = exactly one entry in resolution_path AND it's the
    # untouched default. The resolver appends "default: all installed"
    # verbatim in that case.
    is_plain_default = (
        len(resolved.resolution_path) == 1
        and resolved.resolution_path[0] == "default: all installed"
        and not resolved.disable_qualified
    )

    console.print("\n[bold]Resolved serve set:[/bold]")
    if not resolved.toolkits:
        console.print("  [dim](nothing)[/dim]")
    for tk in resolved.toolkits:
        meta = installed_meta.get(tk)
        if meta is not None and meta.meta:
            total = meta.meta.get("tools_count", "?")
        else:
            total = "?"
        per_tool = resolved.tools.get(tk)
        if per_tool is None:
            disables_here = [
                q.split("__", 1)[1]
                for q in resolved.disable_qualified
                if q.startswith(f"{tk}__")
            ]
            if disables_here:
                # If we know the total, show "(N of M tools, except: X)".
                if isinstance(total, int):
                    served = total - len(disables_here)
                    console.print(
                        f"  [cyan]{tk}[/cyan] ({served} of {total} tools)"
                    )
                    console.print(f"    disabled: {', '.join(disables_here)}")
                else:
                    console.print(
                        f"  [cyan]{tk}[/cyan] (all tools except "
                        f"{', '.join(disables_here)})"
                    )
            else:
                console.print(f"  [cyan]{tk}[/cyan] ({total} of {total} tools)"
                              if isinstance(total, int)
                              else f"  [cyan]{tk}[/cyan] (all tools)")
        else:
            console.print(
                f"  [cyan]{tk}[/cyan] ({len(per_tool)} of {total} tools)"
            )
            console.print(f"    enabled: {', '.join(per_tool)}")

    if resolved.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for w in resolved.warnings:
            console.print(f"  [yellow]•[/yellow] {w}")

    if is_plain_default:
        from .serve.config import SERVE_CONFIG_PATH
        if SERVE_CONFIG_PATH.exists():
            console.print(f"\nResolution: default from {SERVE_CONFIG_PATH}")
        else:
            console.print(f"\nResolution: default (no {SERVE_CONFIG_PATH})")
        return

    console.print("\n[bold]Resolution path:[/bold]")
    for step in resolved.resolution_path:
        console.print(f"  {step}")


@serve.command('enable', short_help='Persistently enable a toolkit by default.')
@click.argument('toolkit')
def serve_enable(toolkit):
    """Persistently enable a toolkit (remove from default.toolkits.disabled)."""
    from .serve.config import load_serve_config, save_serve_config

    cfg = load_serve_config()
    if toolkit not in cfg.default.disabled_toolkits:
        console.print(f"[dim]'{toolkit}' is already enabled by default.[/dim]")
        return
    cfg.default.disabled_toolkits.remove(toolkit)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{toolkit}' will be served by default.")


@serve.command('disable', short_help='Persistently disable a toolkit by default.')
@click.argument('toolkit')
def serve_disable(toolkit):
    """Persistently disable a toolkit from the default serve set."""
    from .serve.config import load_serve_config, save_serve_config

    cfg = load_serve_config()
    if toolkit in cfg.default.disabled_toolkits:
        console.print(f"[dim]'{toolkit}' is already disabled.[/dim]")
        return
    cfg.default.disabled_toolkits.append(toolkit)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{toolkit}' will be skipped by default.")


@serve.command('enable-tool', short_help='Persistently enable a single tool by default.')
@click.argument('qualified', metavar='TOOLKIT__TOOL')
def serve_enable_tool(qualified):
    """Persistently re-enable a tool (remove from default.tools.disabled)."""
    from .serve.config import (
        load_serve_config, save_serve_config, _split_tool, ServeConfigError,
    )

    try:
        _split_tool(qualified)
    except ServeConfigError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)

    cfg = load_serve_config()
    if qualified not in cfg.default.disabled_tools:
        console.print(f"[dim]'{qualified}' is already enabled by default.[/dim]")
        return
    cfg.default.disabled_tools.remove(qualified)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{qualified}' will be served by default.")


@serve.command('disable-tool', short_help='Persistently disable a single tool by default.')
@click.argument('qualified', metavar='TOOLKIT__TOOL')
def serve_disable_tool(qualified):
    """Persistently disable a single tool from the default serve set."""
    from .serve.config import (
        load_serve_config, save_serve_config, _split_tool, ServeConfigError,
    )

    try:
        _split_tool(qualified)
    except ServeConfigError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)

    cfg = load_serve_config()
    if qualified in cfg.default.disabled_tools:
        console.print(f"[dim]'{qualified}' is already disabled.[/dim]")
        return
    cfg.default.disabled_tools.append(qualified)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{qualified}' will be skipped by default.")


@serve.command('config')
@click.option('--show', 'action', flag_value='show', default='show',
              help='Print the current serve config (default).')
@click.option('--edit', 'action', flag_value='edit',
              help='Open the serve config in $EDITOR.')
@click.option('--path', 'action', flag_value='path',
              help='Print the path to the serve config file.')
def serve_config(action):
    """Show, edit, or locate the serve config file."""
    from .serve.config import SERVE_CONFIG_PATH

    if action == 'path':
        console.print(str(SERVE_CONFIG_PATH))
        return
    if action == 'edit':
        click.edit(filename=str(SERVE_CONFIG_PATH))
        return
    # show
    if not SERVE_CONFIG_PATH.exists():
        console.print(f"[dim]No serve config yet at {SERVE_CONFIG_PATH}.[/dim]")
        console.print(
            "[dim]It will be created when you run a `toolbase serve` "
            "subcommand that edits state.[/dim]"
        )
        return
    console.print(SERVE_CONFIG_PATH.read_text())


@main.group()
def groups():
    """Manage tool groups in ~/.toolbase/serve.yaml."""
    pass


@groups.command('list', short_help='List all configured tool groups.')
def groups_list():
    """List all configured tool groups."""
    from .serve.config import load_serve_config

    cfg = load_serve_config()
    if not cfg.groups:
        console.print("[dim]No groups defined.[/dim]\n")
        console.print("Create one combining several toolkits:")
        console.print(
            "  [cyan]tb groups create exoplanet-pipeline aster arxiv-search[/cyan]\n"
        )
        console.print("Or include several toolkits but exclude a slow tool:")
        console.print(
            "  [cyan]tb groups create exoplanet-pipeline aster arxiv-search "
            "--exclude-tool aster__heavy_simulation[/cyan]\n"
        )
        console.print(
            "Then serve it with: [cyan]tb serve --group exoplanet-pipeline[/cyan]"
        )
        return
    for name, g in cfg.groups.items():
        console.print(f"[bold cyan]{name}[/bold cyan]")
        console.print(f"  toolkits: {', '.join(g.toolkits) or '(none)'}")
        if g.disabled_tools:
            console.print(f"  excludes: {', '.join(g.disabled_tools)}")


@groups.command(
    'create',
    short_help='Create a new group spanning multiple toolkits.',
)
@click.argument('name')
@click.argument('toolkits', nargs=-1, required=True)
@click.option(
    '--exclude-tool', 'exclude_tool', multiple=True, metavar='TOOLKIT__TOOL',
    help=(
        'Exclude a specific tool from the group (repeatable). The named '
        'toolkit must be one of the positional toolkits above.'
    ),
)
def groups_create(name, toolkits, exclude_tool):
    """Create a new group containing the given toolkits.

    \b
    A group must contain at least two toolkits — single-toolkit invocations
    are better expressed as `tb serve <toolkit>` directly.

    \b
    Examples:
        tb groups create exoplanet aster arxiv-search
        tb groups create exoplanet aster arxiv-search \\
            --exclude-tool aster__heavy_simulation
    """
    from .serve.config import (
        load_serve_config, save_serve_config, Group, _split_tool, ServeConfigError,
    )

    if len(toolkits) < 2:
        console.print(
            "[red]✗ A group must contain at least two toolkits.[/red]"
        )
        console.print(
            f"For a single toolkit, use [cyan]tb serve {toolkits[0]}[/cyan] "
            "directly — no group needed."
        )
        sys.exit(2)

    cfg = load_serve_config()
    if name in cfg.groups:
        console.print(f"[red]Group '{name}' already exists.[/red]")
        console.print(f"Use [cyan]tb groups edit[/cyan] to modify it.")
        sys.exit(1)

    # Validate every --exclude-tool reference: it must be well-shaped and
    # name a toolkit in this group.
    toolkit_set = set(toolkits)
    excludes: list[str] = []
    for q in exclude_tool:
        try:
            tk, _t = _split_tool(q)
        except ServeConfigError as e:
            console.print(f"[red]✗ {e}[/red]")
            sys.exit(2)
        if tk not in toolkit_set:
            console.print(
                f"[red]✗ --exclude-tool '{q}' references '{tk}', which "
                f"isn't in this group ({', '.join(toolkits)}).[/red]"
            )
            sys.exit(2)
        excludes.append(q)

    cfg.groups[name] = Group(
        name=name, toolkits=list(toolkits), disabled_tools=excludes,
    )
    save_serve_config(cfg)
    extra = f", excluding {len(excludes)} tool(s)" if excludes else ""
    console.print(
        f"[green]✓[/green] Created group '{name}' with {len(toolkits)} "
        f"toolkit(s){extra}."
    )


@groups.command('edit', short_help='Open serve.yaml in $EDITOR to edit groups.')
def groups_edit():
    """Open the serve config in $EDITOR (groups live under groups:)."""
    from .serve.config import SERVE_CONFIG_PATH

    # Ensure the file exists so $EDITOR has something to open.
    if not SERVE_CONFIG_PATH.exists():
        SERVE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        SERVE_CONFIG_PATH.write_text("groups: {}\n")
    click.edit(filename=str(SERVE_CONFIG_PATH))


@groups.command('delete', short_help='Delete a tool group from serve.yaml.')
@click.argument('name')
@_interactive_options
def groups_delete(name, yes, no_, no_input):
    """Delete a tool group from serve.yaml."""
    from .serve.config import load_serve_config, save_serve_config

    mode = _resolve_prompt_mode(yes, no_, no_input)
    cfg = load_serve_config()
    if name not in cfg.groups:
        console.print(f"[red]Group '{name}' does not exist.[/red]")
        sys.exit(1)
    if not _confirm(
        f"Delete group '{name}'?", default=False, mode=mode, consequential=True,
    ):
        console.print("[dim]Cancelled.[/dim]")
        sys.exit(0)
    del cfg.groups[name]
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] Deleted group '{name}'.")


@main.command()
@click.option(
    '-n', '--lines', 'lines', type=int, default=50,
    help='Number of lines to show from the tail (default 50).',
)
@click.option(
    '-f/-F', '--follow/--no-follow', 'follow', default=True,
    help='Follow the log as new lines are appended (default: follow).',
)
@click.option(
    '--all', 'show_all', is_flag=True, default=False,
    help='Show the whole log, not just the tail. Implies --no-follow unless -f is also given.',
)
@click.option(
    '--raw', is_flag=True, default=False,
    help='Include the JSON mirror lines (the lines starting with "# ") in the output.',
)
def logs(lines, follow, show_all, raw):
    """
    Tail the serve log.

    The orchestrator writes structured events and tool-call traces to
    ~/.toolbase/logs/serve.log whenever toolbase serve is running.
    This command renders that log with colors so you can watch tool calls
    fire in real time while Claude Code uses them.

    \b
    Examples:
        tb logs                   # tail and follow (Ctrl-C to stop)
        tb logs --no-follow       # last 50 lines, then exit
        tb logs -n 200            # last 200 lines and follow
        tb logs --all --no-follow # full log to stdout
    """
    from .logging.logger import SERVE_LOG_PATH
    import time

    log_path = SERVE_LOG_PATH

    if not log_path.exists():
        console.print(
            "[dim]No serve log yet at "
            f"{log_path}.[/dim]"
        )
        console.print(
            "[dim]Start `toolbase serve` (or have Claude Code launch it) "
            "to generate one.[/dim]"
        )
        sys.exit(0)

    def _render(line: str) -> None:
        """Print one log line with appropriate styling, or skip it."""
        stripped = line.rstrip("\n")
        if not stripped:
            console.print()
            return
        # JSON mirror lines start with "# {...}" — usually skip; the human
        # line just above carries the same info.
        if stripped.startswith("# {"):
            if raw:
                console.print(f"[dim]{stripped}[/dim]")
            return
        # Session marker bars / banner: highlight in bold.
        if stripped.startswith("═") or stripped.startswith("serve session started") or stripped.startswith("pid "):
            console.print(f"[bold cyan]{stripped}[/bold cyan]")
            return
        # Prune banner.
        if stripped.startswith("# --- serve.log pruned"):
            console.print(f"[dim]{stripped}[/dim]")
            return
        # Tool call completion lines carry ✓ / ✗.
        if "✓ Completed" in stripped:
            console.print(f"[green]{stripped}[/green]")
            return
        if "✗ Failed" in stripped:
            console.print(f"[red]{stripped}[/red]")
            return
        # Event lines: "[ts] event=<name> ..."
        if " event=" in stripped:
            # Color by level cue in the message: warn/error tokens win, else dim.
            lower = stripped.lower()
            if "level=error" in lower or "crashed" in lower or "failed_permanently" in lower:
                console.print(f"[red]{stripped}[/red]")
            elif "level=warn" in lower or "skipped" in lower or "restarting" in lower:
                console.print(f"[yellow]{stripped}[/yellow]")
            else:
                console.print(f"[cyan]{stripped}[/cyan]")
            return
        # Tool call start / output lines.
        if "::" in stripped:
            console.print(stripped)
            return
        # Fallback.
        console.print(f"[dim]{stripped}[/dim]")

    # Initial dump: either the whole file, or the last N lines.
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if show_all:
                initial = f.read().splitlines(keepends=True)
            else:
                # Read the whole file then keep last N — fine for serve.log
                # (capped at ~10 MB) and avoids reverse-seek complexity.
                initial = f.readlines()[-lines:]
            for line in initial:
                _render(line)
            offset = f.tell()
    except OSError as e:
        console.print(f"[red]Could not read {log_path}: {e}[/red]")
        sys.exit(1)

    if not follow and not show_all:
        return
    if show_all and not follow:
        return

    # Follow mode: poll for appended lines. Handles file truncation/rotation
    # by detecting size shrink and re-opening from the start.
    try:
        while True:
            try:
                size = log_path.stat().st_size
            except FileNotFoundError:
                # File was removed; wait for it to come back.
                time.sleep(1.0)
                continue
            if size < offset:
                # File was truncated or replaced; restart from the beginning.
                offset = 0
                console.print("[dim]--- log rotated, re-reading ---[/dim]")
            if size > offset:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    for line in f:
                        _render(line)
                    offset = f.tell()
            time.sleep(0.5)
    except KeyboardInterrupt:
        # Quiet exit on Ctrl-C — no traceback noise.
        return


def create_tarball(source_dir: Path, output_path: Path, toolkit_name: str):
    """
    Create a gzipped tarball of the toolkit.

    Excludes: .git/, __pycache__/, *.pyc, .DS_Store, venv/, .venv/

    Args:
        source_dir: Source directory to package
        output_path: Where to write the tarball
        toolkit_name: Name of the toolkit (not used in arcname)
    """
    exclude_patterns = {
        '.git', '__pycache__', '.pyc', '.DS_Store',
        'venv', '.venv', '.pytest_cache', '.mypy_cache',
        '.egg-info', 'dist', 'build', '.tox', 'htmlcov',
        '.coverage', '.env', '.vscode', '.idea'
    }

    def should_exclude(path: Path) -> bool:
        """Check if path should be excluded from tarball."""
        rel_path = path.relative_to(source_dir)

        # Check each part of the path
        for part in rel_path.parts:
            if part in exclude_patterns:
                return True
            # Check for patterns like *.pyc
            if part.endswith('.pyc') or part.endswith('.pyo'):
                return True
            if '.egg-info' in part:
                return True

        return False

    with tarfile.open(output_path, 'w:gz') as tar:
        # Iterate files only (not directories) and add them non-recursively.
        # ``tar.add(dir, recursive=True)`` (the default) would walk the tree
        # itself and add everything inside, bypassing should_exclude — that's
        # how __pycache__/ contents leak in, and how every regular file
        # ends up duplicated (once via the dir walk, once via this loop).
        # Tarfile creates intermediate directory entries automatically when
        # we add a file at a nested arcname.
        for item in source_dir.rglob('*'):
            if not item.is_file():
                continue
            if should_exclude(item):
                continue
            arcname = item.relative_to(source_dir)
            tar.add(item, arcname=arcname, recursive=False)


if __name__ == '__main__':
    main()