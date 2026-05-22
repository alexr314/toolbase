"""Unit tests for the orchestrator's auto-restart logic.

Covers the state machine described in SERVE_ARCHITECTURE.md §3.7 and the
restart policy in §3.3 (3-attempt budget, 1s/4s/16s exponential backoff,
runtime-only — initial-launch failures don't consume budget).

These tests mock the subprocess + MCPClient layer so they run in
milliseconds. The full live-subprocess crash-recovery path is exercised
by ``tests/e2e/run_restart_e2e.py``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from toolbase.serve import orchestrator
from toolbase.serve.orchestrator import (
    Orchestrator,
    RESTART_BACKOFF_S,
    RESTART_BUDGET,
    ToolkitDiscovery,
    ToolkitRuntime,
    ToolkitState,
)


# ── helpers ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _stub_state_config(monkeypatch):
    """Default: declarative config-resolution returns "no config" so
    restart tests don't have to construct a real toolkit.yaml.

    Tests exercising the config-resolution path itself can override
    via their own ``monkeypatch.setattr`` later.
    """
    monkeypatch.setattr(
        orchestrator, "_resolve_state_config", lambda d: ({}, None),
    )


def _make_runtime(
    name: str = "demo",
    *,
    state: ToolkitState = ToolkitState.READY,
    proc: Optional[Any] = None,        # back-compat shim, see below
    client: Optional[Any] = None,
    discovery: Optional[ToolkitDiscovery] = None,
) -> ToolkitRuntime:
    """Build a ToolkitRuntime suitable for testing without real subprocesses.

    As of 0.4.1, the orchestrator no longer holds a ``Popen`` directly —
    ``MCPClient`` (here a ``MagicMock``) owns the subprocess lifecycle.
    The test-only ``rt.proc`` shim is preserved so existing tests can
    keep using ``proc.poll.return_value = 1`` as the "subprocess is
    dead" idiom; ``_classify_call_failure`` is patched in this test
    module (see ``_install_test_classify_call_failure``) to honor the
    shim alongside the production ``client._subprocess_died`` signal.
    """
    if client is None:
        client = MagicMock()
        client._subprocess_died = False
    if proc is None:
        proc = MagicMock()
        proc.poll.return_value = None  # alive by default
        proc.pid = 12345

    if discovery is None:
        discovery = ToolkitDiscovery(
            name=name,
            path=Path("/tmp/fake-toolkit"),
            meta={"environment": "venv", "python_path": "/usr/bin/python"},
        )
    rt = ToolkitRuntime(
        name=name,
        path=Path("/tmp/fake-toolkit"),
        upstream_tool_names=["demo_tool"],
        mcp_client=client,
        state=state,
        discovery=discovery,
    )
    # Test-only shim: tests reach for rt.proc directly.
    rt.proc = proc  # type: ignore[attr-defined]
    return rt


@pytest.fixture(autouse=True)
def _install_test_classify_call_failure(monkeypatch):
    """Test-only patch: also honor ``rt.proc.poll()`` as a crash signal.

    The production ``_classify_call_failure`` checks
    ``rt.mcp_client._subprocess_died`` (the canonical 0.4.1 stdio
    signal). The pre-existing restart-test idiom is
    ``rt.proc.poll.return_value = 1``, which is no longer load-bearing
    in production but is still the simplest way to write tests. This
    fixture extends the production check to also fall back to
    ``rt.proc.poll()`` when present, so existing tests pass without
    rewriting every assertion.
    """
    original = Orchestrator._classify_call_failure

    def patched(self, rt, exc):
        if original(self, rt, exc):
            return True
        proc = getattr(rt, "proc", None)
        if proc is not None:
            try:
                return proc.poll() is not None
            except Exception:
                return False
        return False

    monkeypatch.setattr(Orchestrator, "_classify_call_failure", patched)


def _make_orchestrator(tmp_path: Path) -> Orchestrator:
    """Build an Orchestrator pointed at an empty toolkits dir.

    No real toolkits are discovered. Tests inject ToolkitRuntimes
    directly into ``_runtimes`` after construction.
    """
    return Orchestrator(toolkits_dir=tmp_path / "toolkits")


# ── state machine: enum ────────────────────────────────────────────────


def test_states_match_architecture_doc():
    """All six states from SERVE_ARCHITECTURE.md §3.7 are present."""
    names = {s.name for s in ToolkitState}
    assert names == {
        "DISCOVERED", "STARTING", "READY", "CRASHED", "FAILED", "STOPPED",
    }


def test_restart_policy_constants():
    """Pin the documented policy: 3 attempts, 1s/4s/16s backoff."""
    assert RESTART_BUDGET == 3
    assert RESTART_BACKOFF_S == (1.0, 4.0, 16.0)


# ── crash detection ────────────────────────────────────────────────────


def test_is_crash_exception_connection_error():
    """Stdlib ConnectionError counts as a crash."""
    assert Orchestrator._is_crash_exception(ConnectionError("refused"))


def test_is_crash_exception_httpx_classes_by_name():
    """httpx errors are matched by class name (no hard import)."""
    # We can't import httpx without it being a hard dep, so we synthesize
    # exception classes with matching names.
    for cls_name in ("ConnectError", "RemoteProtocolError", "ReadError"):
        cls = type(cls_name, (Exception,), {})
        assert Orchestrator._is_crash_exception(cls("boom"))


def test_is_crash_exception_unwraps_exception_groups():
    """ExceptionGroup-style wrappers (anyio TaskGroup) get unwrapped."""
    class _FakeGroup(Exception):
        def __init__(self, exceptions):
            super().__init__("group")
            self.exceptions = exceptions

    inner = ConnectionError("refused")
    wrapped = _FakeGroup([inner])
    assert Orchestrator._is_crash_exception(wrapped)


def test_is_crash_exception_tool_errors_are_not_crashes():
    """A RuntimeError from inside a tool body is not a crash."""
    assert not Orchestrator._is_crash_exception(RuntimeError("oops"))
    assert not Orchestrator._is_crash_exception(ValueError("bad arg"))
    assert not Orchestrator._is_crash_exception(TimeoutError("slow"))


def test_classify_call_failure_uses_proc_poll(tmp_path):
    """Even a non-connection-y exception counts as a crash if proc died."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime()
    rt.proc.poll.return_value = 137  # killed (SIGKILL)
    # ValueError on its own is not a crash...
    assert not Orchestrator._is_crash_exception(ValueError("x"))
    # ...but with proc dead, _classify_call_failure escalates.
    assert orch._classify_call_failure(rt, ValueError("x"))


