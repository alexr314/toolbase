"""OpenCode adapter for ``tb connect``.

Scopes (toolbase -> OpenCode):
- ``user``    -> ``~/.config/opencode/opencode.json`` (honors ``$XDG_CONFIG_HOME``)
- ``project`` -> ``<root>/opencode.json``

OpenCode merges the project config over the global one key-by-key, so both
scopes are real. The MCP entry lives under the top-level ``mcp`` key as a
*local* (stdio) server, and — unlike Claude Code / Codex — the command and its
arguments are a single ``command`` array::

    {
      "$schema": "https://opencode.ai/config.json",
      "mcp": {
        "toolbase": {
          "type": "local",
          "command": ["toolbase", "serve"],
          "enabled": true
        }
      }
    }

Config files may be ``.json`` or ``.jsonc``. We edit an existing ``.jsonc`` in
place when that is what the user has (so we don't create a competing
``.json``), and refuse to overwrite a ``.jsonc`` that actually carries comments
rather than silently dropping them — the same "refuse rather than clobber"
discipline the Claude Code adapter applies to malformed JSON.

Writes are a non-destructive merge (only the toolbase entry under ``mcp`` is
touched; every other server, top-level key, and ``$schema`` is preserved) and
atomic (tmp file + rename).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from .base import (
    AvailabilityStatus, HarnessAdapter, HarnessConfigError, RegistrationEntry,
)

_SCHEMA_URL = "https://opencode.ai/config.json"


class OpenCodeConfigError(HarnessConfigError):
    """Existing OpenCode config is unreadable / malformed."""


def _config_home() -> Path:
    """``$XDG_CONFIG_HOME`` (when set and non-empty) or ``~/.config``."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


class OpenCodeAdapter(HarnessAdapter):
    name = "opencode"

    def project_scope_note(self) -> str:
        return (
            "OpenCode merges a project's opencode.json over your global config "
            "automatically — no extra trust step is required."
        )

    # ── detection ────────────────────────────────────────────────────

    def is_available(self) -> AvailabilityStatus:
        import shutil
        if shutil.which("opencode"):
            return AvailabilityStatus(True, "opencode CLI found on PATH")
        if (_config_home() / "opencode").exists():
            return AvailabilityStatus(True, "found ~/.config/opencode")
        return AvailabilityStatus(
            False, "no `opencode` CLI on PATH and no ~/.config/opencode"
        )

    def supported_scopes(self) -> Dict[str, str]:
        return {"user": "global", "project": "project"}

    def skill_target(self, scope="user", project_root=None):
        # <root>/skills/<toolkit>__<skill>/SKILL.md — OpenCode's skill loader
        # scans `**/SKILL.md` under ~/.config/opencode/skills and a project's
        # .opencode/skills, and surfaces each to the model by its
        # `description`. Frontmatter is kept: a skill without a description is
        # filtered out and never reaches the model.
        from ..skills import SkillTarget
        if scope == "user":
            root = _config_home() / "opencode" / "skills"
        elif scope == "project":
            if project_root is None:
                raise ValueError("project scope requires a project_root")
            root = project_root / ".opencode" / "skills"
        else:
            raise ValueError(f"unknown scope {scope!r}")
        return SkillTarget(
            harness=self.name, root=root, layout="dir", keep_frontmatter=True,
        )

    def legacy_skill_targets(self):
        # We used to write flat `command/` prompt files, from before OpenCode
        # had skills. Those are user-invoked `/<name>` slash commands only --
        # never model-facing -- and OpenCode still reads them, so they have to
        # go when the real skill lands.
        from ..skills import SkillTarget
        return [SkillTarget(
            harness=self.name, root=_config_home() / "opencode" / "command",
            layout="flat", keep_frontmatter=False, frontmatter_keys=["description"],
        )]

    # ── paths ────────────────────────────────────────────────────────

    def config_path(self, scope: str, project_root: Optional[Path]) -> Path:
        if scope == "user":
            return self._resolve_variant(_config_home() / "opencode" / "opencode.json")
        if scope == "project":
            if project_root is None:
                raise ValueError("project scope requires a project_root")
            return self._resolve_variant(project_root / "opencode.json")
        raise ValueError(f"unknown scope {scope!r}")

    @staticmethod
    def _resolve_variant(json_path: Path) -> Path:
        """Prefer an existing config file. OpenCode reads either ``.json`` or
        ``.jsonc``; if the user already has a ``.jsonc`` we edit that rather
        than create a second file OpenCode may ignore. Absent both, default to
        ``.json``."""
        if json_path.exists():
            return json_path
        jsonc = json_path.with_suffix(".jsonc")
        if jsonc.exists():
            return jsonc
        return json_path

    # ── read / write ─────────────────────────────────────────────────

    @staticmethod
    def _read(path: Path) -> dict:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            hint = ""
            if "//" in text or "/*" in text:
                hint = (
                    " It looks like JSONC with comments, which toolbase can't "
                    "safely rewrite — add the toolbase MCP server under `mcp` "
                    "by hand, or remove the comments and re-run."
                )
            raise OpenCodeConfigError(
                f"{path} is not valid JSON ({e}); refusing to overwrite.{hint}"
            ) from e
        if not isinstance(data, dict):
            raise OpenCodeConfigError(
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
        # OpenCode wants command + args as one array; env is `environment`.
        entry: dict = {
            "type": "local",
            "command": [command, *args],
            "enabled": True,
        }
        if env:
            entry["environment"] = dict(env)
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
        data.setdefault("$schema", _SCHEMA_URL)
        servers = data.get("mcp")
        if servers is None:
            servers = {}
        elif not isinstance(servers, dict):
            raise OpenCodeConfigError(f"{path}: 'mcp' must be a JSON object.")
        servers[server_name] = self._entry(command, args, env)
        data["mcp"] = servers
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
        servers = data.get("mcp")
        if not isinstance(servers, dict) or server_name not in servers:
            return False
        del servers[server_name]
        # Leave an empty mcp object rather than reshaping the file.
        data["mcp"] = servers
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
                except OpenCodeConfigError:
                    data = {}
                servers = data.get("mcp")
                if isinstance(servers, dict) and "toolbase" in servers:
                    entry = servers["toolbase"]
                    if isinstance(entry, dict):
                        present = True
                        cmd = entry.get("command")
                        if isinstance(cmd, list) and cmd:
                            command = str(cmd[0])
                            args = [str(a) for a in cmd[1:]]
            out.append(RegistrationEntry(
                harness=self.name, scope=scope, path=path,
                present=present, command=command, args=args,
            ))
        return out
