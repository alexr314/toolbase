"""
Unit tests for ``toolbase.setup.context.SetupContext`` (Day 2).

Test the subprocess-side ``ctx.*`` API in isolation — mock the RPC
client and verify each method produces the right wire payload and
parses the response correctly.

The end-to-end behavior (real subprocess, real RPC) is covered by
``test_setup_host.py``. Here we focus on:

- Each ``prompt_*`` method serializes its kwargs to the right
  ``kind`` + params shape on the wire.
- Responses with ``None`` and with values both round-trip cleanly.
- ``ctx.set_config`` updates the local snapshot (write-through) so
  ``ctx.get_config`` sees the new value within one setup() call.
- Validate-mode guards reject prompt/set_config/download with a
  clear RuntimeError before any RPC happens.
- Local-only methods (``toolkit_path``, ``data_dir``, etc.) never
  touch the RPC client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from toolbase.setup import _rpc
from toolbase.setup.context import SetupContext, _SetupContextRPC


class _MockRPC:
    """Stand-in for ``_SetupContextRPC`` that records calls and
    returns scripted responses.

    The test sets ``self.responses`` to a list of (method_filter,
    response) pairs; each call pops the matching entry. If no entry
    matches, raises an AssertionError so the test fails loudly.
    """

    def __init__(self, responses: Optional[List] = None):
        self.calls: List[Dict[str, Any]] = []
        self.responses: List = responses or []

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self.calls.append({"method": method, "params": params or {}})
        if not self.responses:
            return None
        # Either a (method_filter, response) pair, or a bare response.
        head = self.responses.pop(0)
        if isinstance(head, tuple) and len(head) == 2:
            expected_method, response = head
            assert expected_method == method, (
                f"expected RPC method {expected_method!r}, got {method!r}"
            )
            if isinstance(response, _rpc.SetupRPCError):
                raise response
            return response
        if isinstance(head, _rpc.SetupRPCError):
            raise head
        return head

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self.calls.append({"method": method, "params": params or {}, "is_notify": True})


def _make_ctx(
    *,
    mode: str = "setup",
    prompt_mode: str = "ask",
    config_snapshot: Optional[Dict] = None,
    rpc: Optional[_MockRPC] = None,
    tmp_path: Optional[Path] = None,
) -> SetupContext:
    if rpc is None:
        rpc = _MockRPC()
    if tmp_path is None:
        tmp_path = Path("/tmp/synthetic")
    return SetupContext(
        rpc=rpc,
        mode=mode,
        prompt_mode=prompt_mode,
        config_snapshot=config_snapshot or {},
        toolkit_path=tmp_path / "toolkit",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        config_path=tmp_path / "config.yaml",
    )


# ── local properties (no RPC) ─────────────────────────────────────────


def test_toolkit_path_is_local(tmp_path):
    rpc = _MockRPC()
    ctx = _make_ctx(rpc=rpc, tmp_path=tmp_path)
    assert ctx.toolkit_path == tmp_path / "toolkit"
    assert rpc.calls == []  # no RPC for local properties


def test_data_dir_auto_creates(tmp_path):
    ctx = _make_ctx(tmp_path=tmp_path)
    d = ctx.data_dir
    assert d.exists()
    assert d.is_dir()


def test_cache_dir_auto_creates(tmp_path):
    ctx = _make_ctx(tmp_path=tmp_path)
    c = ctx.cache_dir
    assert c.exists()
    assert c.is_dir()


def test_config_path_passes_through(tmp_path):
    ctx = _make_ctx(tmp_path=tmp_path)
    assert ctx.config_path == tmp_path / "config.yaml"


def test_get_config_reads_from_snapshot():
    ctx = _make_ctx(config_snapshot={"a": 1, "b": "two"})
    assert ctx.get_config("a") == 1
    assert ctx.get_config("b") == "two"
    assert ctx.get_config("missing") is None
    assert ctx.get_config("missing", default=42) == 42


def test_config_property_returns_copy():
    """Mutating ctx.config shouldn't affect the snapshot."""
    ctx = _make_ctx(config_snapshot={"a": 1})
    cfg = ctx.config
    cfg["a"] = 999
    assert ctx.get_config("a") == 1  # original unchanged