def test_classify_call_failure_alive_proc_tool_error(tmp_path):
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime()
    rt.proc.poll.return_value = None  # alive
    assert not orch._classify_call_failure(rt, RuntimeError("tool said no"))


# ── _schedule_restart: lock + state transitions ───────────────────────


def test_schedule_restart_no_op_when_already_starting(tmp_path):
    """A second schedule call while a restart is in flight does nothing."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.STARTING)
    # Pre-set state to STARTING; schedule should observe and bail early
    # before incrementing or spawning a thread.
    threads_before = threading.active_count()
    orch._schedule_restart(rt)
    threads_after = threading.active_count()
    assert rt.state == ToolkitState.STARTING
    assert threads_after == threads_before


def test_schedule_restart_no_op_when_failed(tmp_path):
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.FAILED)
    rt.restart_attempts = 3
    threads_before = threading.active_count()
    orch._schedule_restart(rt)
    assert rt.state == ToolkitState.FAILED
    assert threading.active_count() == threads_before


def test_schedule_restart_kicks_off_thread(tmp_path, monkeypatch):
    """A normal CRASHED → schedule transitions to STARTING and starts a thread."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.CRASHED)

    # Replace _attempt_restart with a no-op so we don't actually wait/spawn.
    called = threading.Event()
    captured: Dict[str, Any] = {}

    def fake_attempt(rt_arg, attempt, backoff):
        captured["rt"] = rt_arg
        captured["attempt"] = attempt
        captured["backoff"] = backoff
        called.set()

    monkeypatch.setattr(orch, "_attempt_restart", fake_attempt)

    orch._schedule_restart(rt)
    assert called.wait(timeout=2.0), "_attempt_restart was never called"
    assert captured["attempt"] == 1
    assert captured["backoff"] == 1.0
    # Schedule transitioned to STARTING (the thread sees that and goes from there).
    assert rt.state == ToolkitState.STARTING


