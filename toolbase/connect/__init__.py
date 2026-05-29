"""Harness wiring for ``tb connect`` — make toolbase's tools available to an
agent harness so the user never copy-pastes config by hand.

Two shapes of harness:

- **Config-file harnesses** (Claude Code writes JSON; Codex writes TOML) connect
  as MCP clients and get a ``HarnessAdapter`` (see ``base.py``). The adapter
  knows the harness's config-file location, format, scope vocabulary, and merge
  rules; the CLI surface is shared.
- **Library harnesses** (Orchestral) aren't configured by a file — they import
  toolbase. ``orchestral.py`` holds that integration (``toolbase_tools`` and the
  ``tb connect orchestral`` script generator); it is not a ``HarnessAdapter``
  and is dispatched separately in the CLI.
"""

from __future__ import annotations

from typing import Dict, List

from .base import HarnessAdapter, RegistrationEntry, AvailabilityStatus
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter


# Registry of config-file harness adapters, keyed by the name the user types
# (``tb connect <name>``).
_ADAPTERS: Dict[str, HarnessAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
}


def get_adapter(name: str) -> HarnessAdapter:
    """Return the adapter for ``name`` or raise ``KeyError``."""
    return _ADAPTERS[name]


def available_adapter_names() -> List[str]:
    return sorted(_ADAPTERS)


def all_adapters() -> List[HarnessAdapter]:
    return [_ADAPTERS[n] for n in available_adapter_names()]


__all__ = [
    "HarnessAdapter",
    "RegistrationEntry",
    "AvailabilityStatus",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "get_adapter",
    "available_adapter_names",
    "all_adapters",
]
