"""Tests for per-serve state-config overrides (``config_overrides``).

An embedding harness (e.g. a benchmark runner) serves one orchestrator
per working directory and needs file-aware tools scoped to *its* tree —
``Orchestrator(config_overrides={"base_directory": <sandbox>})`` merges
the override over every toolkit's resolved state-config before host
spawn, and ``toolbase_tools(config_overrides=...)`` threads it through
the orchestral bridge.

Covers:

1. Overrides merge over the resolved config at spawn (override wins on
   key collision, resolved keys survive otherwise).
2. No overrides → spawn sees the resolved config untouched (and an
   empty resolved config stays falsy: no ``--state-config`` flag
   regression for configless toolkits).
3. The restart path re-applies overrides.
4. ``toolbase_tools`` forwards the kwarg to the Orchestrator.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from toolbase.serve import orchestrator
from toolbase.serve.orchestrator import Orchestrator


class _SpawnCapture:
    """Stand-in for Orchestrator._spawn_and_connect that records the
    state_config it would have launched the host with."""

    def __init__(self):
        self.calls = []

    def __call__(self, disc, *, state_config=None):
        self.calls.append({"toolkit": disc.name, "state_config": state_config})
        return None, "captured (test stops before real spawn)"


@pytest.fixture
def quiet_orch(monkeypatch: pytest.MonkeyPatch):
    """An Orchestrator whose spawn is captured and whose state-config
    resolution is stubbed to a known dict."""

    def make(config_overrides=None, resolved=None):
        orch = Orchestrator(config_overrides=config_overrides)
        capture = _SpawnCapture()
        monkeypatch.setattr(orch, "_spawn_and_connect", capture)
        monkeypatch.setattr(
            orchestrator, "_resolve_state_config",
            lambda disc: (dict(resolved) if resolved is not None else {}, None),
        )
        return orch, capture

    return make


def _disc(tmp_path: Path, name: str = "demo"):
    tk = tmp_path / name
    tk.mkdir()
    return orchestrator.ToolkitDiscovery(
        name=name, path=tk,
        meta={"environment": "venv", "python_path": "x", "python_version": "3.12"},
    )


def test_overrides_merge_over_resolved_config(quiet_orch, tmp_path):
    orch, capture = quiet_orch(
        config_overrides={"base_directory": "/sandbox/trial1"},
        resolved={"base_directory": "/cwd", "mg5_path": "/opt/mg5"},
    )
    orch._launch_one(_disc(tmp_path))
    assert capture.calls, "spawn was never reached"
    sc = capture.calls[0]["state_config"]
    assert sc["base_directory"] == "/sandbox/trial1"   # override wins
    assert sc["mg5_path"] == "/opt/mg5"                # resolved keys survive


def test_no_overrides_leaves_config_untouched(quiet_orch, tmp_path):
    orch, capture = quiet_orch(resolved={"mg5_path": "/opt/mg5"})
    orch._launch_one(_disc(tmp_path))
    assert capture.calls[0]["state_config"] == {"mg5_path": "/opt/mg5"}


def test_no_overrides_keeps_empty_config_falsy(quiet_orch, tmp_path):
    # A configless toolkit must keep passing a falsy state_config so the
    # host command is built without a --state-config flag, as before.
    orch, capture = quiet_orch(resolved={})
    orch._launch_one(_disc(tmp_path))
    assert not capture.calls[0]["state_config"]


def test_overrides_apply_to_configless_toolkit(quiet_orch, tmp_path):
    orch, capture = quiet_orch(
        config_overrides={"base_directory": "/sandbox/t"}, resolved={})
    orch._launch_one(_disc(tmp_path))
    assert capture.calls[0]["state_config"] == {"base_directory": "/sandbox/t"}


def test_bridge_threads_overrides_through():
    # The orchestral bridge accepts and forwards the kwarg.
    from toolbase.connect.orchestral import toolbase_tools
    assert "config_overrides" in inspect.signature(toolbase_tools).parameters

    captured = {}

    class _FakeOrch:
        def __init__(self, **kw):
            captured.update(kw)

        def start(self):
            return []

        def shutdown(self):
            pass

    import toolbase.connect.orchestral as bridge
    orig_orch = bridge.Orchestrator
    orig_resolve = bridge._resolve
    bridge.Orchestrator = _FakeOrch
    bridge._resolve = lambda project_root, profile: object()
    try:
        with toolbase_tools(profile="p", quiet=True,
                            config_overrides={"base_directory": "/sb"}) as tools:
            assert tools == []
    finally:
        bridge.Orchestrator = orig_orch
        bridge._resolve = orig_resolve
    assert captured["config_overrides"] == {"base_directory": "/sb"}
