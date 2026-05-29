"""Adapter contract for ``tb connect``.

A *harness* is an agent runtime you serve tools to (Claude Code, Codex,
Orchestral). Config-file harnesses (Claude Code, Codex) connect as MCP clients
and get a ``HarnessAdapter`` here; library harnesses (Orchestral) are handled
separately (see ``orchestral.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


class HarnessConfigError(Exception):
    """A harness's existing config is unreadable / malformed.

    Shared base so the CLI can catch one type across all adapters; each
    adapter may subclass it (e.g. ``ClaudeCodeConfigError``, ``CodexConfigError``).
    """


@dataclass
class AvailabilityStatus:
    """Whether a harness is usable as a connect target on this machine."""

    detected: bool
    detail: str  # human-readable ("found ~/.claude.json", "claude CLI on PATH", ...)


@dataclass
class RegistrationEntry:
    """One discovered toolbase registration, for ``tb connect --list``."""

    harness: str
    scope: str            # toolbase scope: "user" | "project"
    path: Path            # the config file
    present: bool         # is a toolbase server entry present?
    command: str = ""     # the wired command (e.g. "toolbase")
    args: Optional[List[str]] = None


class HarnessAdapter(ABC):
    """Per-harness adapter for a config-file harness (Claude Code, Codex).

    Knows one harness's config layout + scope map. Library harnesses that
    import toolbase rather than reading a config file (Orchestral) are not
    adapters — see ``orchestral.py``.
    """

    name: str  # e.g. "claude-code"

    @abstractmethod
    def is_available(self) -> AvailabilityStatus:
        """Whether this harness is present / wireable on this machine."""

    @abstractmethod
    def supported_scopes(self) -> Dict[str, str]:
        """Map toolbase scope -> this harness's native scope name."""

    @abstractmethod
    def config_path(self, scope: str, project_root: Optional[Path]) -> Path:
        """Config file this scope writes to."""

    @abstractmethod
    def install(
        self,
        *,
        scope: str,
        project_root: Optional[Path],
        server_name: str,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Path:
        """Write the server entry (non-destructive merge). Returns the path.

        With ``dry_run`` the intended write is computed and the path is
        returned, but nothing is written.
        """

    @abstractmethod
    def uninstall(
        self,
        *,
        scope: str,
        project_root: Optional[Path],
        server_name: str,
    ) -> bool:
        """Remove the server entry. Returns True if something was removed."""

    @abstractmethod
    def status(self, project_root: Optional[Path]) -> List[RegistrationEntry]:
        """Report toolbase registrations across this harness's scopes."""

    def project_scope_note(self) -> Optional[str]:
        """A harness-specific caveat to print after a project-scope connect
        (e.g. a first-use trust prompt). ``None`` means nothing to add."""
        return None
