"""Antigravity adapter for ``tb connect``.

Google's Antigravity keeps *one* MCP config shared by the ``agy`` CLI, the
Antigravity IDE, and the SDK, so a single adapter wires all three.

Config lives at a **customization root** — the directory that holds
``mcp_config.json``, ``skills/``, ``rules/``, ``plugins/`` and ``hooks.json``.
There are two roots, which is our scope map:

- ``user``    -> ``~/.gemini/config/mcp_config.json`` (global root)
- ``project`` -> ``<root>/.agents/mcp_config.json`` (workspace root)

The entry is a stdio MCP server under the top-level ``mcpServers`` key::

    {
      "mcpServers": {
        "toolbase": {
          "command": "toolbase",
          "args": ["serve"]
        }
      }
    }

Two Antigravity-specific wrinkles:

- The IDE creates ``~/.gemini/config/mcp_config.json`` as a **zero-byte file**.
  An empty (or whitespace-only) file is read as ``{}`` rather than refused, so
  a first connect on a fresh install just works.
- Workspace scope does nothing on the ``agy`` CLI: it starts no server from
  ``.agents/mcp_config.json`` (measured — the same entry in the global root
  starts one at boot). Google documents workspace config for the IDE and the
  SDK, which we haven't verified; antigravity-cli#60 is a related report
  against a different path. Hence ``project_scope_note``.

Writes are a non-destructive merge (only the toolbase entry under
``mcpServers`` is touched) and atomic (tmp file + rename). Malformed existing
JSON is refused rather than clobbered.
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

# Workspace customization root, relative to the project root.
WORKSPACE_ROOT_DIR = ".agents"
_CONFIG_NAME = "mcp_config.json"


class AntigravityConfigError(HarnessConfigError):
    """Existing Antigravity config is unreadable / malformed."""


def _global_root() -> Path:
    """The global customization root, ``~/.gemini/config``."""
    return Path.home() / ".gemini" / "config"


class AntigravityAdapter(HarnessAdapter):
    name = "antigravity"

    def project_scope_note(self) -> str:
        return (
            "the agy CLI does not load a workspace .agents/mcp_config.json — "
            "only the IDE and SDK are documented to. Use "
            "`tb connect antigravity -u` if you're on the CLI."
        )

    # ── detection ────────────────────────────────────────────────────

    def is_available(self) -> AvailabilityStatus:
        if shutil.which("agy"):
            return AvailabilityStatus(True, "agy CLI found on PATH")
        # ~/.gemini alone means the Gemini CLI, not Antigravity -- key on the
        # customization root and Antigravity's own state dirs instead.
        if (_global_root() / _CONFIG_NAME).exists():
            return AvailabilityStatus(True, "found ~/.gemini/config/mcp_config.json")
        gemini = Path.home() / ".gemini"
        for d in ("antigravity", "antigravity-cli", "antigravity-ide"):
            if (gemini / d).exists():
                return AvailabilityStatus(True, f"found ~/.gemini/{d}")
        return AvailabilityStatus(
            False, "no `agy` CLI on PATH and no ~/.gemini/config"
        )

    def supported_scopes(self) -> Dict[str, str]:
        return {"user": "global", "project": "workspace"}

    def skill_target(self):
        # ~/.gemini/config/skills/<toolkit>__<skill>/SKILL.md -- Antigravity's
        # native skill layout is Claude Code's: a directory per skill holding a
        # SKILL.md with name+description frontmatter, loaded on demand. Skills
        # sit in the customization root next to mcp_config.json; we use the
        # global root, matching the other harnesses' user-global surfacing.
        from ..skills import SkillTarget
        return SkillTarget(
            harness=self.name, root=_global_root() / "skills",
            layout="dir", keep_frontmatter=True,
        )

    # ── paths ────────────────────────────────────────────────────────

    def config_path(self, scope: str, project_root: Optional[Path]) -> Path:
        if scope == "user":
            return _global_root() / _CONFIG_NAME
        if scope == "project":
            if project_root is None:
                raise ValueError("project scope requires a project_root")
            return project_root / WORKSPACE_ROOT_DIR / _CONFIG_NAME
        raise ValueError(f"unknown scope {scope!r}")

    # ── read / write ─────────────────────────────────────────────────

    @staticmethod
    def _read(path: Path) -> dict:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        # The IDE ships a zero-byte mcp_config.json; that's an empty config,
        # not a malformed one.
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            hint = ""
            if "//" in text or "/*" in text:
                hint = (
                    " Antigravity's mcp_config.json doesn't support comments — "
                    "remove them and re-run."
                )
            raise AntigravityConfigError(
                f"{path} is not valid JSON ({e}); refusing to overwrite.{hint}"
            ) from e
        if not isinstance(data, dict):
            raise AntigravityConfigError(
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
        entry: dict = {"command": command, "args": list(args)}
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
            raise AntigravityConfigError(
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
                except AntigravityConfigError:
                    data = {}
                servers = data.get("mcpServers")
                if isinstance(servers, dict) and "toolbase" in servers:
                    entry = servers["toolbase"]
                    if isinstance(entry, dict):
                        present = True
                        command = str(entry.get("command", ""))
                        raw = entry.get("args")
                        if isinstance(raw, list):
                            args = [str(a) for a in raw]
            out.append(RegistrationEntry(
                harness=self.name, scope=scope, path=path,
                present=present, command=command, args=args,
            ))
        return out
