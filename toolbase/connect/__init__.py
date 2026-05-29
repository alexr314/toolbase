"""Client wiring for ``tb connect`` — make toolbase's tools available to an
agent client so the user never copy-pastes config by hand.

Two shapes of client:

- **Config-file clients** (Claude Code; Codex later) get a ``ClientAdapter``
  (see ``base.py``). The adapter knows the client's config-file location,
  format, scope vocabulary, and merge rules; the CLI surface is shared.
- **Library clients** (orchestral) aren't configured by a file — they import
  toolbase. ``orchestral.py`` holds that integration (``toolbase_tools`` and
  the ``tb connect orchestral`` script generator); it is not a
  ``ClientAdapter`` and is dispatched separately in the CLI.
"""

from __future__ import annotations

from typing import Dict, List

from .base import ClientAdapter, RegistrationEntry, AvailabilityStatus
from .claude_code import ClaudeCodeAdapter


# Registry of known adapters, keyed by the name the user types
# (``tb connect <name>``).
_ADAPTERS: Dict[str, ClientAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
}


def get_adapter(name: str) -> ClientAdapter:
    """Return the adapter for ``name`` or raise ``KeyError``."""
    return _ADAPTERS[name]


def available_adapter_names() -> List[str]:
    return sorted(_ADAPTERS)


def all_adapters() -> List[ClientAdapter]:
    return [_ADAPTERS[n] for n in available_adapter_names()]


__all__ = [
    "ClientAdapter",
    "RegistrationEntry",
    "AvailabilityStatus",
    "ClaudeCodeAdapter",
    "get_adapter",
    "available_adapter_names",
    "all_adapters",
]
