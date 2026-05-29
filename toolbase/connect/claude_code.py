"""Claude Code adapter for ``tb connect``.

Scopes (toolbase -> Claude Code):
- ``user``    -> ``~/.claude.json``, top-level ``mcpServers`` (all projects)
- ``project`` -> ``<root>/.mcp.json``, top-level ``mcpServers`` (team-shared,
  git-tracked)

The entry is a stdio server: ``{"type": "stdio", "command": ..., "args": [...]}``.
Writes are a non-destructive merge (only the toolbase key is touched; every
other server and top-level key is preserved) and atomic (tmp file + rename).
Malformed existing JSON is refused rather than clobbered.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from .base import (
    AvailabilityStatus, HarnessAdapter, HarnessConfigError, RegistrationEntry,
)


class ClaudeCodeConfigError(HarnessConfigError):
    """Existing Claude Code config is unreadable / malformed."""


class ClaudeCodeAdapter(HarnessAdapter):
    name = "claude-code"

    def project_scope_note(self) -> str:
        return (
            "Claude Code shows a one-time approval prompt the first time a "
            "project's .mcp.json is opened -- this is Claude's security model. "
            "Teammates who clone the repo see it once."
        )

    # ── detection ────────────────────────────────────────────────────

    def is_available(self) -> AvailabilityStatus:
        if shutil.which("claude"):
            return AvailabilityStatus(True, "claude CLI found on PATH")
        if (Path.home() / ".claude.json").exists():
            return AvailabilityStatus(True, "found ~/.claude.json")
        return AvailabilityStatus(
            False, "no `claude` CLI on PATH and no ~/.claude.json"
        )

    def supported_scopes(self) -> Dict[str, str]:
        return {"user": "user", "project": "project"}

    # ── paths ────────────────────────────────────────────────────────

    def config_path(self, scope: str, project_root: Optional[Path]) -> Path:
        if scope == "user":
            return Path.home() / ".claude.json"
        if scope == "project":
            if project_root is None:
                raise ValueError("project scope requires a project_root")
            return project_root / ".mcp.json"
        raise ValueError(f"unknown scope {scope!r}")

    # ── read / write ─────────────────────────────────────────────────

    @staticmethod
    def _read(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ClaudeCodeConfigError(
                f"{path} is not valid JSON ({e}); refusing to overwrite. "
                "Fix or remove it, then re-run."
            ) from e
        if not isinstance(data, dict):
            raise ClaudeCodeConfigError(
                f"{path} must be a JSON object at the top level."
            )
        return data

    @staticmethod
    def _write_atomic(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _entry(command: str, args: List[str], env: Optional[Dict[str, str]]) -> dict:
        entry: dict = {"type": "stdio", "command": command, "args": list(args)}
        if env:
            entry["env"] = dict(env)
        return entry

    # ── install / uninstall ──────────────────────────────────────────

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
        path = self.config_path(scope, project_root)
        data = self._read(path)
        servers = data.get("mcpServers")
        if servers is None:
            servers = {}
        elif not isinstance(servers, dict):
            raise ClaudeCodeConfigError(
                f"{path}: 'mcpServers' must be a JSON object."
            )
        servers[server_name] = self._entry(command, args, env)
        data["mcpServers"] = servers
        if not dry_run:
            self._write_atomic(path, data)
        return path

    def uninstall(
        self,
        *,
        scope: str,
        project_root: Optional[Path],
        server_name: str,
    ) -> bool:
        path = self.config_path(scope, project_root)
        if not path.exists():
            return False
        data = self._read(path)
        servers = data.get("mcpServers")
        if not isinstance(servers, dict) or server_name not in servers:
            return False
        del servers[server_name]
        # Leave an empty mcpServers object rather than reshaping the file.
        data["mcpServers"] = servers
        self._write_atomic(path, data)
        return True

    # ── status ───────────────────────────────────────────────────────

    def status(self, project_root: Optional[Path]) -> List[RegistrationEntry]:
        out: List[RegistrationEntry] = []
        scopes = [("user", None)]
        if project_root is not None:
            scopes.append(("project", project_root))
        for scope, root in scopes:
            path = self.config_path(scope, root)
            present = False
            command = ""
            args: Optional[List[str]] = None
            if path.exists():
                try:
                    data = self._read(path)
                except ClaudeCodeConfigError:
                    data = {}
                servers = data.get("mcpServers")
                if isinstance(servers, dict) and "toolbase" in servers:
                    entry = servers["toolbase"]
                    if isinstance(entry, dict):
                        present = True
                        command = entry.get("command", "")
                        args = entry.get("args")
            out.append(RegistrationEntry(
                harness=self.name, scope=scope, path=path,
                present=present, command=command, args=args,
            ))
        return out
