"""Adapter contract for ``tb connect``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class AvailabilityStatus:
    """Whether a client is usable as a connect target on this machine."""

    detected: bool
    detail: str  # human-readable ("found ~/.claude.json", "claude CLI on PATH", ...)


@dataclass
class RegistrationEntry:
    """One discovered toolbase registration, for ``tb connect --list``."""

    client: str
    scope: str            # toolbase scope: "user" | "project"
    path: Path            # the config file
    present: bool         # is a toolbase server entry present?
    command: str = ""     # the wired command (e.g. "toolbase")
    args: Optional[List[str]] = None


class ClientAdapter(ABC):
    """Per-client adapter. Knows one client's config layout + scope map."""

    name: str  # e.g. "claude-code"

    @abstractmethod
    def is_available(self) -> AvailabilityStatus:
        """Whether this client is present / wireable on this machine."""

    @abstractmethod
    def supported_scopes(self) -> Dict[str, str]:
        """Map toolbase scope -> this client's native scope name."""

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
        """Report toolbase registrations across this client's scopes."""
