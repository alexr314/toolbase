"""Codex CLI adapter for ``tb connect``.

Scopes (toolbase -> Codex):
- ``user``    -> ``~/.codex/config.toml``, ``[mcp_servers.toolbase]`` (all projects)
- ``project`` -> ``<root>/.codex/config.toml``, ``[mcp_servers.toolbase]``
  (git-tracked, team-shared; Codex loads it only for *trusted* projects)

The entry is a stdio MCP server::

    [mcp_servers.toolbase]
    command = "toolbase"
    args = ["serve"]

Writes are a non-destructive, comment-preserving round-trip via ``tomlkit``
(only the toolbase entry is touched; every other server, top-level key, and
comment is preserved) and atomic (tmp file + rename). Malformed existing TOML
is refused rather than clobbered.

(The stdlib only *reads* TOML via ``tomllib``; ``tomlkit`` is the writer, the
same role ``ruamel.yaml`` plays for toolbase's YAML.)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from .base import (
    AvailabilityStatus, HarnessAdapter, HarnessConfigError, RegistrationEntry,
)

_SERVERS_TABLE = "mcp_servers"


class CodexConfigError(HarnessConfigError):
    """Existing Codex config is unreadable / malformed."""


class CodexAdapter(HarnessAdapter):
    name = "codex"

    def project_scope_note(self) -> str:
        return (
            "Codex loads a project's .codex/config.toml only after you trust "
            "the project (run `codex` in the repo and approve it once)."
        )

    # ── detection ────────────────────────────────────────────────────

    def is_available(self) -> AvailabilityStatus:
        if shutil.which("codex"):
            return AvailabilityStatus(True, "codex CLI found on PATH")
        if (Path.home() / ".codex").exists():
            return AvailabilityStatus(True, "found ~/.codex")
        return AvailabilityStatus(
            False, "no `codex` CLI on PATH and no ~/.codex"
        )

    def supported_scopes(self) -> Dict[str, str]:
        return {"user": "user", "project": "project"}

    # ── paths ────────────────────────────────────────────────────────

    def config_path(self, scope: str, project_root: Optional[Path]) -> Path:
        if scope == "user":
            return Path.home() / ".codex" / "config.toml"
        if scope == "project":
            if project_root is None:
                raise ValueError("project scope requires a project_root")
            return project_root / ".codex" / "config.toml"
        raise ValueError(f"unknown scope {scope!r}")

    # ── read / write ─────────────────────────────────────────────────

    @staticmethod
    def _parse(path: Path):
        """Parse the TOML file into a tomlkit document (preserving comments).

        Returns an empty document if the file is absent. Refuses malformed
        TOML rather than clobbering it.
        """
        import tomlkit
        from tomlkit.exceptions import TOMLKitError

        if not path.exists():
            return tomlkit.document()
        try:
            return tomlkit.parse(path.read_text(encoding="utf-8"))
        except TOMLKitError as e:
            raise CodexConfigError(
                f"{path} is not valid TOML ({e}); refusing to overwrite. "
                "Fix or remove it, then re-run."
            ) from e

    @staticmethod
    def _write_atomic(path: Path, doc) -> None:
        import tomlkit

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _entry(command: str, args: List[str], env: Optional[Dict[str, str]]):
        import tomlkit

        entry = tomlkit.table()
        entry["command"] = command
        entry["args"] = list(args)
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
        import tomlkit

        path = self.config_path(scope, project_root)
        doc = self._parse(path)
        servers = doc.get(_SERVERS_TABLE)
        if servers is None:
            # Super-table so it renders as `[mcp_servers.toolbase]`, not an
            # empty `[mcp_servers]` header followed by the sub-table.
            servers = tomlkit.table(is_super_table=True)
            doc[_SERVERS_TABLE] = servers
        servers[server_name] = self._entry(command, args, env)
        if not dry_run:
            self._write_atomic(path, doc)
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
        doc = self._parse(path)
        servers = doc.get(_SERVERS_TABLE)
        if servers is None or server_name not in servers:
            return False
        del servers[server_name]
        # Leave an empty mcp_servers table rather than reshaping the file.
        self._write_atomic(path, doc)
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
                    doc = self._parse(path)
                except CodexConfigError:
                    doc = None
                if doc is not None:
                    servers = doc.get(_SERVERS_TABLE)
                    if servers is not None and "toolbase" in servers:
                        entry = servers["toolbase"]
                        present = True
                        command = str(entry.get("command", ""))
                        raw_args = entry.get("args")
                        args = (
                            [str(a) for a in raw_args]
                            if raw_args is not None else None
                        )
            out.append(RegistrationEntry(
                harness=self.name, scope=scope, path=path,
                present=present, command=command, args=args,
            ))
        return out
