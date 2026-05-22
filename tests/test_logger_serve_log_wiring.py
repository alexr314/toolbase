"""Pin the serve_log wiring on the global ToolLogger singleton.

Regression: in 0.5.x, any caller that invoked ``get_logger()`` with no
kwarg (e.g. project-discovery debug logging) before
``toolbase serve`` started would lock the singleton into
``_serve_log_enabled=False`` for the rest of the process, because the
"first caller wins" semantics dropped serve's later
``get_logger(serve_log=True)`` request on the floor. Result:
``~/.toolbase/logs/serve.log`` was never written during serve
sessions, ``toolbase logs`` had nothing to show, and the
``run_serve_e2e.py`` harness self-reported the regression with
"(no serve.log written — serve_log flag wiring may have regressed)".

These tests pin the fix: a late ``get_logger(serve_log=True)``
upgrades an existing instance to mirror events into serve.log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase.logging import logger as logger_mod


@pytest.fixture(autouse=True)
def _reset_logger_singleton(tmp_path: Path, monkeypatch):
    """Reset the module-level singleton + redirect serve.log to tmp."""
    logger_mod._logger = None
    fake_serve_log = tmp_path / "serve.log"
    monkeypatch.setattr(logger_mod, "SERVE_LOG_PATH", fake_serve_log)
    fake_logs_dir = tmp_path  # so the daily-log path stays writable
    monkeypatch.setattr(logger_mod, "LOGS_DIR", fake_logs_dir)
    yield
    logger_mod._logger = None


def test_late_get_logger_with_serve_log_upgrades_existing_instance():
    # 1st caller: no serve_log (mimics _log_project_discovered).
    first = logger_mod.get_logger()
    assert first._serve_log_enabled is False

    # 2nd caller: serve_log=True (mimics orchestrator startup).
    second = logger_mod.get_logger(serve_log=True)
    assert second is first  # singleton, same instance
    assert second._serve_log_enabled is True


def test_late_upgrade_writes_session_marker_to_serve_log():
    logger_mod.get_logger()
    logger_mod.get_logger(serve_log=True)

    contents = logger_mod.SERVE_LOG_PATH.read_text()
    assert "serve session started" in contents


def test_late_upgrade_is_idempotent():
    """Calling get_logger(serve_log=True) twice doesn't double the marker."""
    logger_mod.get_logger()
    logger_mod.get_logger(serve_log=True)
    logger_mod.get_logger(serve_log=True)

    marker_count = logger_mod.SERVE_LOG_PATH.read_text().count(
        "serve session started"
    )
    assert marker_count == 1


def test_event_after_late_upgrade_lands_in_serve_log():
    """Once upgraded, log_event() writes through to serve.log."""
    logger_mod.get_logger()
    log = logger_mod.get_logger(serve_log=True)

    log.log_event(
        event="serve_started",
        toolkit=None,
        message="hello from serve",
        level="info",
    )

    contents = logger_mod.SERVE_LOG_PATH.read_text()
    assert "serve_started" in contents
    assert "hello from serve" in contents


def test_event_without_upgrade_does_not_land_in_serve_log():
    """Counter-test: without the upgrade, events do NOT mirror."""
    log = logger_mod.get_logger()  # no serve_log kwarg
    log.log_event(
        event="some_event",
        toolkit=None,
        message="should not appear",
        level="info",
    )

    # serve.log may or may not exist; if it does, it must not have
    # the event line.
    if logger_mod.SERVE_LOG_PATH.exists():
        assert "should not appear" not in logger_mod.SERVE_LOG_PATH.read_text()


def test_first_caller_with_serve_log_true_works_unchanged():
    """The happy path: serve starts first, no other caller has touched it."""
    log = logger_mod.get_logger(serve_log=True)
    assert log._serve_log_enabled is True
    assert logger_mod.SERVE_LOG_PATH.exists()
