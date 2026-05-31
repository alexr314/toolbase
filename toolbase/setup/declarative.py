"""
Tier-1 install-time runner and serve-time state-config resolver.

Two entry points:

- ``run_install_setup(name, schema, mode)`` — called from
  ``toolbase install`` after env setup. Walks the schema, prompts
  the user (TTY) or fills in defaults (non-TTY), writes
  ``~/.toolbase/config/<name>.yaml``. Always succeeds: required
  fields the user can't supply land as ``NEEDS_VALUE_SENTINEL`` and
  ``serve`` will refuse the toolkit until they're filled.

- ``load_state_config(name, schema)`` — called from
  ``serve/orchestrator.py`` at startup. Reads the YAML, validates each
  filled field against its schema entry, returns
  ``(state_config_dict, missing_or_invalid)``. The orchestrator passes
  the dict via ``--state-config`` to the toolkit subprocess, where
  ``_inject_state_into_tools`` writes it onto the tool instances.

The shape of the state-config dict (per Item 3 sketch sign-off
2026-05-06): **flat** — ``{state_field_name: value}``. Not nested
per-tool. One toolkit's config applies across every tool in that
toolkit.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from .prompts import PromptOutcome, prompt_for_field
from .schema import (
    ConfigError,
    ConfigField,
    ConfigSchema,
    NEEDS_VALUE_SENTINEL,
    _TEMPLATE_PATTERN,
    coerce_value,
)
from .storage import config_path, load_config, save_config


# Header comment seeded into newly-written config files. Sets the
# expectation that this file is canonical and editing is encouraged.
_FILE_HEADER = """\
# Configuration for {toolkit} — managed by toolbase.
#
# This file is canonical: edit anytime, changes apply on next
# `toolbase serve`. Required fields with the value <NEEDS VALUE>
# must be filled in before the toolkit will start.
#
# Run `toolbase config show {toolkit}` to inspect (secrets masked),
# `toolbase config edit {toolkit}` to open in $EDITOR, or
# `toolbase config set {toolkit} <key> <value>` to set one field.
"""


# ── install-time runner ──────────────────────────────────────────────


@dataclass
class InstallSetupResult:
    """Outcome of ``run_install_setup`` — what the user sees on completion."""
    config_file: Path
    fields_filled: List[str] = dc_field(default_factory=list)
    fields_skipped_optional: List[str] = dc_field(default_factory=list)
    fields_skipped_required: List[str] = dc_field(default_factory=list)
    cancelled: bool = False

    @property
    def needs_attention(self) -> bool:
        """True if the user has more work to do before serve will accept this toolkit."""
        return bool(self.fields_skipped_required) or self.cancelled


def run_install_setup(
    toolkit_name: str,
    schema: ConfigSchema,
    *,
    mode: str = "ask",
    base: Optional[Path] = None,
    console: Optional[Console] = None,
) -> InstallSetupResult:
    """Walk the schema, prompt the user, write the config file.

    ``mode`` is the resolved interactive mode from
    ``cli._resolve_prompt_mode``. ``"ask"`` prompts; ``"skip" | "yes"
    | "no"`` fills defaults and marks required-no-default fields as
    ``NEEDS_VALUE_SENTINEL``.

    The function never raises on prompt-level failures (bad input,
    cancelled prompt). It returns an ``InstallSetupResult`` whose
    ``needs_attention`` flag the caller uses to print the right
    follow-up message. The only thing that can raise here is a real
    OS-level write failure on the config file, which we let propagate.

    Idempotent on re-run: an existing config file is loaded first and
    fields that already have valid values are kept, so reinstalling a
    toolkit doesn't blow away a working config. Re-running
    ``run_install_setup`` only prompts for fields that are missing,
    invalid, or carry the NEEDS_VALUE_SENTINEL.
    """
    out = console or Console(stderr=True)
    existing = load_config(toolkit_name, base=base)
    result = InstallSetupResult(
        config_file=config_path(toolkit_name, base=base),
    )

    if not schema.fields:
        # Empty schema — nothing to ask. Don't even create a file.
        return result

    if mode == "ask":
        out.print(
            f"\n[bold]Configuring {toolkit_name}[/bold] "
            "([dim]you can edit this anytime; press Enter to skip[/dim])"
        )
    else:
        out.print(
            f"\n[bold]Configuring {toolkit_name}[/bold] "
            "([dim]non-interactive mode: filling defaults[/dim])"
        )

    for field in schema.fields:
        # Existing valid value? Keep it.
        if field.name in existing and existing[field.name] != NEEDS_VALUE_SENTINEL:
            try:
                # Validate (and re-coerce, e.g. tilde-expand paths) but
                # don't error here — if the user's stored value is
                # invalid, surface that in the result.
                coerce_value(field, existing[field.name])
                result.fields_filled.append(field.name)
                continue
            except ConfigError:
                # Stored value is bad; reprompt.
                pass

        outcome = prompt_for_field(field, mode)
        if outcome.cancelled:
            out.print("\n[yellow]Setup cancelled.[/yellow]")
            result.cancelled = True
            break
        if outcome.has_value:
            existing[field.name] = outcome.value
            result.fields_filled.append(field.name)
        else:
            # Skipped.
            if field.required:
                existing[field.name] = NEEDS_VALUE_SENTINEL
                result.fields_skipped_required.append(field.name)
            else:
                # Optional & no default & user didn't supply one —
                # don't write the key at all. Tools handle their own
                # absent-optional defaults.
                if field.name in existing and existing[field.name] == NEEDS_VALUE_SENTINEL:
                    # Was previously required-but-skipped, now optional? Leave it.
                    pass
                result.fields_skipped_optional.append(field.name)

    save_config(
        toolkit_name,
        existing,
        base=base,
        header_comment=_FILE_HEADER.format(toolkit=toolkit_name),
    )

    _print_install_summary(out, toolkit_name, result, mode)
    return result


def _print_install_summary(
    console: Console,
    toolkit_name: str,
    result: InstallSetupResult,
    mode: str,
) -> None:
    if result.cancelled:
        console.print(
            f"  [yellow]Partial config written: {result.config_file}[/yellow]"
        )
        return

    if result.fields_skipped_required:
        console.print(
            f"\n  [yellow]Required fields needing values:[/yellow] "
            + ", ".join(result.fields_skipped_required)
        )
        console.print(f"  [dim]Edit:[/dim] {result.config_file}")
        console.print(
            f"  [dim]Or run:[/dim] "
            f"toolbase config set {toolkit_name} <key> <value>"
        )
    else:
        if result.fields_filled or mode == "ask":
            console.print(
                f"  [green]✓[/green] config written: {result.config_file}"
            )


# ── serve-time resolver ──────────────────────────────────────────────


@dataclass
class StateConfigResolution:
    """Outcome of ``load_state_config``.

    - ``state_config`` — flat ``{state_field_name: value}`` ready to
      hand to the toolkit subprocess via ``--state-config``. Only
      contains validated values; missing/invalid required fields are
      *not* present (the orchestrator skips the toolkit on
      ``missing_required``).
    - ``missing_required`` — names of required fields that aren't
      filled in (or carry the ``NEEDS_VALUE_SENTINEL``). When
      non-empty, the toolkit is not safe to serve.
    - ``invalid`` — ``[(name, error_message), ...]`` for fields
      whose stored value doesn't validate against the schema. Treated
      the same as missing_required for go/no-go.
    """
    state_config: Dict[str, Any] = dc_field(default_factory=dict)
    missing_required: List[str] = dc_field(default_factory=list)
    invalid: List[Tuple[str, str]] = dc_field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if the toolkit's required config is fully filled in."""
        return not self.missing_required and not self.invalid

    def skip_reason(self) -> Optional[str]:
        """Compose the human-readable skip message for serve startup."""
        if self.ok:
            return None
        bits: List[str] = []
        if self.missing_required:
            bits.append(
                "missing required: " + ", ".join(self.missing_required)
            )
        if self.invalid:
            bits.append(
                "invalid: " + ", ".join(f"{n} ({e})" for n, e in self.invalid)
            )
        return "; ".join(bits)


