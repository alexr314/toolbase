"""Sentinel test for per-toolkit stderr → log-file routing.

As of 0.4.1 (the Orchestral 1.4 stdio cleanup), the host writes its
stderr directly to ``~/.toolbase/logs/<toolkit>.log`` via the
``TOOLBASE_HOST_LOG`` env var. Pre-0.4.1, the orchestrator captured
the host's stderr via ``Popen(stderr=PIPE)`` and pumped it to that
same path; with ``MCPClient`` owning the subprocess that lever isn't
available anymore, so the host owns logging.

This test pins the new wiring with a small spawn-a-host check:

1. Set ``TOOLBASE_HOST_LOG`` to a tmp path.
2. Run ``_redirect_stderr_to_log`` as the host's main() does at startup.
3. Write a known string to ``sys.stderr``.
4. Restore the original stderr and confirm the string landed in the
   log file.

If a future change breaks this path — say someone "simplifies" the
redirect or moves it later in main() — this sentinel fails loudly
and the per-toolkit logging regression is caught at unit-test time
rather than at user-debugs-a-mystery time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from toolbase._toolkit_host import _redirect_stderr_to_log


def test_stderr_redirects_to_log_file_when_env_set(tmp_path, monkeypatch):
    log_path = tmp_path / "demo.log"
    monkeypatch.setenv("TOOLBASE_HOST_LOG", str(log_path))

    saved_stderr = sys.stderr
    try:
        _redirect_stderr_to_log()
        sys.stderr.write("HOST_STDERR_SENTINEL line 1\n")
        sys.stderr.write("HOST_STDERR_SENTINEL line 2\n")
        sys.stderr.flush()
    finally:
        # Close the redirected handle and restore.
        try:
            sys.stderr.close()
        except Exception:
            pass
        sys.stderr = saved_stderr

    contents = log_path.read_text(encoding="utf-8")
    assert "HOST_STDERR_SENTINEL line 1" in contents
    assert "HOST_STDERR_SENTINEL line 2" in contents


def test_stderr_unchanged_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("TOOLBASE_HOST_LOG", raising=False)

    saved_stderr = sys.stderr
    try:
        _redirect_stderr_to_log()
        # Should be the inherited stderr, not a file handle pointed
        # at a tmp path.
        assert sys.stderr is saved_stderr
    finally:
        sys.stderr = saved_stderr


def test_stderr_redirect_handles_unwritable_path(tmp_path, monkeypatch):
    """Logging is best-effort — if the path can't be opened, the host
    should keep working with the inherited stderr rather than crashing
    at startup.
    """
    bad_path = tmp_path / "nonexistent-dir" / "child" / "demo.log"
    # Parent doesn't exist; open() will fail.
    monkeypatch.setenv("TOOLBASE_HOST_LOG", str(bad_path))

    saved_stderr = sys.stderr
    try:
        _redirect_stderr_to_log()
        # No-op on failure; stderr remains the inherited one.
        assert sys.stderr is saved_stderr
    finally:
        sys.stderr = saved_stderr
