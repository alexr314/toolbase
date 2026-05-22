"""
Unit tests for the parent-side RPC handlers in
``toolbase.setup.runner``.

Tests for ``_default_prompt_handler``, ``_default_set_config_handler``
in isolation (no subprocess) — drive each with constructed params
dicts and verify the response.

Real-subprocess coverage (handlers wired into the runner, driving an
actual setup.py) lives in ``test_setup_host.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from toolbase.setup import _rpc
from toolbase.setup.runner import (
    _default_prompt_handler,
    _default_set_config_handler,
    _handle_choice,
    _handle_confirm,
    _handle_value_prompt,
    _coerce_for_kind,
)


# ── confirm dispatch ──────────────────────────────────────────────────


@pytest.mark.parametrize("mode,default,expected", [
    ("yes", True, True),
    ("yes", False, True),
    ("no", True, False),
    ("no", False, False),
    ("skip", True, True),
    ("skip", False, False),
])
def test_handle_confirm_non_interactive(mode, default, expected):
    assert _handle_confirm(mode, "OK?", default) is expected


def test_handle_confirm_ask_mode_uses_click(monkeypatch):
    import click
    monkeypatch.setattr(click, "confirm", lambda label, default: True)
    assert _handle_confirm("ask", "OK?", False) is True


# ── value prompt dispatch ─────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["yes", "skip", "no"])
def test_value_prompt_non_interactive_with_default(mode):
    """Non-ask modes return the default coerced to the right type."""
    result = _handle_value_prompt(mode, "int", "label", 42, {})
    assert result == 42


@pytest.mark.parametrize("mode", ["yes", "skip", "no"])
def test_value_prompt_non_interactive_no_default_returns_none(mode):
    """No default + non-interactive = None (skip the field)."""
    result = _handle_value_prompt(mode, "string", "label", None, {})
    assert result is None


def test_value_prompt_string_in_ask_mode(monkeypatch):
    import click
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "the answer")
    result = _handle_value_prompt("ask", "string", "Q", None, {})
    assert result == "the answer"


def test_value_prompt_int_validates_min(monkeypatch):
    """Bad value (below min) → retry. Good value on retry → return."""
    import click
    answers = iter(["0", "5"])
    echoes = []
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: next(answers))
    monkeypatch.setattr(click, "echo", lambda msg: echoes.append(msg))
    result = _handle_value_prompt(
        "ask", "int", "n", None, {"min": 1, "max": 10},
    )
    assert result == 5
    assert any("must be >= 1" in e for e in echoes)


def test_value_prompt_int_validates_max(monkeypatch):
    import click
    answers = iter(["100", "5"])
    echoes = []
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: next(answers))
    monkeypatch.setattr(click, "echo", lambda msg: echoes.append(msg))
    result = _handle_value_prompt(
        "ask", "int", "n", None, {"min": 1, "max": 10},
    )
    assert result == 5
    assert any("must be <= 10" in e for e in echoes)


def test_value_prompt_float_works(monkeypatch):
    import click
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "0.5")
    result = _handle_value_prompt("ask", "float", "x", None, {})
    assert result == 0.5


def test_value_prompt_path_expansion(monkeypatch, tmp_path):
    """``~`` should expand."""
    import click
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "~/some-data")
    result = _handle_value_prompt("ask", "path", "p", None, {})
    # Returned as string; subprocess wraps in Path.
    assert "~" not in result  # should be expanded
    assert "/some-data" in result


def test_value_prompt_path_must_exist_retry(monkeypatch, tmp_path):
    """If must_exist and the path doesn't, retry."""
    import click
    nonexistent = str(tmp_path / "nope")
    existent = str(tmp_path)
    answers = iter([nonexistent, existent])
    echoes = []
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: next(answers))
    monkeypatch.setattr(click, "echo", lambda msg: echoes.append(msg))

    result = _handle_value_prompt(
        "ask", "path", "p", None, {"must_exist": True},
    )
    assert result == str(tmp_path)
    assert any("does not exist" in e for e in echoes)


def test_value_prompt_retry_budget_exhausted_returns_none(monkeypatch):
    """3 bad inputs in a row → return None (don't loop forever)."""
    import click
    answers = iter(["bad", "still bad", "nope", "should never reach"])
    echoes = []
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: next(answers))
    monkeypatch.setattr(click, "echo", lambda msg: echoes.append(msg))

    result = _handle_value_prompt("ask", "int", "n", None, {})
    assert result is None


def test_value_prompt_abort_returns_none(monkeypatch):
    """Ctrl-C / Esc → return None."""
    import click
    def raise_abort(*a, **kw):
        raise click.exceptions.Abort()
    monkeypatch.setattr(click, "prompt", raise_abort)
    result = _handle_value_prompt("ask", "string", "x", None, {})
    assert result is None


# ── choice dispatch ───────────────────────────────────────────────────


def test_choice_in_yes_mode_picks_first():
    options = [["a", "Apple"], ["b", "Banana"]]
    assert _handle_choice("yes", "label", options) == "a"


