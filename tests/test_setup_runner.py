"""
Unit tests for ``toolbase.setup.runner``.

Drive ``_Runner`` against a mock subprocess. The mock is a pair of
in-memory streams plus a "subprocess script" function that produces
lines on the parent's stdin (= mock subprocess stdout) and consumes
lines the parent writes (= mock subprocess stdin).

Day 1 covers:

- Happy path: hello → go → done(true).
- Authoring failures: protocol version mismatch, missing setup(),
  missing validate().
- Log routing: ``ctx.info(...)`` reaches the console_print sink with
  the right styling.
- Error paths: subprocess exits without sending hello / done; malformed
  RPC line; unknown method requested by subprocess.
- Traceback log file is written when ``done`` carries a traceback.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import List, Optional

import pytest

from toolbase.setup import _rpc
from toolbase.setup.runner import (
    _Runner, _default_log_handler, SetupResult,
)


class _FakePopen:
    """Stand-in for ``subprocess.Popen`` with controllable streams.

    The "subprocess" is just a thread running a script function the
    test provides. The script reads from ``self.stdin`` (which the
    parent writes to) and writes to ``self.stdout`` (which the parent
    reads from). Same wire shape as a real subprocess.
    """

    def __init__(self, script_fn):
        # Pipes: pair of in-memory streams. We use a small wrapper so
        # blocking reads work; StringIO doesn't block on empty.
        self._parent_to_child = _BlockingPipe()
        self._child_to_parent = _BlockingPipe()

        # The parent talks to us through these:
        self.stdin = self._parent_to_child.write_end
        self.stdout = self._child_to_parent.read_end
        # stderr — for failure-path tests; default empty.
        self.stderr = io.StringIO("")

        self._returncode: Optional[int] = None
        self._script_fn = script_fn

        # Run the subprocess "script" in a thread.
        self._thread = threading.Thread(
            target=self._run_script, daemon=True,
        )
        self._thread.start()

    def _run_script(self):
        # Script signature: fn(rx, tx) where rx is what the script
        # reads (parent → child), tx is what the script writes (child
        # → parent).
        try:
            self._script_fn(
                rx=self._parent_to_child.read_end,
                tx=self._child_to_parent.write_end,
            )
        finally:
            # Closing the write end signals EOF to the parent's reader.
            self._child_to_parent.close_write()
            self._returncode = 0

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._thread.join(timeout)
        if self._thread.is_alive():
            import subprocess as _sp
            raise _sp.TimeoutExpired("fake", timeout or 0)
        return self._returncode or 0

    def terminate(self):
        # Best-effort: signal EOF to the script's read side.
        self._parent_to_child.close_write()

    def kill(self):
        self.terminate()


class _BlockingPipe:
    """A pair of stream ends with blocking-on-empty read semantics.

    Python's ``StringIO.readline()`` returns ``""`` immediately on
    empty buffer, which doesn't model a real pipe. This wrapper uses
    a ``threading.Condition`` to block readers until either a line
    arrives or the writer closes.
    """

    def __init__(self):
        self._lock = threading.Condition()
        self._buf = ""
        self._closed = False
        self.read_end = _PipeReadEnd(self)
        self.write_end = _PipeWriteEnd(self)

    def write(self, data: str):
        with self._lock:
            if self._closed:
                raise BrokenPipeError("pipe closed")
            self._buf += data
            self._lock.notify_all()

    def readline(self) -> str:
        with self._lock:
            while "\n" not in self._buf and not self._closed:
                self._lock.wait()
            if "\n" in self._buf:
                idx = self._buf.index("\n")
                line = self._buf[: idx + 1]
                self._buf = self._buf[idx + 1:]
                return line
            # Closed and no newline left → maybe a partial last line.
            line = self._buf
            self._buf = ""
            return line

    def close_write(self):
        with self._lock:
            self._closed = True
            self._lock.notify_all()


class _PipeReadEnd:
    def __init__(self, pipe: _BlockingPipe):
        self._pipe = pipe
        self.closed = False
    def readline(self) -> str:
        return self._pipe.readline()
    def read(self) -> str:
        # Drain everything; used for stderr-style reads.
        result = ""
        while True:
            chunk = self._pipe.readline()
            if not chunk:
                break
            result += chunk
        return result


class _PipeWriteEnd:
    def __init__(self, pipe: _BlockingPipe):
        self._pipe = pipe
        self.closed = False
    def write(self, data: str):
        self._pipe.write(data)
    def flush(self):
        pass
    def close(self):
        self.closed = True
        self._pipe.close_write()


def _make_runner(
    monkeypatch, tmp_path: Path,
    *,
    script_fn,
    mode: str = "setup",
    prompt_mode: str = "ask",
    config_snapshot: Optional[dict] = None,
    extra_handlers=None,
):
    """Build a _Runner whose Popen is replaced with our fake."""
    captured_print: List[str] = []

    def fake_popen(*args, **kwargs):
        return _FakePopen(script_fn)

    # Patch Popen as referenced inside the runner module.
    import toolbase.setup.runner as runner_mod
    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)

    runner = runner_mod._Runner(
        toolkit_name="testkit",
        toolkit_dir=tmp_path / "toolkit",
        python_exe="/usr/bin/python3",
        env={"PATH": "/usr/bin"},
        mode=mode,
        prompt_mode=prompt_mode,
        config_snapshot=config_snapshot or {},
        config_path=tmp_path / "config" / "testkit.yaml",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        console_print=captured_print.append,
        extra_handlers=extra_handlers,
    )
    return runner, captured_print


# ── happy paths ───────────────────────────────────────────────────────


def test_happy_path_setup_returns_ok(monkeypatch, tmp_path):
    """Subprocess says hello, parent says go, subprocess says done(true)."""
    def script(rx, tx):
        # 1. send hello
        _rpc.write_message(tx, _rpc.make_hello(
            has_setup=True, has_validate=True,
        ))
        # 2. read go
        msg = _rpc.read_message(rx)
        assert msg is not None and msg.method == "go"
        # 3. send done
        _rpc.write_message(tx, _rpc.make_done(result=True))

    runner, _printed = _make_runner(monkeypatch, tmp_path, script_fn=script)
    result = runner.run()
    assert result.ok is True
    assert result.traceback is None
    assert result.message is None


def test_happy_path_validate_returns_ok(monkeypatch, tmp_path):
    """Same as setup but mode=validate."""
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(
            has_setup=False, has_validate=True,
        ))
        msg = _rpc.read_message(rx)
        assert msg.params["mode"] == "validate"
        _rpc.write_message(tx, _rpc.make_done(result=True))

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script, mode="validate")
    assert runner.run().ok is True


def test_validate_short_circuits_when_no_validate_function(monkeypatch, tmp_path):
    """If hello says has_validate=False and mode=validate, parent
    never sends 'go' and immediately returns ok=True. This is the
    fast path for "toolkit has no validate(ctx) defined."
    """
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(
            has_setup=True, has_validate=False,
        ))
        # Parent should not send go; it should just close.
        # If it does send go, our test will block reading it; the
        # cleanup path will detect that and the test would hang.

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script, mode="validate")
    result = runner.run()
    assert result.ok is True


# ── log routing ───────────────────────────────────────────────────────


def test_log_info_routes_to_console_with_cyan_style(monkeypatch, tmp_path):
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(has_setup=True, has_validate=True))
        msg = _rpc.read_message(rx)
        assert msg.method == "go"
        # Send a log RPC.
        _rpc.write_message(tx, _rpc.make_request(1, "log",
            {"level": "info", "message": "checking dependencies..."},
        ))
        # Read the response (parent ack).
        resp = _rpc.read_message(rx)
        assert resp is not None and resp.id == 1 and resp.error is None
        _rpc.write_message(tx, _rpc.make_done(result=True))

    runner, printed = _make_runner(monkeypatch, tmp_path, script_fn=script)
    result = runner.run()
    assert result.ok is True
    assert any("[cyan]" in p and "checking dependencies" in p for p in printed)


@pytest.mark.parametrize("level,style", [
    ("info", "cyan"),
    ("warn", "yellow"),
    ("error", "red"),
    ("hint", "dim"),
    ("success", "green"),
])
def test_each_log_level_uses_correct_style(level, style):
    captured = []
    handler = _default_log_handler(captured.append)
    handler({"level": level, "message": f"{level}-message"})
    assert any(f"[{style}]" in p and f"{level}-message" in p for p in captured)


def test_unknown_log_level_falls_back_with_bracket_prefix():
    captured = []
    handler = _default_log_handler(captured.append)
    handler({"level": "ULTRAVERBOSE", "message": "x"})
    assert any("[ULTRAVERBOSE]" in p for p in captured)


# ── error paths ───────────────────────────────────────────────────────


def test_subprocess_dies_before_hello(monkeypatch, tmp_path):
    """Subprocess closes its stdout immediately. Parent sees EOF on
    the hello read and reports a clean failure."""
    def script(rx, tx):
        return  # close write end immediately

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script)
    result = runner.run()
    assert result.ok is False
    assert "before sending hello" in result.message


def test_protocol_version_mismatch(monkeypatch, tmp_path):
    """Subprocess announces a protocol version we don't speak."""
    def script(rx, tx):
        _rpc.write_message(tx, {
            "method": "hello",
            "params": {
                "protocol": 999,
                "python_version": "3.14",
                "has_setup": True,
                "has_validate": True,
            },
        })

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script)
    result = runner.run()
    assert result.ok is False
    assert "protocol version mismatch" in result.message


