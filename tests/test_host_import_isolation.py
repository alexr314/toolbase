"""Regression: the per-toolkit host must not let the toolkit dir shadow
installed packages.

The host is spawned as ``python -m toolbase._toolkit_host`` with cwd at the
toolkit dir, and ``-m`` prepends cwd to ``sys.path``. A toolkit that ships a
top-level dir named like an installed package (the scaffold's ``mcp/`` is the
canonical trap) would then shadow it -- ``import mcp`` resolves to the
toolkit's ``mcp/`` instead of the MCP SDK, and ``orchestral.mcp`` fails to
import, so the toolkit is silently skipped at serve.

``_build_host_env`` sets ``PYTHONSAFEPATH=1`` to suppress the implicit cwd
entry. These tests pin that, plus the underlying interpreter behavior.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from toolbase.serve.orchestrator import _build_host_env


def test_build_host_env_sets_pythonsafepath(tmp_path: Path):
    env = _build_host_env(tmp_path, "demo")
    assert env.get("PYTHONSAFEPATH") == "1"


def test_build_host_env_keeps_explicit_pythonpath(tmp_path: Path):
    # PYTHONSAFEPATH must not wipe the explicit PYTHONPATH that makes
    # ``toolbase`` importable from the toolkit's interpreter.
    env = _build_host_env(tmp_path, "demo")
    assert env.get("PYTHONPATH")


def test_pythonsafepath_keeps_cwd_off_syspath(tmp_path: Path):
    """The interpreter behavior the fix relies on: with PYTHONSAFEPATH set,
    a module sitting in cwd is no longer importable via the implicit path
    entry."""
    (tmp_path / "shadow_probe.py").write_text("VALUE = 1\n", encoding="utf-8")
    base_env = {k: v for k, v in os.environ.items() if k != "PYTHONSAFEPATH"}

    unguarded = subprocess.run(
        [sys.executable, "-c", "import shadow_probe"],
        cwd=tmp_path, env=base_env,
    )
    assert unguarded.returncode == 0  # cwd is on sys.path without the guard

    guarded = subprocess.run(
        [sys.executable, "-c", "import shadow_probe"],
        cwd=tmp_path, env={**base_env, "PYTHONSAFEPATH": "1"},
    )
    assert guarded.returncode != 0  # cwd dropped from sys.path -> ImportError
