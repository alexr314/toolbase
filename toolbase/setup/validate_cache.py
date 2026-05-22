"""
Mtime-keyed cache for ``validate(ctx)`` results.

Per the spec: ``validate(ctx)`` runs at every ``toolbase serve``
startup with a 100ms latency budget. Spawning a subprocess every time
breaks that budget for any non-trivial venv (interpreter startup +
import toolbase slice + import setup.py + invoke validate ≈ 200-
500ms even on warm caches).

Cache strategy (per the manager's Day 5 sign-off):

- Cache **both** successful and failed validates.
- Key: ``(toolkit_name, config_file_mtime, setup_py_mtime)``.
- Value: ``{"result": bool, "message": str | null,
            "config_mtime": int, "setup_py_mtime": int}``.
- Storage: one JSON file at
  ``~/.toolbase/cache/_setup_validate.json`` — keyed by toolkit
  name, all toolkits in one file.

Invalidation: any change to the config file or to ``setup.py``
makes the cache key change, which is what we want — running
``toolbase setup`` (which writes config) automatically
invalidates the cached validate result.

The cache is small and human-readable. Users can ``rm`` it safely;
we recreate from scratch on next access.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


CACHE_FILE_NAME = "_setup_validate.json"


class ValidateCache:
    """Light wrapper around the on-disk cache JSON.

    Intentionally not a Cache class with sophisticated eviction —
    it's per-toolkit and keyed by mtimes, so size is bounded by the
    number of toolkits the user has installed (small) and stale
    entries are functionally invisible (mismatch on next mtime read).

    Concurrent-write safety: best-effort. The cache is small enough
    that an occasional dropped entry is fine; the next validate will
    just re-run and write fresh. Atomic writes via tmp+replace
    minimize corruption windows.
    """

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                return {}
            return data
        except (json.JSONDecodeError, OSError):
            # Corrupted cache → start fresh. The user's serve startup
            # pays one extra subprocess spawn; not worth crashing for.
            return {}

    def _save(self, data: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, self.path)
        except OSError:
            # Cache write failed (read-only filesystem? full disk?).
            # Fail silent: validate still ran correctly, we just don't
            # remember the result. Better than blocking on serve startup.
            pass

    def get(
        self,
        toolkit_name: str,
        *,
        config_mtime: Optional[int],
        setup_py_mtime: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Return cached entry if mtimes match; None otherwise.

        ``None`` mtimes mean the file doesn't exist; that's a valid
        cache key as long as it's stable across calls.
        """
        all_data = self._load()
        entry = all_data.get(toolkit_name)
        if not entry:
            return None
        if entry.get("config_mtime") != config_mtime:
            return None
        if entry.get("setup_py_mtime") != setup_py_mtime:
            return None
        return entry

    def put(
        self,
        toolkit_name: str,
        *,
        result: bool,
        message: Optional[str],
        config_mtime: Optional[int],
        setup_py_mtime: Optional[int],
    ) -> None:
        all_data = self._load()
        all_data[toolkit_name] = {
            "result": bool(result),
            "message": message,
            "config_mtime": config_mtime,
            "setup_py_mtime": setup_py_mtime,
            "cached_at": int(time.time()),
        }
        self._save(all_data)

    def evict(self, toolkit_name: str) -> None:
        all_data = self._load()
        if toolkit_name in all_data:
            del all_data[toolkit_name]
            self._save(all_data)

    def clear(self) -> None:
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass


def _mtime_or_none(path: Path) -> Optional[int]:
    """Best-effort mtime read. Returns int seconds or None."""
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return None


def default_cache_path() -> Path:
    """Default cache location: ``~/.toolbase/cache/_setup_validate.json``.

    Resolves at call time (not import time) so test fixtures that
    patch HOME pick up the new path.
    """
    return Path.home() / ".toolbase" / "cache" / CACHE_FILE_NAME
