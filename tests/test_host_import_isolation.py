"""Regression: the per-toolkit host must not let the toolkit dir shadow
installed packages -- and that isolation must NOT leak into the child
subprocesses a toolkit spawns.

The host is spawned as ``python -P -m toolbase._toolkit_host`` with cwd at the
toolkit dir. Without isolation, ``-m`` prepends cwd to ``sys.path``, so a
toolkit that ships a top-level dir named like an installed package (the
scaffold's ``mcp/`` is the canonical trap) shadows it -- ``import mcp``
resolves to the toolkit's ``mcp/`` instead of the MCP SDK, ``orchestral.mcp``
fails to import, and the toolkit is silently skipped at serve.

Isolation is enforced with the ``-P`` interpreter flag (Python 3.11+), NOT a
``PYTHONSAFEPATH`` env var. Both drop the implicit cwd/script-dir entry for the
host, but the env var is inherited by every child subprocess the toolkit
spawns -- which breaks external tools that rely on the implicit script-dir
sys.path (e.g. MadGraph's ``write_param_card.py`` doing ``from parameters
import ...``). The flag isolates only the host interpreter. These tests pin
both: the host is isolated, and the isolation does not leak to children.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from toolbase.serve.orchestrator import (
    ToolkitDiscovery,
    _build_host_command,
    _build_host_env,
    _strip_caller_env_activation,
)


def _venv_disc(tmp_path: Path) -> ToolkitDiscovery:
    return ToolkitDiscovery(
        name="demo",
        path=tmp_path,
        meta={"environment": "venv", "python_path": sys.executable},
    )


def test_host_command_uses_dash_P_flag(tmp_path: Path):
    """The host argv isolates via ``-P``, placed before ``-m`` so it applies
    to the toolkit-host interpreter itself."""
    argv = _build_host_command(_venv_disc(tmp_path))
    assert "-P" in argv, argv
    assert argv.index("-P") < argv.index("-m"), argv


def test_build_host_env_does_not_leak_pythonsafepath(tmp_path: Path):
    """The env handed to the host (and inherited by every subprocess it
    spawns) must NOT carry PYTHONSAFEPATH -- isolation is the host's ``-P``
    flag, which does not propagate to children."""
    env = _build_host_env(tmp_path, "demo")
    assert "PYTHONSAFEPATH" not in env


def test_build_host_env_keeps_explicit_pythonpath(tmp_path: Path):
    # The explicit PYTHONPATH that makes ``toolbase`` importable from the
    # toolkit's interpreter must still be present.
    env = _build_host_env(tmp_path, "demo")
    assert env.get("PYTHONPATH")


def test_dash_P_isolates_host_but_not_children(tmp_path: Path):
    """``-P`` drops cwd from the host's own sys.path (isolation preserved) but,
    unlike PYTHONSAFEPATH, is not inherited -- a child subprocess still resolves
    modules from its cwd."""
    (tmp_path / "shadow_probe.py").write_text("VALUE = 1\n", encoding="utf-8")
    base_env = {k: v for k, v in os.environ.items() if k != "PYTHONSAFEPATH"}

    # Baseline: a plain interpreter imports the cwd module fine.
    unguarded = subprocess.run(
        [sys.executable, "-c", "import shadow_probe"],
        cwd=tmp_path, env=base_env,
    )
    assert unguarded.returncode == 0

    # Host with -P: cwd is dropped, so the host itself cannot import it.
    host = subprocess.run(
        [sys.executable, "-P", "-c", "import shadow_probe"],
        cwd=tmp_path, env=base_env,
    )
    assert host.returncode != 0

    # A child spawned by a -P parent CAN import from cwd: the flag did not leak.
    child_from_guarded_parent = subprocess.run(
        [sys.executable, "-P", "-c",
         "import subprocess, sys; "
         "sys.exit(subprocess.run([sys.executable, '-c', 'import shadow_probe']).returncode)"],
        cwd=tmp_path, env=base_env,
    )
    assert child_from_guarded_parent.returncode == 0


# --- Caller conda/venv activation isolation -------------------------------- #
# _build_host_env must not leak the CALLER's activated-env pollution
# (PYTHIA8DATA, ROOTSYS, compiler flags, ...) into the toolkit host, or a
# toolkit's own bundled software gets pointed at the caller's install.


def test_strip_removes_vars_bound_to_active_prefix(tmp_path: Path):
    prefix = str(tmp_path / "envs" / "caller")
    env = {
        "CONDA_PREFIX": prefix,
        "PYTHIA8DATA": prefix + "/share/Pythia8/xmldoc",   # data path -> strip
        "ROOTSYS": prefix,                                  # -> strip
        "CFLAGS": f"-isystem {prefix}/include",             # build flag -> strip
        "SOME_API_KEY": "sk-abc",                           # unrelated -> keep
    }
    out = _strip_caller_env_activation(dict(env))
    assert "PYTHIA8DATA" not in out
    assert "ROOTSYS" not in out
    assert "CFLAGS" not in out
    assert out.get("SOME_API_KEY") == "sk-abc"


def test_strip_keeps_path_family_and_conda_bookkeeping(tmp_path: Path):
    prefix = str(tmp_path / "envs" / "caller")
    env = {
        "CONDA_PREFIX": prefix,
        "PATH": f"{prefix}/bin:/usr/bin",           # search path -> keep
        "LD_LIBRARY_PATH": f"{prefix}/lib",         # loader path -> keep
        "CONDA_DEFAULT_ENV": "caller",              # CONDA_* bookkeeping -> keep
    }
    out = _strip_caller_env_activation(dict(env))
    assert out["PATH"] == f"{prefix}/bin:/usr/bin"
    assert out["LD_LIBRARY_PATH"] == f"{prefix}/lib"
    assert out["CONDA_DEFAULT_ENV"] == "caller"


def test_strip_is_noop_without_active_env():
    env = {"PYTHIA8DATA": "/somewhere/xmldoc", "PATH": "/usr/bin"}
    out = _strip_caller_env_activation(dict(env))
    assert out == env  # no CONDA_PREFIX/VIRTUAL_ENV -> nothing to attribute


def test_strip_honors_virtualenv_prefix(tmp_path: Path):
    venv = str(tmp_path / ".venv")
    env = {"VIRTUAL_ENV": venv, "PYTHIA8DATA": venv + "/share/Pythia8/xmldoc"}
    out = _strip_caller_env_activation(dict(env))
    assert "PYTHIA8DATA" not in out


def test_build_host_env_strips_active_prefix_pollution(tmp_path: Path, monkeypatch):
    prefix = str(tmp_path / "envs" / "caller")
    monkeypatch.setenv("CONDA_PREFIX", prefix)
    monkeypatch.setenv("PYTHIA8DATA", prefix + "/share/Pythia8/xmldoc")
    env = _build_host_env(tmp_path, "demo")
    assert "PYTHIA8DATA" not in env
    # toolbase's own PYTHONPATH injection still happens after the strip.
    assert env.get("PYTHONPATH")


def test_build_host_env_prepends_venv_bin_to_path(tmp_path: Path):
    venv_bin = str(tmp_path / ".venv" / "bin")
    env = _build_host_env(tmp_path, "demo", venv_bin=venv_bin)
    assert env["PATH"].split(os.pathsep)[0] == venv_bin


def test_build_host_env_no_venv_bin_leaves_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = _build_host_env(tmp_path, "demo")   # venv_bin defaults to None
    assert env["PATH"] == "/usr/bin:/bin"
