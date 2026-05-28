"""Client wiring for ``tb connect`` — write toolbase into an agent
client's MCP configuration so the user never copy-pastes JSON.

Adapter pattern: one ``ClientAdapter`` per client (Claude Code in v1;
Codex / Orchestral later). The CLI surface (``tb connect``) is shared;
each adapter knows its own config-file location, JSON shape, scope
vocabulary, and merge rules.
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
