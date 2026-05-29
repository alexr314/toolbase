"""Client wiring for ``tb connect`` — write toolbase into an agent
client's MCP configuration so the user never copy-pastes config by hand.

Adapter pattern: one ``ClientAdapter`` per config-file client (Claude Code
writes JSON; Codex writes TOML). The CLI surface (``tb connect``) is shared;
each adapter knows its own config-file location, format, scope vocabulary,
and merge rules. (Library clients like Orchestral aren't config-file
clients — they import toolbase — so they're handled separately, not here.)
"""

from __future__ import annotations

from typing import Dict, List

from .base import ClientAdapter, RegistrationEntry, AvailabilityStatus
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter


# Registry of known adapters, keyed by the name the user types
# (``tb connect <name>``).
_ADAPTERS: Dict[str, ClientAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
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
    "CodexAdapter",
    "get_adapter",
    "available_adapter_names",
    "all_adapters",
]