def test_choice_in_no_mode_returns_none():
    options = [["a", "Apple"]]
    assert _handle_choice("no", "label", options) is None


def test_choice_in_skip_mode_picks_first():
    options = [["a", "A"], ["b", "B"]]
    assert _handle_choice("skip", "label", options) == "a"


def test_choice_with_empty_options_returns_none():
    assert _handle_choice("yes", "label", []) is None


def test_choice_ask_mode_by_number(monkeypatch):
    import click
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "2")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    options = [["a", "A"], ["b", "B"], ["c", "C"]]
    assert _handle_choice("ask", "Pick:", options) == "b"


def test_choice_ask_mode_by_key(monkeypatch):
    import click
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "c")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    options = [["a", "A"], ["b", "B"], ["c", "C"]]
    assert _handle_choice("ask", "Pick:", options) == "c"


def test_choice_ask_mode_invalid_then_valid(monkeypatch):
    import click
    answers = iter(["99", "xyz", "1"])  # bad number, bad key, then 1
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: next(answers))
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    options = [["a", "A"], ["b", "B"]]
    assert _handle_choice("ask", "Pick:", options) == "a"


# ── coerce_for_kind ───────────────────────────────────────────────────


@pytest.mark.parametrize("kind,raw,expected", [
    ("string", "x", "x"),
    ("string", 42, "42"),
    ("secret", "abc", "abc"),
    ("path", "/tmp", "/tmp"),
    ("int", "5", 5),
    ("int", 5, 5),
    ("int", "not-an-int", None),
    ("float", "0.5", 0.5),
    ("float", 0.5, 0.5),
    ("float", "nope", None),
    ("unknown", "x", "x"),  # passthrough
])
def test_coerce_for_kind(kind, raw, expected):
    assert _coerce_for_kind(kind, raw) == expected


# ── set_config handler ───────────────────────────────────────────────


def test_set_config_handler_writes_through(tmp_path, monkeypatch):
    """Verify the handler invokes set_config_value from storage."""
    captured = {}

    def fake_set(name, key, value, *, base=None):
        captured["call"] = (name, key, value)
        return tmp_path / "x.yaml"

    monkeypatch.setattr(
        "toolbase.setup.runner.set_config_value", fake_set,
    )
    handler = _default_set_config_handler("mykit")
    result = handler({"name": "api_key", "value": "sct_xxx"})
    assert result is None
    assert captured["call"] == ("mykit", "api_key", "sct_xxx")


def test_set_config_handler_rejects_empty_name(monkeypatch):
    handler = _default_set_config_handler("mykit")
    with pytest.raises(_rpc.SetupRPCError, match="invalid_params"):
        handler({"name": "", "value": "x"})
    with pytest.raises(_rpc.SetupRPCError, match="invalid_params"):
        handler({"value": "x"})


def test_set_config_handler_accepts_various_value_types(tmp_path, monkeypatch):
    """The handler should NOT validate against ConfigSchema — Tier 2
    set_config is intentionally schema-less so authors can stash
    derived state (e.g., a detected GPU bool) that isn't declared in
    toolkit.yaml's config: block."""
    captured_calls = []

    def fake_set(name, key, value, *, base=None):
        captured_calls.append((name, key, value))
        return tmp_path / "x.yaml"

    monkeypatch.setattr(
        "toolbase.setup.runner.set_config_value", fake_set,
    )
    handler = _default_set_config_handler("mykit")

    handler({"name": "k1", "value": "string"})
    handler({"name": "k2", "value": 42})
    handler({"name": "k3", "value": True})
    handler({"name": "k4", "value": [1, 2, 3]})
    handler({"name": "k5", "value": {"nested": "dict"}})
    handler({"name": "k6", "value": None})

    assert len(captured_calls) == 6
    assert captured_calls[2] == ("mykit", "k3", True)
    assert captured_calls[5] == ("mykit", "k6", None)


# ── prompt handler dispatch (top-level) ───────────────────────────────


def test_prompt_handler_dispatches_to_confirm_for_bool_kind():
    handler = _default_prompt_handler("yes")
    result = handler({"kind": "bool", "label": "OK?", "default": False})
    # yes mode + confirm = True
    assert result is True


def test_prompt_handler_dispatches_to_choice_for_choice_kind():
    handler = _default_prompt_handler("skip")
    result = handler({
        "kind": "choice",
        "label": "Pick:",
        "options": [["a", "A"], ["b", "B"]],
    })
    assert result == "a"  # skip mode picks first


def test_prompt_handler_dispatches_to_value_for_string_kind():
    handler = _default_prompt_handler("skip")
    result = handler({"kind": "string", "label": "x", "default": "hello"})
    assert result == "hello"


def test_prompt_handler_unknown_kind_falls_through_to_value():
    """Unknown kind → treated as string."""
    handler = _default_prompt_handler("skip")
    result = handler({"kind": "weird", "label": "x", "default": "fallback"})
    assert result == "fallback"