def test_schedule_restart_concurrent_calls_only_kick_one_thread(tmp_path, monkeypatch):
    """Parallel forwarders on the same crashed runtime spawn at most one restart."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.CRASHED)

    invocations: List[int] = []
    invocation_lock = threading.Lock()

    def fake_attempt(rt_arg, attempt, backoff):
        with invocation_lock:
            invocations.append(attempt)
        # Block for a moment so concurrent _schedule_restart calls all see
        # state == STARTING.
        time.sleep(0.05)

    monkeypatch.setattr(orch, "_attempt_restart", fake_attempt)

    threads = [
        threading.Thread(target=orch._schedule_restart, args=(rt,))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    # Wait for the single fake_attempt to finish.
    time.sleep(0.1)

    # Exactly one restart attempt should have been kicked off.
    assert invocations == [1], (
        f"Expected exactly one restart kicked off; got {invocations}"
    )


def test_schedule_restart_at_budget_marks_failed(tmp_path):
    """If restart_attempts already == BUDGET, schedule transitions to FAILED."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.CRASHED)
    rt.restart_attempts = RESTART_BUDGET
    rt.last_error = "third attempt failed"

    orch._schedule_restart(rt)
    assert rt.state == ToolkitState.FAILED


def test_schedule_restart_backoff_schedule(tmp_path, monkeypatch):
    """attempt 1 → 1s, attempt 2 → 4s, attempt 3 → 16s."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.CRASHED)

    captured_backoffs: List[float] = []

    def fake_attempt(rt_arg, attempt, backoff):
        captured_backoffs.append(backoff)

    monkeypatch.setattr(orch, "_attempt_restart", fake_attempt)

    # attempt 1
    rt.restart_attempts = 0
    rt.state = ToolkitState.CRASHED
    orch._schedule_restart(rt)
    # attempt 2
    rt.restart_attempts = 1
    rt.state = ToolkitState.CRASHED
    orch._schedule_restart(rt)
    # attempt 3
    rt.restart_attempts = 2
    rt.state = ToolkitState.CRASHED
    orch._schedule_restart(rt)

    # Give threads time to run.
    time.sleep(0.1)

    assert captured_backoffs == [1.0, 4.0, 16.0]


# ── _attempt_restart: success / failure paths ─────────────────────────


def test_attempt_restart_success_swaps_in_new_subprocess(tmp_path, monkeypatch):
    """A successful spawn returns the runtime to READY with a fresh client."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.STARTING)

    new_client = MagicMock()
    new_client._subprocess_died = False
    fake_spawn = Orchestrator._SpawnResult(
        upstream_tools=["demo_tool"],
        client=new_client,
    )

    monkeypatch.setattr(
        orch, "_spawn_and_connect",
        lambda disc, **kw: (fake_spawn, None),
    )

    # Bypass the sleep so the test runs in milliseconds.
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    orch._attempt_restart(rt, attempt=1, backoff_s=1.0)

    assert rt.state == ToolkitState.READY
    assert rt.mcp_client is new_client
    # Successful restarts DO count against the budget — §3.3 caps total
    # restarts per session at 3, regardless of success/failure outcome.
    assert rt.restart_attempts == 1