def _expand_default_template(
    raw: str, *, project_root: Optional[Path]
) -> str:
    """Expand ``${CWD}`` and ``${PROJECT_ROOT}`` in a schema default value.

    Called at serve time (from ``load_state_config``) so the resolved
    value reflects the orchestrator's process state, NOT the install /
    config-write moment. ``${CWD}`` becomes ``os.getcwd()`` — which when
    ``tb serve`` is launched by a harness (Claude Code, Codex, …) is
    the harness's launch directory, i.e. where the agent thinks it's
    working. ``${PROJECT_ROOT}`` becomes the discovered ``.toolbase/``
    parent if there is one; otherwise it falls back to ``${CWD}``.

    Unknown templates are unreachable here — they're rejected at schema
    parse time — but we still raise loudly if one slips through so the
    failure mode is "skip this toolkit with a clear reason" rather than
    "inject the literal string ``${BANANA}`` as a path."
    """
    import os

    def _sub(match):
        var = match.group(1)
        if var == "CWD":
            return os.getcwd()
        if var == "PROJECT_ROOT":
            if project_root is not None:
                return str(project_root)
            return os.getcwd()
        raise ConfigError(
            f"unknown template variable ${{{var}}} in default value"
        )

    return _TEMPLATE_PATTERN.sub(_sub, raw)


