"""
Unit tests for the line-mode RPC primitives in ``toolbase.setup._rpc``.

The protocol is intentionally tiny but every shape needs a sentinel
test because it's the seam between two processes — drift here means
silent hangs in production. Cover:

- Round-trip encode/decode for every message shape (request, response,
  notification, hello, go, done).
- Framing edge cases: blank lines, trailing whitespace, malformed JSON.
- ``read_message`` returns None on EOF (not an exception).
- ``parse_line`` rejects non-object payloads cleanly.

These tests run against in-memory ``StringIO`` streams; the multi-process
behavior is covered by ``test_setup_host.py``.
"""

from __future__ import annotations

import io
import json

import pytest

from toolbase.setup import _rpc


class TestEncode:
    def test_encode_returns_single_line(self):
        out = _rpc.encode({"method": "log", "params": {"level": "info", "message": "hi"}})
        assert out.endswith("\n")
        assert out.count("\n") == 1

    def test_encode_preserves_unicode(self):
        out = _rpc.encode({"method": "log", "params": {"message": "héllo 世界"}})
        # ensure_ascii=False means raw chars, not \uXXXX escapes
        assert "héllo" in out
        assert "世界" in out

    def test_encode_escapes_internal_newlines(self):
        out = _rpc.encode({"method": "log", "params": {"message": "line1\nline2"}})
        assert out.count("\n") == 1  # only the trailing one
        # The \n in the message is escaped as \\n in the JSON
        assert "\\n" in out


class TestParseLine:
    def test_parse_valid_object(self):
        msg = _rpc.parse_line('{"id": 1, "method": "log", "params": {}}')
        assert msg.is_request
        assert msg.id == 1
        assert msg.method == "log"

    def test_parse_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _rpc.parse_line("")

    def test_parse_malformed_json_raises(self):
        with pytest.raises(ValueError, match="malformed"):
            _rpc.parse_line("not json at all")

    def test_parse_non_object_raises(self):
        # JSON arrays, numbers, strings — all valid JSON but not RPC payloads.
        with pytest.raises(ValueError, match="must be a JSON object"):
            _rpc.parse_line('[1, 2, 3]')
        with pytest.raises(ValueError, match="must be a JSON object"):
            _rpc.parse_line('"just a string"')


class TestReadMessage:
    def test_returns_none_on_eof(self):
        stream = io.StringIO("")
        assert _rpc.read_message(stream) is None

    def test_reads_one_message_per_call(self):
        stream = io.StringIO(
            _rpc.encode({"method": "a", "params": {}})
            + _rpc.encode({"method": "b", "params": {}})
        )
        m1 = _rpc.read_message(stream)
        m2 = _rpc.read_message(stream)
        assert m1.method == "a"
        assert m2.method == "b"
        # Third read sees EOF.
        assert _rpc.read_message(stream) is None

    def test_skips_blank_lines(self):
        stream = io.StringIO(
            "\n" + _rpc.encode({"method": "after_blank", "params": {}})
        )
        msg = _rpc.read_message(stream)
        assert msg is not None
        assert msg.method == "after_blank"

    def test_handles_multibyte_unicode_payload(self):
        encoded = _rpc.encode({"method": "log", "params": {"message": "日本語"}})
        stream = io.StringIO(encoded)
        msg = _rpc.read_message(stream)
        assert msg.params["message"] == "日本語"


class TestMessageShape:
    def test_request_has_id_and_method(self):
        m = _rpc.parse_line('{"id": 5, "method": "x", "params": {}}')
        assert m.is_request
        assert not m.is_response
        assert not m.is_notification

    def test_response_has_id_no_method(self):
        m = _rpc.parse_line('{"id": 5, "result": 42}')
        assert m.is_response
        assert not m.is_request
        assert m.result == 42

    def test_response_with_error_classified_as_response(self):
        m = _rpc.parse_line('{"id": 5, "error": {"code": "x", "message": "y"}}')
        assert m.is_response
        assert m.error == {"code": "x", "message": "y"}

    def test_notification_has_method_no_id(self):
        m = _rpc.parse_line('{"method": "progress", "params": {"bytes": 1}}')
        assert m.is_notification
        assert not m.is_request
        assert not m.is_response

    def test_params_defaults_to_empty_dict(self):
        m = _rpc.parse_line('{"method": "hello"}')
        # No params field → empty dict (so handlers don't need None-checks)
        assert m.params == {}