# ── log routing ───────────────────────────────────────────────────────


@pytest.mark.parametrize("method,level", [
    ("info", "info"),
    ("warn", "warn"),
    ("error", "error"),
    ("hint", "hint"),
    ("success", "success"),
])
def test_log_methods_emit_correct_level(method, level):
    rpc = _MockRPC()
    ctx = _make_ctx(rpc=rpc)
    getattr(ctx, method)("a message")
    assert len(rpc.calls) == 1
    assert rpc.calls[0]["method"] == "log"
    assert rpc.calls[0]["params"]["level"] == level
    assert rpc.calls[0]["params"]["message"] == "a message"


# ── prompt methods → RPC payloads ─────────────────────────────────────


def test_prompt_string_serializes_correctly():
    rpc = _MockRPC(responses=[("prompt", "Alex")])
    ctx = _make_ctx(rpc=rpc)
    result = ctx.prompt("Your name?", default="anon")
    assert result == "Alex"
    assert rpc.calls[0]["params"] == {
        "kind": "string", "label": "Your name?", "default": "anon",
    }


def test_prompt_path_returns_path_object():
    rpc = _MockRPC(responses=[("prompt", "/tmp/data")])
    ctx = _make_ctx(rpc=rpc)
    result = ctx.prompt_path("Data dir:", must_exist=False)
    assert isinstance(result, Path)
    assert str(result) == "/tmp/data"
    sent = rpc.calls[0]["params"]
    assert sent["kind"] == "path"
    assert sent["must_exist"] is False


def test_prompt_path_returns_none_on_skip():
    rpc = _MockRPC(responses=[("prompt", None)])
    ctx = _make_ctx(rpc=rpc)
    assert ctx.prompt_path("Data dir:") is None


def test_prompt_path_must_exist_propagates():
    rpc = _MockRPC(responses=[("prompt", "/x")])
    ctx = _make_ctx(rpc=rpc)
    ctx.prompt_path("p", must_exist=True)
    assert rpc.calls[0]["params"]["must_exist"] is True


def test_prompt_int_carries_min_max():
    rpc = _MockRPC(responses=[("prompt", 8)])
    ctx = _make_ctx(rpc=rpc)
    result = ctx.prompt_int("Workers:", default=4, min=1, max=64)
    assert result == 8
    sent = rpc.calls[0]["params"]
    assert sent["kind"] == "int"
    assert sent["min"] == 1
    assert sent["max"] == 64


def test_prompt_float_carries_min_max():
    rpc = _MockRPC(responses=[("prompt", 0.95)])
    ctx = _make_ctx(rpc=rpc)
    result = ctx.prompt_float("Threshold:", min=0.0, max=1.0)
    assert result == 0.95
    assert rpc.calls[0]["params"]["kind"] == "float"


def test_prompt_secret_uses_secret_kind():
    rpc = _MockRPC(responses=[("prompt", "shh")])
    ctx = _make_ctx(rpc=rpc)
    assert ctx.prompt_secret("Token:") == "shh"
    assert rpc.calls[0]["params"]["kind"] == "secret"


def test_confirm_returns_bool_default_on_skip():
    """When parent returns None (impossible by spec but defensive),
    confirm falls back to its default."""
    rpc = _MockRPC(responses=[("prompt", None)])
    ctx = _make_ctx(rpc=rpc)
    assert ctx.confirm("OK?", default=True) is True


def test_confirm_returns_true_when_parent_says_true():
    rpc = _MockRPC(responses=[("prompt", True)])
    ctx = _make_ctx(rpc=rpc)
    assert ctx.confirm("OK?") is True