def load_state_config(
    toolkit_name: str,
    schema: ConfigSchema,
    *,
    base: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> StateConfigResolution:
    """Read the toolkit's stored config and validate it against the schema.

    Returns a ``StateConfigResolution`` that tells the orchestrator
    whether to serve the toolkit and (if so) what state values to
    inject. Never raises; the orchestrator wants a clean go/no-go
    answer it can render in the startup banner.

    Phase 4 (0.5.0): if ``project_root`` is supplied, the project layer
    at ``<project_root>/.toolbase/config/<toolkit>.yaml`` is read and
    merged on top of the user layer (project wins key-by-key). When
    ``project_root`` is ``None`` (the legacy / test path), only the
    user layer is consulted — matching the 0.4.x behavior.

    Optional fields:
        - With a stored value → validated, included in state_config.
        - Without a stored value → omitted (the tool's own default
          handles it).

    Required fields:
        - With a valid stored value → included.
        - Missing or NEEDS_VALUE_SENTINEL → flagged in
          ``missing_required`` UNLESS the schema declares a default
          (literal or template), in which case the default is
          injected and the field counts as satisfied.
        - Stored but invalid → flagged in ``invalid``, not included.

    Template defaults (``${CWD}``, ``${PROJECT_ROOT}``) are expanded
    here, in the orchestrator's process — so ``${CWD}`` resolves to
    the harness's launch directory (the agent's workspace), not to
    wherever the user ran ``tb install``.
    """
    resolution = StateConfigResolution()
    try:
        user_stored = load_config(toolkit_name, base=base)
        if project_root is not None:
            project_stored = load_config(
                toolkit_name, base=base,
                layer="project", project_root=project_root,
            )
            # Merge: user → project; project wins key-by-key. We use a
            # plain dict here because we don't need to preserve comments
            # at this stage — the merged view is consumed as data.
            stored = dict(user_stored)
            for k, v in project_stored.items():
                if k == "schema_version":
                    continue
                stored[k] = v
        else:
            stored = user_stored
        # The schema_version envelope is a file-format concern, never a
        # state field. Strip it before validation / pass-through.
        if "schema_version" in stored:
            stored = {k: v for k, v in stored.items() if k != "schema_version"}
    except ValueError as e:
        # Malformed YAML — treat as "everything missing." The
        # orchestrator surfaces this as a skip with the parse error.
        resolution.invalid.append(("<file>", str(e)))
        # Still mark every required field missing so the user sees
        # *what* is missing, not just that the file is broken.
        for f in schema.required_fields():
            resolution.missing_required.append(f.name)
        return resolution

    declared_names = {f.name for f in schema.fields}

    for field in schema.fields:
        raw = stored.get(field.name) if field.name in stored else None

        if raw is None or raw == NEEDS_VALUE_SENTINEL:
            # No user-supplied value. If the schema has a default
            # (literal or template), use it — this satisfies required
            # fields whose default fully determines a sensible value
            # (e.g. ``base_directory: ${CWD}``). Otherwise:
            #   - required → flagged missing
            #   - optional → omitted (tool handles its own absent default)
            if field.default is None:
                if field.required:
                    resolution.missing_required.append(field.name)
                continue
            raw = field.default

        # Templates only meaningful as schema defaults; expand here so
        # value coercion sees the resolved path. We also expand if a
        # user pasted a template into their config file — same semantics
        # in either spot.
        if isinstance(raw, str) and _TEMPLATE_PATTERN.search(raw):
            try:
                raw = _expand_default_template(raw, project_root=project_root)
            except ConfigError as e:
                resolution.invalid.append((field.name, str(e)))
                continue

        try:
            value = coerce_value(field, raw)
        except ConfigError as e:
            resolution.invalid.append((field.name, str(e)))
            continue

        resolution.state_config[field.name] = _serializable(value)

    # Pass-through for extras not declared in the config: schema. These
    # are values written via ``ctx.set_config`` from a Tier-2 setup.py
    # that aren't declared as Tier-1 fields — derived state like a
    # detected GPU bool or a download path. The orchestrator forwards
    # them to tools that declare them in ``state=[...]``; tools that
    # don't reference them simply don't see them.
    #
    # We don't validate these (no schema to validate against). We do
    # serialize them through ``_serializable`` for the JSON wire.
    for key, value in stored.items():
        if key in declared_names:
            continue
        if value is None or value == NEEDS_VALUE_SENTINEL:
            continue
        resolution.state_config[key] = _serializable(value)

    return resolution


def _serializable(value: Any) -> Any:
    """Make a value JSON-encodable for the --state-config handoff.

    The only non-JSON-native type our schema produces is ``Path``,
    which our coerce_value already returns as a string. This is a
    defensive belt-and-suspenders that catches future-type-additions
    that might forget the conversion.
    """
    if isinstance(value, Path):
        return str(value)
    return value