class TestFactoryHelpers:
    def test_make_response(self):
        out = _rpc.make_response(7, {"value": 42})
        assert out == {"id": 7, "result": {"value": 42}}

    def test_make_error_response(self):
        out = _rpc.make_error_response(7, "missing", "field x not set")
        assert out == {
            "id": 7,
            "error": {"code": "missing", "message": "field x not set"},
        }

    def test_make_request(self):
        out = _rpc.make_request(7, "log", {"level": "info"})
        assert out == {"id": 7, "method": "log", "params": {"level": "info"}}

    def test_make_notification_has_no_id(self):
        out = _rpc.make_notification("progress", {"bytes": 100})
        assert "id" not in out
        assert out["method"] == "progress"

    def test_make_hello_carries_protocol_version(self):
        out = _rpc.make_hello(has_setup=True, has_validate=False)
        assert out["method"] == "hello"
        assert out["params"]["protocol"] == _rpc.PROTOCOL_VERSION
        assert out["params"]["has_setup"] is True
        assert out["params"]["has_validate"] is False
        assert "python_version" in out["params"]

    def test_make_go_carries_all_fields(self):
        out = _rpc.make_go(
            mode="setup",
            prompt_mode="ask",
            config={"k": "v"},
            toolkit_path="/tmp/t",
            data_dir="/tmp/d",
            cache_dir="/tmp/c",
            config_path="/tmp/c.yaml",
        )
        assert out["method"] == "go"
        p = out["params"]
        assert p["mode"] == "setup"
        assert p["prompt_mode"] == "ask"
        assert p["config"] == {"k": "v"}
        assert p["toolkit_path"] == "/tmp/t"
        assert p["data_dir"] == "/tmp/d"
        assert p["cache_dir"] == "/tmp/c"
        assert p["config_path"] == "/tmp/c.yaml"

    def test_make_done_with_traceback(self):
        out = _rpc.make_done(result=False, traceback_str="Traceback...")
        assert out["params"]["result"] is False
        assert out["params"]["traceback"] == "Traceback..."

    def test_make_done_without_traceback(self):
        out = _rpc.make_done(result=True)
        assert out["params"]["traceback"] is None


class TestRoundTrip:
    """Verify every factory helper survives encode → read_message intact."""

    @pytest.mark.parametrize("factory_call", [
        lambda: _rpc.make_request(1, "log", {"level": "info", "message": "hi"}),
        lambda: _rpc.make_response(1, {"x": 1}),
        lambda: _rpc.make_error_response(1, "foo", "bar"),
        lambda: _rpc.make_notification("progress", {"bytes": 100}),
        lambda: _rpc.make_hello(has_setup=True, has_validate=True),
        lambda: _rpc.make_go(
            mode="setup", prompt_mode="ask", config={},
            toolkit_path="/", data_dir="/", cache_dir="/", config_path="/x",
        ),
        lambda: _rpc.make_done(result=True),
    ])
    def test_factory_round_trips(self, factory_call):
        original = factory_call()
        encoded = _rpc.encode(original)
        stream = io.StringIO(encoded)
        msg = _rpc.read_message(stream)
        assert msg is not None
        assert msg.raw == original


class TestSetupRPCError:
    def test_carries_code_and_message(self):
        e = _rpc.SetupRPCError("network", "could not connect")
        assert e.code == "network"
        assert e.rpc_message == "could not connect"
        assert "network" in str(e)
        assert "could not connect" in str(e)

    def test_empty_code_omitted_from_message(self):
        e = _rpc.SetupRPCError("", "just the message")
        assert "just the message" in str(e)
