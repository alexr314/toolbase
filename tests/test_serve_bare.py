"""Tests for the ``--bare`` opt-in.

Qualified ``<toolkit>__<tool>`` is the default. Bare serving advertises the
un-namespaced ``<tool>`` and, when two toolkits expose the same name, resolves
the clash to the alphabetically-first toolkit (deterministic served list).
Covers the serve.yaml ``default.bare`` field and the orchestrator's bare-mode
collision resolution + the qualified-mode collision heads-up.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from toolbase.serve.config import (
    DefaultBlock,
    ServeConfig,
    ServeConfigError,
    load_serve_config,
    merge_serve_configs,
    save_serve_config,
)
from toolbase.serve.orchestrator import Orchestrator


# ── serve.yaml default.bare ──────────────────────────────────────────────────


def test_bare_defaults_false():
    assert DefaultBlock().bare is False


def test_load_bare_true(tmp_path):
    p = tmp_path / "serve.yaml"
    p.write_text("default:\n  bare: true\n")
    assert load_serve_config(p).default.bare is True


def test_load_bare_absent_is_false(tmp_path):
    p = tmp_path / "serve.yaml"
    p.write_text("default:\n  profile: paper\n")
    assert load_serve_config(p).default.bare is False


def test_load_bare_wrong_type_errors(tmp_path):
    p = tmp_path / "serve.yaml"
    p.write_text("default:\n  bare: sometimes\n")
    with pytest.raises(ServeConfigError):
        load_serve_config(p)


def test_bare_roundtrip(tmp_path):
    cfg = ServeConfig()
    cfg.default.bare = True
    p = tmp_path / "serve.yaml"
    save_serve_config(cfg, p)
    assert load_serve_config(p).default.bare is True


def test_merge_bare_is_or():
    user_on = ServeConfig()
    user_on.default.bare = True
    assert merge_serve_configs(user_on, ServeConfig()).default.bare is True
    assert merge_serve_configs(ServeConfig(), ServeConfig()).default.bare is False


# ── orchestrator bare naming + collision resolution ──────────────────────────


class _FakeProxy:
    """Minimal stand-in for a proxy tool — only the fields the bare-collision
    resolver reads."""

    def __init__(self, wire: str, toolkit: str):
        self._stk_namespaced_name = wire
        self._stk_toolkit = toolkit


def _orch(bare: bool):
    buf = io.StringIO()
    return Orchestrator(console=Console(file=buf), bare=bare), buf


def test_bare_flag_stored():
    assert _orch(True)[0]._bare is True
    assert _orch(False)[0]._bare is False


def test_bare_collision_keeps_alphabetically_first():
    orch, buf = _orch(True)
    # 'calc' and 'matrix' both expose bare 'Multiply'; 'calc' < 'matrix' wins.
    orch._proxy_tools = [
        _FakeProxy("Multiply", "matrix"),
        _FakeProxy("Add", "calc"),
        _FakeProxy("Multiply", "calc"),
    ]
    orch._resolve_bare_collisions()

    survivors = {
        (p._stk_namespaced_name, p._stk_toolkit) for p in orch._proxy_tools
    }
    assert survivors == {("Add", "calc"), ("Multiply", "calc")}
    # exactly one 'Multiply' survives, and the warning names the loser
    assert sum(p._stk_namespaced_name == "Multiply"
               for p in orch._proxy_tools) == 1
    assert "matrix" in buf.getvalue()
    assert "Multiply" in buf.getvalue()


def test_bare_no_collision_keeps_all():
    orch, _ = _orch(True)
    orch._proxy_tools = [_FakeProxy("Add", "calc"), _FakeProxy("Invert", "mx")]
    orch._resolve_bare_collisions()
    assert len(orch._proxy_tools) == 2


# ── qualified-mode collision heads-up (no dedup) ─────────────────────────────


class _FakeRuntime:
    def __init__(self, names):
        self.upstream_tool_names = names


def test_qualified_warn_flags_but_keeps_both():
    orch, buf = _orch(False)
    orch._runtimes = {
        "calc": _FakeRuntime(["Add", "Multiply"]),
        "matrix": _FakeRuntime(["Multiply", "Invert"]),
    }
    orch._warn_name_collisions()  # must not raise, must not drop anything
    out = buf.getvalue()
    assert "Multiply" in out
    assert "calc__Multiply" in out and "matrix__Multiply" in out
    assert len(orch._runtimes) == 2


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