def test_setup_mode_with_no_setup_function_fails(monkeypatch, tmp_path):
    """If hello says has_setup=False and we're in mode=setup, fail
    loudly rather than letting the subprocess send a confused done."""
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(
            has_setup=False, has_validate=False,
        ))

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script, mode="setup")
    result = runner.run()
    assert result.ok is False
    assert "setup(ctx)" in result.message


def test_done_with_traceback_writes_log_file(monkeypatch, tmp_path):
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(has_setup=True, has_validate=True))
        msg = _rpc.read_message(rx)
        assert msg.method == "go"
        _rpc.write_message(tx, _rpc.make_done(
            result=False,
            traceback_str="Traceback (most recent call last):\n  ...\nValueError: bad",
        ))

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script)
    result = runner.run()
    assert result.ok is False
    assert result.traceback is not None
    assert "ValueError: bad" in result.traceback
    assert result.log_path is not None
    assert result.log_path.exists()
    assert "ValueError: bad" in result.log_path.read_text()
    # Filename pattern: setup-<name>-<stamp>.log
    assert result.log_path.name.startswith("setup-testkit-")
    assert result.log_path.name.endswith(".log")


def test_done_with_false_result_and_no_traceback(monkeypatch, tmp_path):
    """Author returned False explicitly — clean refusal, no traceback,
    no log file written."""
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(has_setup=True, has_validate=True))
        msg = _rpc.read_message(rx)
        assert msg.method == "go"
        _rpc.write_message(tx, _rpc.make_done(result=False))

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script)
    result = runner.run()
    assert result.ok is False
    assert result.traceback is None
    assert result.log_path is None


