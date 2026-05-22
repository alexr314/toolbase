"""
Type-dispatched prompt helpers for the Tier-1 declarative install flow.

Each prompt:

- Takes a ``ConfigField`` and the resolved interactive ``mode`` from
  the existing ``cli._resolve_prompt_mode()`` (``"yes" | "no" |
  "skip" | "ask"``).
- Returns a ``PromptOutcome`` carrying either a parsed value, a
  "skipped" sentinel (user pressed Esc / non-TTY mode), or an error
  message for retry.

The wrapper ``prompt_for_field()`` dispatches on ``field.type`` and
handles per-type input (hidden text for secrets, y/n for booleans,
menu rendering for choices).

Why a dedicated module rather than reusing ``cli._require_input``: we
need richer behavior — an Esc-to-skip path, type-aware retry messages,
and choice menus rendered inline. The shared helpers stay
single-purpose; this module is the toolkit-config-specific surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import click
from rich.console import Console

from .schema import ConfigField, ConfigError, NEEDS_VALUE_SENTINEL, coerce_value


# Console writes to stderr because in the install flow stdout is the
# user's normal output channel — but we use stderr for prompts to
# keep them visible alongside Rich-styled status messages from the
# rest of the install.
_console = Console(stderr=True)


@dataclass
class PromptOutcome:
    """Result of one prompt attempt.

    Exactly one of ``value`` / ``skipped`` / ``cancelled`` is set:

    - ``value`` (with parsed type) — user gave a usable value.
    - ``skipped`` — user pressed Esc or the prompt was non-interactive
      and no default applied. The runner writes
      ``NEEDS_VALUE_SENTINEL`` if the field is required.
    - ``cancelled`` — user hit Ctrl-C / Ctrl-D. The runner aborts the
      whole install (this isn't "skip this field," it's "stop").
    """
    value: Any = None
    has_value: bool = False
    skipped: bool = False
    cancelled: bool = False


# Click sentinel for "user pressed Enter without typing." We can't use
# ``None`` as the default sentinel because some values legitimately
# parse to None.
_EMPTY = object()


def _format_label(field: ConfigField) -> str:
    """Compose the prompt label: 'name (type, required) — description'."""
    bits = [field.type]
    if field.required:
        bits.append("required")
    head = f"{field.name} ({', '.join(bits)})"
    if field.description:
        return f"{head}\n  {field.description}"
    return head


def _print_prompt_intro(field: ConfigField) -> None:
    _console.print()
    _console.print(f"[bold cyan]{field.name}[/bold cyan]", end="")
    suffix = []
    if field.required:
        suffix.append("[yellow]required[/yellow]")
    suffix.append(f"type={field.type}")
    _console.print(f"  ({', '.join(suffix)})")
    if field.description:
        _console.print(f"  [dim]{field.description}[/dim]")


def prompt_for_field(field: ConfigField, mode: str) -> PromptOutcome:
    """Prompt for one field and return a parsed outcome.

    ``mode`` semantics:

    - ``"yes"`` — accept the field's default if any; otherwise skip
      (writes ``NEEDS_VALUE_SENTINEL`` for required, omits for optional).
      ``--yes`` is for confirmation prompts, not input prompts; this
      treats it as "skip with default" so an agent passing ``--yes``
      to ``tb install`` gets a predictable post-install state.
    - ``"no"`` — same as ``"skip"``: don't prompt, accept default if
      one exists, otherwise mark NEEDS_VALUE for required fields.
    - ``"skip"`` — non-interactive: use default if present, otherwise
      mark NEEDS_VALUE.
    - ``"ask"`` — prompt the user (TTY).

    Validation: invalid input retries up to 3 times in ``"ask"`` mode
    before giving up and marking the field skipped (the user can edit
    the file later). In non-interactive modes, an invalid default is a
    hard error — that's the toolkit author's bug, surface it loudly.
    """
    if mode in ("yes", "no", "skip"):
        return _resolve_non_interactive(field, mode)

    return _resolve_interactive(field)


def _resolve_non_interactive(field: ConfigField, mode: str) -> PromptOutcome:
    """No TTY — accept default or write NEEDS_VALUE.

    Boolean and choice fields with a default behave the same as any
    other type: take the default. The ``mode`` is informational only;
    yes/no/skip all collapse to the same path.
    """
    if field.default is not None:
        try:
            value = coerce_value(field, field.default)
        except ConfigError:
            # The schema validator should already have caught this at
            # parse time, but be defensive: fall through to skip.
            return PromptOutcome(skipped=True)
        return PromptOutcome(value=value, has_value=True)
    return PromptOutcome(skipped=True)


def _resolve_interactive(field: ConfigField) -> PromptOutcome:
    """TTY — render type-aware prompt, retry on validation failure."""
    _print_prompt_intro(field)

    if field.type == "boolean":
        return _prompt_boolean(field)
    if field.type == "choice":
        return _prompt_choice(field)
    return _prompt_textual(field)


def _prompt_textual(field: ConfigField) -> PromptOutcome:
    """Common prompt for string/secret/path/integer/float."""
    is_secret = field.type == "secret"
    default_repr = "" if field.default is None else str(field.default)

    for attempt in range(3):
        try:
            raw = click.prompt(
                "  enter value (Enter = skip)",
                default=default_repr,
                show_default=bool(default_repr) and not is_secret,
                hide_input=is_secret,
                err=True,
            )
        except click.exceptions.Abort:
            # Ctrl-C or Ctrl-D
            return PromptOutcome(cancelled=True)

        # Empty input → skip (or default if defined).
        if raw == "" or raw is None:
            if field.default is not None:
                try:
                    return PromptOutcome(
                        value=coerce_value(field, field.default),
                        has_value=True,
                    )
                except ConfigError:
                    return PromptOutcome(skipped=True)
            return PromptOutcome(skipped=True)

        try:
            return PromptOutcome(
                value=coerce_value(field, raw),
                has_value=True,
            )
        except ConfigError as e:
            _console.print(f"  [red]✗[/red] {e}")
            if attempt < 2:
                _console.print("  [dim]Try again, or press Enter to skip.[/dim]")

    _console.print(
        "  [yellow]Too many invalid attempts; skipping. Edit the config "
        "file later.[/yellow]"
    )
    return PromptOutcome(skipped=True)


def _prompt_boolean(field: ConfigField) -> PromptOutcome:
    """y/n/Enter prompt with optional default."""
    default = field.default if isinstance(field.default, bool) else None
    suffix = ""
    if default is True:
        suffix = " [Y/n]"
    elif default is False:
        suffix = " [y/N]"
    else:
        suffix = " [y/n]"
    try:
        raw = click.prompt(
            f"  {suffix.strip()}",
            default="" if default is None else ("y" if default else "n"),
            show_default=False,
            err=True,
        )
    except click.exceptions.Abort:
        return PromptOutcome(cancelled=True)

    if raw == "":
        if default is not None:
            return PromptOutcome(value=default, has_value=True)
        return PromptOutcome(skipped=True)

    try:
        return PromptOutcome(
            value=coerce_value(field, raw),
            has_value=True,
        )
    except ConfigError as e:
        _console.print(f"  [red]✗[/red] {e}; skipping.")
        return PromptOutcome(skipped=True)


def _prompt_choice(field: ConfigField) -> PromptOutcome:
    """Numbered menu of options."""
    options = field.options or []
    for i, opt in enumerate(options, start=1):
        marker = " (default)" if opt == field.default else ""
        _console.print(f"  {i}. {opt}{marker}")
    default_idx: Optional[int] = None
    if field.default in options:
        default_idx = options.index(field.default) + 1

    for attempt in range(3):
        try:
            raw = click.prompt(
                f"  pick 1-{len(options)} (Enter = skip)",
                default="" if default_idx is None else str(default_idx),
                show_default=default_idx is not None,
                err=True,
            )
        except click.exceptions.Abort:
            return PromptOutcome(cancelled=True)

        if raw == "":
            if default_idx is not None:
                return PromptOutcome(
                    value=options[default_idx - 1],
                    has_value=True,
                )
            return PromptOutcome(skipped=True)

        # Accept either the index or the literal option string.
        try:
            idx = int(raw.strip())
            if 1 <= idx <= len(options):
                return PromptOutcome(value=options[idx - 1], has_value=True)
            _console.print(f"  [red]✗[/red] choice must be 1..{len(options)}")
            continue
        except ValueError:
            pass

        if raw in options:
            return PromptOutcome(value=raw, has_value=True)
        _console.print(f"  [red]✗[/red] {raw!r} is not in the list")

    _console.print("  [yellow]Skipping.[/yellow]")
    return PromptOutcome(skipped=True)
