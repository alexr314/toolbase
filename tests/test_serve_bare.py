"""Tests for the ``--bare`` opt-in.

Qualified ``<toolkit>__<tool>`` is the default. Bare serving advertises the
un-namespaced ``<tool>``; when two toolkits expose the same name, those tools
fall back to their qualified form (both stay callable) rather than one being
dropped. Covers the serve.yaml ``default.bare`` field and the orchestrator's
bare-mode collision disambiguation + the qualified-mode collision heads-up.
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
    disambiguator reads. In bare mode the wire name starts as the bare
    upstream name."""

    def __init__(self, bare_name: str, toolkit: str):
        self._stk_namespaced_name = bare_name
        self._stk_upstream_name = bare_name
        self._stk_toolkit = toolkit


def _orch(bare: bool):
    buf = io.StringIO()
    return Orchestrator(console=Console(file=buf), bare=bare), buf


def test_bare_flag_stored():
    assert _orch(True)[0]._bare is True
    assert _orch(False)[0]._bare is False


def test_bare_collision_qualifies_both_and_keeps_them():
    orch, buf = _orch(True)
    # 'calc' and 'matrix' both expose bare 'Multiply'; 'calc' also has 'Add'.
    orch._proxy_tools = [
        _FakeProxy("Multiply", "matrix"),
        _FakeProxy("Add", "calc"),
        _FakeProxy("Multiply", "calc"),
    ]
    orch._disambiguate_bare_collisions()

    names = {(p._stk_namespaced_name, p._stk_toolkit) for p in orch._proxy_tools}
    # Nothing dropped: all three still served.
    assert len(orch._proxy_tools) == 3
    # The unique name stays bare; the colliding one falls back to qualified.
    assert names == {
        ("Add", "calc"),                 # unique -> stays bare
        ("calc__Multiply", "calc"),      # collided -> qualified, still callable
        ("matrix__Multiply", "matrix"),  # collided -> qualified, still callable
    }
    out = buf.getvalue()
    assert "Multiply" in out and "calc__Multiply" in out and "matrix__Multiply" in out


def test_bare_no_collision_keeps_bare_names():
    orch, _ = _orch(True)
    orch._proxy_tools = [_FakeProxy("Add", "calc"), _FakeProxy("Invert", "mx")]
    orch._disambiguate_bare_collisions()
    assert len(orch._proxy_tools) == 2
    assert {p._stk_namespaced_name for p in orch._proxy_tools} == {"Add", "Invert"}


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
