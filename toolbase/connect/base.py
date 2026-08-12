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
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from ..skills import SkillTarget


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

    def skill_target(
        self, scope: str = "user", project_root: Optional[Path] = None,
    ) -> Optional["SkillTarget"]:
        """Where this harness surfaces a toolkit's skills for ``scope``, or
        ``None`` if it has no skill surface at that scope.

        Scoped the same way ``config_path`` is, and for the same reason: a
        harness that reads a project's MCP config generally reads a project's
        skills too, and surfacing the two at different scopes means the guide
        for a tool is in front of every agent while the tool is only in front
        of one. ``tb connect`` surfaces the activated toolkits' skills into
        the scope it wired; ``tb disconnect`` clears that scope.

        Raise ``ValueError`` for an unknown scope, as ``config_path`` does.
        Returning ``None`` says "this harness has no skill surface here" —
        the default, which keeps skill-less harnesses opt-out."""
        return None

    def legacy_skill_targets(self) -> List["SkillTarget"]:
        """Surfaces an older toolbase wrote skills into and this one no
        longer uses.

        A harness that grows a skill concept moves us off whatever we were
        approximating it with, and the files left behind keep being read.
        ``tb connect`` / ``tb disconnect`` clear these before surfacing, so
        the move happens on the next connect rather than needing a manual
        sweep. Default empty: no harness has moved."""
        return []