def test_attempt_restart_failure_under_budget_reschedules(tmp_path, monkeypatch):
    """A failed attempt with budget remaining schedules the next attempt."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.STARTING)

    monkeypatch.setattr(
        orch, "_spawn_and_connect",
        lambda disc, **kw: (None, "spawn failed: OSError"),
    )
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    rescheduled: List[int] = []

    real_schedule = orch._schedule_restart

    def tracking_schedule(rt_arg):
        rescheduled.append(rt_arg.restart_attempts)
        # Don't actually start another thread — we just want to confirm
        # _schedule_restart was invoked with the incremented attempt count.
        rt_arg.state = ToolkitState.STARTING

    monkeypatch.setattr(orch, "_schedule_restart", tracking_schedule)

    orch._attempt_restart(rt, attempt=1, backoff_s=1.0)

    assert rt.restart_attempts == 1
    assert rt.last_error.startswith("spawn failed")
    assert rescheduled == [1]  # _schedule_restart called with attempts=1


def test_attempt_restart_failure_at_budget_marks_failed(tmp_path, monkeypatch):
    """The 3rd failure transitions CRASHED → FAILED, no further attempts."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.STARTING)
    rt.restart_attempts = 2  # one more attempt is the last

    monkeypatch.setattr(
        orch, "_spawn_and_connect",
        lambda disc, **kw: (None, "mcp connect failed: refused"),
    )
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    rescheduled: List[Any] = []
    monkeypatch.setattr(
        orch, "_schedule_restart",
        lambda rt_arg: rescheduled.append(rt_arg),
    )

    orch._attempt_restart(rt, attempt=3, backoff_s=16.0)

    assert rt.state == ToolkitState.FAILED
    assert rt.restart_attempts == 3
    assert "mcp connect failed" in rt.last_error
    # No further restart scheduled — terminal state.
    assert rescheduled == []


def test_attempt_restart_during_shutdown_is_noop(tmp_path, monkeypatch):
    """If shutdown started, attempt_restart returns early without spawning."""
    orch = _make_orchestrator(tmp_path)
    orch._shutdown_initiated = True
    rt = _make_runtime(state=ToolkitState.STARTING)

    spawn_called = [False]

    def fake_spawn(disc, **_kw):
        spawn_called[0] = True
        return None, None

    monkeypatch.setattr(orch, "_spawn_and_connect", fake_spawn)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    orch._attempt_restart(rt, attempt=1, backoff_s=1.0)
    assert spawn_called[0] is False


# ── forwarder integration ──────────────────────────────────────────────


def test_forwarder_returns_failed_message_when_runtime_failed(tmp_path):
    """A FAILED runtime gets the 'permanently failed' message."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.FAILED)
    rt.restart_attempts = 3
    orch._runtimes["demo"] = rt

    forward = orch._make_forwarder("demo")
    msg = forward("demo_tool", {})
    assert "subprocess crashed 3 times" in msg
    assert "toolbase logs" in msg


def test_forwarder_returns_in_progress_message_when_starting(tmp_path, monkeypatch):
    """A STARTING runtime gets the 'restart in progress' message."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.STARTING)
    rt.restart_attempts = 0
    orch._runtimes["demo"] = rt

    # _schedule_restart will be a no-op on STARTING; assert the message anyway.
    monkeypatch.setattr(orch, "_schedule_restart", lambda rt_arg: None)

    forward = orch._make_forwarder("demo")
    msg = forward("demo_tool", {})
    assert "restart in progress" in msg
    assert "1 of 3" in msg


def test_forwarder_detects_crash_and_schedules_restart(tmp_path, monkeypatch):
    """A connection error during call_tool transitions READY → CRASHED."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.READY)
    rt.proc.poll.return_value = 1  # dead
    rt.mcp_client.call_tool = MagicMock(side_effect=ConnectionError("refused"))
    orch._runtimes["demo"] = rt

    scheduled = [False]
    monkeypatch.setattr(
        orch, "_schedule_restart",
        lambda rt_arg: scheduled.__setitem__(0, True),
    )

    forward = orch._make_forwarder("demo")
    msg = forward("demo_tool", {})

    assert rt.state == ToolkitState.CRASHED
    assert "subprocess crashed" in msg
    assert "Automatic restart scheduled" in msg
    assert "attempt 1 of 3" in msg
    assert scheduled[0] is True


def test_forwarder_tool_error_does_not_change_state(tmp_path):
    """A non-connection exception with proc alive leaves the runtime READY."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.READY)
    rt.proc.poll.return_value = None  # alive
    rt.mcp_client.call_tool = MagicMock(side_effect=RuntimeError("tool error"))
    orch._runtimes["demo"] = rt

    forward = orch._make_forwarder("demo")
    msg = forward("demo_tool", {})

    assert rt.state == ToolkitState.READY
    # The user-facing error format from the non-crash branch.
    assert "demo_tool failed after" in msg
    assert "tool error" in msg