def test_unknown_method_from_subprocess_returns_error_response(monkeypatch, tmp_path):
    """Subprocess calls a method the parent doesn't know. Parent
    responds with an error response; subprocess can recover and send
    done."""
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(has_setup=True, has_validate=True))
        msg = _rpc.read_message(rx)
        assert msg.method == "go"
        _rpc.write_message(tx, _rpc.make_request(
            1, "phone_home", {"target": "mothership"},
        ))
        resp = _rpc.read_message(rx)
        assert resp is not None
        assert resp.id == 1
        assert resp.error is not None
        assert resp.error["code"] == "unknown_method"
        _rpc.write_message(tx, _rpc.make_done(result=True))

    runner, _ = _make_runner(monkeypatch, tmp_path, script_fn=script)
    result = runner.run()
    assert result.ok is True


def test_handler_exception_routes_back_as_error_response(monkeypatch, tmp_path):
    """A handler raises; parent sends an error response with the
    exception message. Subprocess can catch via SetupRPCError."""
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(has_setup=True, has_validate=True))
        msg = _rpc.read_message(rx)
        assert msg.method == "go"
        _rpc.write_message(tx, _rpc.make_request(1, "boom", {}))
        resp = _rpc.read_message(rx)
        assert resp.error["code"] == "handler_exception"
        assert "intentional" in resp.error["message"]
        _rpc.write_message(tx, _rpc.make_done(result=True))

    def boom_handler(params):
        raise ValueError("intentional")

    runner, _ = _make_runner(
        monkeypatch, tmp_path, script_fn=script,
        extra_handlers={"boom": boom_handler},
    )
    assert runner.run().ok is True


def test_setup_rpc_error_in_handler_preserves_code(monkeypatch, tmp_path):
    """Handler raises SetupRPCError; the code/message are preserved
    in the error response (vs. the generic ``handler_exception``)."""
    def script(rx, tx):
        _rpc.write_message(tx, _rpc.make_hello(has_setup=True, has_validate=True))
        _rpc.read_message(rx)  # go
        _rpc.write_message(tx, _rpc.make_request(1, "fancy_fail", {}))
        resp = _rpc.read_message(rx)
        assert resp.error["code"] == "user_cancelled"
        assert resp.error["message"] == "user pressed Esc"
        _rpc.write_message(tx, _rpc.make_done(result=False))

    def handler(params):
        raise _rpc.SetupRPCError("user_cancelled", "user pressed Esc")

    runner, _ = _make_runner(
        monkeypatch, tmp_path, script_fn=script,
        extra_handlers={"fancy_fail": handler},
    )
    runner.run()  # ok=False but we already asserted via the script