def test_confirm_kind_is_bool():
    rpc = _MockRPC(responses=[("prompt", False)])
    ctx = _make_ctx(rpc=rpc)
    ctx.confirm("OK?", default=False)
    assert rpc.calls[0]["params"]["kind"] == "bool"


def test_choice_normalizes_string_options_to_pairs():
    rpc = _MockRPC(responses=[("prompt", "apple")])
    ctx = _make_ctx(rpc=rpc)
    ctx.choice("Pick:", ["apple", "banana", "cherry"])
    sent_options = rpc.calls[0]["params"]["options"]
    # Each becomes [key, label] pair where key == label for strings.
    assert sent_options == [["apple", "apple"], ["banana", "banana"], ["cherry", "cherry"]]


def test_choice_passes_tuple_options_through():
    rpc = _MockRPC(responses=[("prompt", "dl")])
    ctx = _make_ctx(rpc=rpc)
    options = [
        ("dl", "Download (~2.3 GB)"),
        ("path", "I have it"),
        ("skip", "Skip"),
    ]
    result = ctx.choice("How?", options)
    assert result == "dl"
    sent = rpc.calls[0]["params"]["options"]
    assert sent == [["dl", "Download (~2.3 GB)"], ["path", "I have it"], ["skip", "Skip"]]


# ── set_config (write-through) ────────────────────────────────────────


def test_set_config_calls_rpc_and_updates_snapshot():
    rpc = _MockRPC(responses=[("set_config", None)])
    ctx = _make_ctx(rpc=rpc, config_snapshot={"existing": "v"})
    ctx.set_config("new_key", "new_value")
    assert rpc.calls[0]["params"] == {"name": "new_key", "value": "new_value"}
    # Write-through: get_config sees the new value.
    assert ctx.get_config("new_key") == "new_value"
    # Existing values still there.
    assert ctx.get_config("existing") == "v"


def test_set_config_does_not_update_snapshot_if_rpc_fails():
    """If the RPC raises (parent-side write fails), the local snapshot
    must NOT be updated — otherwise the file and snapshot diverge."""
    rpc = _MockRPC(responses=[("set_config", _rpc.SetupRPCError("io_error", "disk full"))])
    ctx = _make_ctx(rpc=rpc)
    with pytest.raises(_rpc.SetupRPCError):
        ctx.set_config("x", 42)
    assert ctx.get_config("x") is None  # not added to snapshot


# ── validate-mode guards ──────────────────────────────────────────────


@pytest.mark.parametrize("method,args,kwargs", [
    ("prompt", ("label",), {}),
    ("prompt_path", ("label",), {}),
    ("prompt_int", ("label",), {}),
    ("prompt_float", ("label",), {}),
    ("prompt_secret", ("label",), {}),
    ("confirm", ("label",), {}),
    ("choice", ("label", ["a", "b"]), {}),
    ("set_config", ("k", "v"), {}),
])
def test_validate_mode_blocks_setup_only_methods(method, args, kwargs):
    rpc = _MockRPC()
    ctx = _make_ctx(mode="validate", rpc=rpc)
    with pytest.raises(RuntimeError, match="not allowed in validate"):
        getattr(ctx, method)(*args, **kwargs)
    # No RPC call was made — the guard is client-side.
    assert rpc.calls == []


def test_validate_mode_allows_get_config():
    """``get_config`` and the other read-only methods are fine in
    validate mode — that's the whole point of validate."""
    ctx = _make_ctx(mode="validate", config_snapshot={"opacity_path": "/data"})
    assert ctx.get_config("opacity_path") == "/data"


def test_validate_mode_allows_log_methods():
    """``info`` etc. are fine in validate mode for diagnostic output."""
    rpc = _MockRPC()
    ctx = _make_ctx(mode="validate", rpc=rpc)
    ctx.info("validate diagnostic")
    assert len(rpc.calls) == 1