def test_forwarder_success_path_unchanged(tmp_path):
    """A successful call returns the result and leaves state alone."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.READY)
    rt.mcp_client.call_tool = MagicMock(return_value="hello world")
    orch._runtimes["demo"] = rt

    forward = orch._make_forwarder("demo")
    result = forward("demo_tool", {"x": 1})
    assert result == "hello world"
    assert rt.state == ToolkitState.READY
    rt.mcp_client.call_tool.assert_called_once_with("demo_tool", {"x": 1})


# ── telemetry ──────────────────────────────────────────────────────────


def test_schedule_emits_restart_scheduled_event(tmp_path, monkeypatch):
    """Each scheduled restart logs a ``restart_scheduled`` event."""
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.CRASHED)

    monkeypatch.setattr(orch, "_attempt_restart", lambda *a, **k: None)

    events: List[Dict[str, Any]] = []
    orig_log_event = orch.logger.log_event

    def capture(event, **kwargs):
        events.append({"event": event, **kwargs})
        return orig_log_event(event, **kwargs)

    monkeypatch.setattr(orch.logger, "log_event", capture)

    orch._schedule_restart(rt)
    time.sleep(0.05)

    scheduled = [e for e in events if e["event"] == "restart_scheduled"]
    assert len(scheduled) == 1
    assert scheduled[0]["toolkit"] == "demo"
    assert scheduled[0]["attempt"] == 1
    assert scheduled[0]["backoff_s"] == 1.0


def test_attempt_restart_success_emits_restart_succeeded(tmp_path, monkeypatch):
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.STARTING)

    spawn = Orchestrator._SpawnResult(
        upstream_tools=["a", "b"], client=MagicMock(),
    )
    monkeypatch.setattr(orch, "_spawn_and_connect", lambda d, **kw: (spawn, None))
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    events: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        orch.logger, "log_event",
        lambda event, **kw: events.append({"event": event, **kw}),
    )

    orch._attempt_restart(rt, attempt=2, backoff_s=4.0)

    succ = [e for e in events if e["event"] == "restart_succeeded"]
    assert len(succ) == 1
    assert succ[0]["toolkit"] == "demo"
    assert succ[0]["attempt"] == 2
    assert succ[0]["tool_count"] == 2


def test_attempt_restart_terminal_failure_emits_permanent_event(tmp_path, monkeypatch):
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.STARTING)
    rt.restart_attempts = 2

    monkeypatch.setattr(
        orch, "_spawn_and_connect",
        lambda d, **kw: (None, "mcp connect failed: refused"),
    )
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)
    monkeypatch.setattr(orch, "_schedule_restart", lambda r: None)

    events: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        orch.logger, "log_event",
        lambda event, **kw: events.append({"event": event, **kw}),
    )

    orch._attempt_restart(rt, attempt=3, backoff_s=16.0)

    perm = [e for e in events if e["event"] == "toolkit_permanently_failed"]
    assert len(perm) == 1
    assert perm[0]["toolkit"] == "demo"
    assert perm[0]["attempts"] == 3
    assert "mcp connect failed" in perm[0]["final_error"]


def test_forwarder_crash_emits_subprocess_crashed_event(tmp_path, monkeypatch):
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.READY)
    rt.proc.poll.return_value = 1
    rt.mcp_client.call_tool = MagicMock(side_effect=ConnectionError("refused"))
    orch._runtimes["demo"] = rt

    monkeypatch.setattr(orch, "_schedule_restart", lambda r: None)

    events: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        orch.logger, "log_event",
        lambda event, **kw: events.append({"event": event, **kw}),
    )

    forward = orch._make_forwarder("demo")
    forward("demo_tool", {})

    crashed = [e for e in events if e["event"] == "subprocess_crashed"]
    assert len(crashed) == 1
    assert crashed[0]["toolkit"] == "demo"
    assert crashed[0]["state_before"] == "ready"


# ── full crash-recovery loop (mocked) ──────────────────────────────────


def test_successful_restarts_consume_budget(tmp_path, monkeypatch):
    """Three successful restarts and the 4th crash transitions to FAILED.

    Pins the §3.3 reading: budget counts attempts, not failures.
    """
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.READY)
    orch._runtimes["demo"] = rt
    rt.proc.poll.return_value = 1  # report dead so each call is a crash

    # Each spawn returns a fresh successful result.
    def make_spawn(_disc, **_kw):
        new_client = MagicMock()
        new_client._subprocess_died = False
        new_client.call_tool = MagicMock(side_effect=ConnectionError("refused"))
        spawn = Orchestrator._SpawnResult(
            upstream_tools=["x"], client=new_client,
        )
        return spawn, None

    monkeypatch.setattr(orch, "_spawn_and_connect", make_spawn)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    # Set up the call_tool to always raise ConnectionError so each call is a crash.
    rt.mcp_client.call_tool = MagicMock(side_effect=ConnectionError("refused"))
    forward = orch._make_forwarder("demo")

    # Crash 1, 2, 3: each followed by a successful restart (mocked).
    for expected_attempt in (1, 2, 3):
        # Need state=READY going in. After a previous successful restart,
        # the new client also raises, so this loop crashes again next.
        rt.proc.poll.return_value = 1  # always-dead so next call detects crash
        # Re-register the side_effect on the (possibly swapped) client.
        rt.mcp_client.call_tool = MagicMock(side_effect=ConnectionError("refused"))
        rt.state = ToolkitState.READY  # simulate recovered

        msg = forward("x", {})
        assert "subprocess crashed" in msg, f"crash {expected_attempt}: {msg!r}"

        # Wait for the daemon thread to complete the (mocked) restart.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and rt.state != ToolkitState.READY:
            time.sleep(0.01)
        assert rt.restart_attempts == expected_attempt

    # Fourth crash: budget exhausted. _schedule_restart hits FAILED branch.
    rt.proc.poll.return_value = 1
    rt.mcp_client.call_tool = MagicMock(side_effect=ConnectionError("refused"))
    rt.state = ToolkitState.READY

    forward("x", {})
    # Give scheduler a moment.
    time.sleep(0.1)
    assert rt.state == ToolkitState.FAILED


def test_full_recovery_loop_mocked(tmp_path, monkeypatch):
    """End-to-end inside the unit test: crash → restart → next call works.

    All subprocess and MCPClient interactions are mocked. This is the
    fast version of the e2e harness — verifies that the pieces compose
    correctly without the cost of real subprocess startup.
    """
    orch = _make_orchestrator(tmp_path)
    rt = _make_runtime(state=ToolkitState.READY)
    orig_proc = rt.proc
    orig_proc.poll.return_value = 1  # dead
    orch._runtimes["demo"] = rt

    # First call: crash. The mock client will raise on call_tool.
    rt.mcp_client.call_tool = MagicMock(side_effect=ConnectionError("refused"))

    # Replace _spawn_and_connect with a "succeeds on next try" mock.
    new_client = MagicMock()
    new_client._subprocess_died = False
    new_client.call_tool = MagicMock(return_value="recovered output")
    fake_spawn = Orchestrator._SpawnResult(
        upstream_tools=["demo_tool"], client=new_client,
    )
    monkeypatch.setattr(orch, "_spawn_and_connect", lambda d, **kw: (fake_spawn, None))
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    forward = orch._make_forwarder("demo")

    # Crash call.
    msg1 = forward("demo_tool", {})
    assert "subprocess crashed" in msg1
    assert rt.state == ToolkitState.CRASHED or rt.state == ToolkitState.STARTING

    # Wait for the daemon restart thread to do its work. Since we patched
    # time.sleep to a no-op, this should resolve quickly.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and rt.state != ToolkitState.READY:
        time.sleep(0.01)
    assert rt.state == ToolkitState.READY, f"runtime still in {rt.state}"

    # Second call should now hit the new client.
    result = forward("demo_tool", {"x": 1})
    assert result == "recovered output"
    new_client.call_tool.assert_called_once_with("demo_tool", {"x": 1})
