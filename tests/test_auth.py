"""Unit tests for the per-user CLI token auth module.

Covers:

- Prefix classification (``tb_user_`` canonical, ``tb_user_`` retired,
  ``stk_`` / ``toolkit_`` legacy per-toolkit).
- Token storage helpers (per-user + legacy per-toolkit). Mode 0600
  set; missing-file behavior; empty-file edge cases.
- Migration helpers (``find_legacy_token_files``,
  ``delete_legacy_token_files``).
- Publish-time token resolution (``load_token_for_publish``) — per-user
  preferred, legacy fallback, ``"none"`` source when nothing's stored.
- ``BrowserFlow`` end-to-end with a synthetic frontend that POSTs to
  the loopback callback. Includes the CSRF state-mismatch path.
- API helpers (``whoami``, ``revoke_token``) with mocked HTTP.

The browser-flow test exercises the real loopback HTTP server (no
mock) to catch port-binding regressions and CORS-header drift.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from toolbase import auth


# ── prefix classification ─────────────────────────────────────────────


def test_is_user_token_recognizes_stk_user():
    assert auth.is_user_token("tb_user_abc123")


def test_is_user_token_rejects_other_prefixes():
    assert not auth.is_user_token("stk_abc123")  # per-toolkit legacy
    assert not auth.is_user_token("toolkit_abc123")
    assert not auth.is_user_token("stk_user_abc")  # retired per-user prefix
    assert not auth.is_user_token("sct_user_abc")  # retired per-user prefix
    assert not auth.is_user_token("stk_user")  # no underscore-suffix; defensive
    assert not auth.is_user_token("")


def test_is_retired_user_token_recognizes_retired_prefixes():
    # The per-user prefix evolved sct_user_ -> stk_user_ -> tb_user_.
    # Both earlier prefixes are retired; the backend no longer accepts
    # them. CLI must classify them for the short-circuit / paste-rejection paths.
    assert auth.is_retired_user_token("stk_user_abc123")
    assert auth.is_retired_user_token("sct_user_abc123")


def test_is_retired_user_token_rejects_other_prefixes():
    assert not auth.is_retired_user_token("tb_user_abc123")  # current canonical
    assert not auth.is_retired_user_token("stk_abc123")
    assert not auth.is_retired_user_token("toolkit_abc123")
    assert not auth.is_retired_user_token("")


def test_stored_token_is_retired_true_for_retired_prefix(tmp_path: Path):
    p = tmp_path / "token"
    p.write_text("stk_user_stale")
    assert auth.stored_token_is_retired(path=p)


def test_stored_token_is_retired_false_for_fresh_token(tmp_path: Path):
    p = tmp_path / "token"
    p.write_text("tb_user_fresh")
    assert not auth.stored_token_is_retired(path=p)


def test_stored_token_is_retired_false_for_missing_file(tmp_path: Path):
    assert not auth.stored_token_is_retired(path=tmp_path / "absent")


def test_is_legacy_toolkit_token_accepts_both_prefixes():
    assert auth.is_legacy_toolkit_token("stk_abc123")
    assert auth.is_legacy_toolkit_token("toolkit_abc123")


def test_is_legacy_toolkit_token_rejects_user_token():
    # Both forms of per-user prefix must not be classified as
    # per-toolkit (the two tracks are separate; per-toolkit
    # classifier excludes tb_user_ explicitly).
    assert not auth.is_legacy_toolkit_token("tb_user_abc")
    assert not auth.is_legacy_toolkit_token("tb_user_abc")


# ── per-user token storage ────────────────────────────────────────────


def test_save_and_load_user_token(tmp_path: Path):
    p = tmp_path / "token"
    auth.save_user_token("tb_user_abc", path=p)
    assert auth.load_user_token(path=p) == "tb_user_abc"


def test_save_user_token_strips_whitespace(tmp_path: Path):
    p = tmp_path / "token"
    auth.save_user_token("  tb_user_abc\n", path=p)
    assert p.read_text() == "tb_user_abc"


def test_save_user_token_sets_0600_on_posix(tmp_path: Path):
    p = tmp_path / "token"
    auth.save_user_token("tb_user_abc", path=p)
    import os
    import stat
    mode = stat.S_IMODE(os.stat(p).st_mode)
    # On POSIX should be 0o600. On Windows the chmod silently no-ops;
    # skip the assertion there.
    if os.name == "posix":
        assert mode == 0o600


def test_load_user_token_missing_returns_none(tmp_path: Path):
    assert auth.load_user_token(path=tmp_path / "no-such-file") is None


def test_load_user_token_empty_returns_none(tmp_path: Path):
    p = tmp_path / "token"
    p.write_text("")
    assert auth.load_user_token(path=p) is None


def test_delete_user_token_removes_file(tmp_path: Path):
    p = tmp_path / "token"
    p.write_text("tb_user_abc")
    assert auth.delete_user_token(path=p) is True
    assert not p.exists()


def test_delete_user_token_missing_returns_false(tmp_path: Path):
    assert auth.delete_user_token(path=tmp_path / "no-such-file") is False


# ── legacy per-toolkit token storage ──────────────────────────────────


def test_legacy_token_path_is_per_toolkit(tmp_path: Path):
    expected = tmp_path / "aster" / "token"
    assert auth.legacy_token_path("aster", base=tmp_path) == expected


def test_save_and_load_legacy_token(tmp_path: Path):
    auth.save_legacy_toolkit_token("aster", "stk_abc", base=tmp_path)
    assert auth.load_legacy_toolkit_token("aster", base=tmp_path) == "stk_abc"


def test_load_legacy_token_missing_returns_none(tmp_path: Path):
    assert auth.load_legacy_toolkit_token("ghost", base=tmp_path) is None


def test_find_legacy_token_files_lists_each_toolkit(tmp_path: Path):
    auth.save_legacy_toolkit_token("aster", "stk_a", base=tmp_path)
    auth.save_legacy_toolkit_token("heptapod", "stk_h", base=tmp_path)
    found = auth.find_legacy_token_files(base=tmp_path)
    names = {n for n, _ in found}
    assert names == {"aster", "heptapod"}


def test_find_legacy_token_files_skips_known_subdirs(tmp_path: Path):
    """toolkits/, logs/, config/ shouldn't be confused with toolkit names."""
    (tmp_path / "toolkits" / "ghost").mkdir(parents=True)
    (tmp_path / "toolkits" / "ghost" / "token").write_text("not-a-real-token")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "token").write_text("ghost")
    auth.save_legacy_toolkit_token("aster", "stk_a", base=tmp_path)

    found = auth.find_legacy_token_files(base=tmp_path)
    names = {n for n, _ in found}
    assert names == {"aster"}


def test_find_legacy_token_files_empty_base(tmp_path: Path):
    assert auth.find_legacy_token_files(base=tmp_path / "missing") == []


def test_delete_legacy_token_files_removes_all(tmp_path: Path):
    auth.save_legacy_toolkit_token("aster", "stk_a", base=tmp_path)
    auth.save_legacy_toolkit_token("heptapod", "stk_h", base=tmp_path)

    removed = auth.delete_legacy_token_files(base=tmp_path)
    assert sorted(removed) == ["aster", "heptapod"]
    assert auth.find_legacy_token_files(base=tmp_path) == []


# ── publish-time resolution ──────────────────────────────────────────


def test_load_token_for_publish_prefers_user(tmp_path: Path):
    user_path = tmp_path / "token"
    auth.save_user_token("tb_user_x", path=user_path)
    auth.save_legacy_toolkit_token("aster", "stk_a", base=tmp_path)

    token, source = auth.load_token_for_publish(
        "aster", base=tmp_path, user_path=user_path
    )
    assert token == "tb_user_x"
    assert source == "user"


def test_load_token_for_publish_falls_back_to_legacy(tmp_path: Path):
    user_path = tmp_path / "token"  # not created
    auth.save_legacy_toolkit_token("aster", "stk_a", base=tmp_path)

    token, source = auth.load_token_for_publish(
        "aster", base=tmp_path, user_path=user_path
    )
    assert token == "stk_a"
    assert source == "legacy"


def test_load_token_for_publish_returns_none_when_nothing_stored(tmp_path: Path):
    user_path = tmp_path / "token"
    token, source = auth.load_token_for_publish(
        "aster", base=tmp_path, user_path=user_path
    )
    assert token is None
    assert source == "none"


# ── BrowserFlow ───────────────────────────────────────────────────────


def _post_callback(callback_url: str, body: dict, *, timeout: float = 5.0) -> int:
    """Helper: POST a JSON body to the loopback callback.

    Returns the HTTP status. Raises on connection failure.
    """
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        callback_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def _drive_browser_flow(
    flow: auth.BrowserFlow, payload_factory,
) -> auth.BrowserFlowResult:
    """Run flow.run() while a side-thread POSTs the callback.

    ``payload_factory`` is a callable receiving the captured callback URL
    and returning the JSON body to POST.
    """
    captured_url: list[str] = []

    def fake_open(url: str) -> bool:
        # Extract the callback= URL param and remember it for the side
        # thread. webbrowser.open returns True on success.
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(url).query)
        captured_url.append(qs["callback"][0])
        return True

    flow._open_browser = fake_open  # type: ignore[attr-defined]

    poster_done = threading.Event()
    poster_error: list[str] = []

    def poster():
        # Wait until fake_open was called (so we have the callback URL).
        deadline = time.monotonic() + 5.0
        while not captured_url and time.monotonic() < deadline:
            time.sleep(0.01)
        if not captured_url:
            poster_error.append("browser open never fired")
            poster_done.set()
            return
        # Wait briefly for the server to bind the listen socket.
        time.sleep(0.05)
        try:
            import urllib.error
            try:
                payload = payload_factory(captured_url[0])
                _post_callback(captured_url[0], payload)
            except urllib.error.HTTPError:
                # Non-2xx responses from the callback are fine for the
                # purposes of these tests — the test asserts on the
                # ``BrowserFlowResult``, not on the HTTP status. The
                # state-mismatch test specifically expects a 400.
                pass
        except Exception as e:  # pragma: no cover (debug aid)
            poster_error.append(f"poster failed: {e}")
        finally:
            poster_done.set()

    t = threading.Thread(target=poster, daemon=True)
    t.start()

    result = flow.run()
    poster_done.wait(timeout=5.0)
    if poster_error:
        pytest.fail(poster_error[0])
    return result


def test_browser_flow_happy_path():
    flow = auth.BrowserFlow(timeout_s=10.0)

    def make_payload(_callback_url: str) -> dict:
        return {"state": flow.state, "token": "tb_user_happy"}

    result = _drive_browser_flow(flow, make_payload)
    assert result.token == "tb_user_happy"
    assert not result.denied
    assert not result.timed_out
    assert result.error is None


def test_browser_flow_denied():
    flow = auth.BrowserFlow(timeout_s=10.0)

    def make_payload(_callback_url: str) -> dict:
        return {"state": flow.state, "denied": True}

    result = _drive_browser_flow(flow, make_payload)
    assert result.denied
    assert result.token is None
    assert result.error is None


def test_browser_flow_state_mismatch_rejected():
    """A callback with a wrong state must NOT populate token."""
    flow = auth.BrowserFlow(timeout_s=10.0)

    def make_payload(_callback_url: str) -> dict:
        return {"state": "WRONG_STATE", "token": "tb_user_should_not_land"}

    result = _drive_browser_flow(flow, make_payload)
    assert result.token is None
    assert "state mismatch" in (result.error or "")


def test_browser_flow_timeout():
    """No callback ever arrives → timed_out=True, no token."""
    flow = auth.BrowserFlow(timeout_s=0.2)
    flow._open_browser = lambda url: True  # type: ignore
    result = flow.run()
    assert result.timed_out
    assert result.token is None


def test_browser_flow_url_includes_state_and_callback():
    flow = auth.BrowserFlow(web_base="https://example.test")
    url = flow.build_auth_url("http://127.0.0.1:1234/cli-callback")
    assert url.startswith("https://example.test/cli-auth?")
    assert "state=" in url
    assert flow.state in url
    assert "127.0.0.1%3A1234" in url  # url-encoded


def test_browser_flow_preview_does_not_bind():
    """auth_url_preview must not bind any port (cheap to call)."""
    flow = auth.BrowserFlow()
    url = flow.auth_url_preview
    assert url is not None
    # Port is "0" in the placeholder; URL-encoded the colon becomes %3A.
    assert "%3A0%2F" in url  # 127.0.0.1:0/cli-callback url-encoded


def test_browser_flow_pick_free_port_returns_int():
    port = auth.BrowserFlow._pick_free_port()
    assert isinstance(port, int)
    assert 1024 < port < 65536


def test_browser_flow_options_returns_cors_headers():
    """OPTIONS preflight must reply with the expected CORS headers."""
    import urllib.request

    flow = auth.BrowserFlow(web_base="https://tool-base.org", timeout_s=10.0)
    flow._open_browser = lambda url: True  # type: ignore

    captured: list[str] = []

    # Run flow on a thread; we'll send OPTIONS then a (denial) POST to
    # let it shut down cleanly.
    def runner():
        captured.append(flow.run().error or "")

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    # We need the actual callback URL. Wait for the server to bind by
    # repeatedly trying a connection on the bound port. Easier: peek at
    # the server's bound port via ``flow._httpd``.
    deadline = time.monotonic() + 2.0
    while flow._httpd is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert flow._httpd is not None
    port = flow._httpd.server_port
    callback_url = f"http://127.0.0.1:{port}/cli-callback"

    # Send OPTIONS preflight.
    req = urllib.request.Request(callback_url, method="OPTIONS")
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "https://tool-base.org"
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")

    # End the flow with a denial so the test thread exits.
    _post_callback(callback_url, {"state": flow.state, "denied": True})
    t.join(timeout=3.0)


# ── API helpers ──────────────────────────────────────────────────────


def test_whoami_returns_dict_on_200():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "uid": "u123", "email": "alice@example.com",
        "name": "Alice", "auth_method": "cli_token",
    }
    with patch("requests.get", return_value=fake_response):
        info = auth.whoami("tb_user_x")
    assert info is not None
    assert info["email"] == "alice@example.com"


def test_whoami_returns_none_on_non_200():
    fake_response = MagicMock()
    fake_response.status_code = 401
    with patch("requests.get", return_value=fake_response):
        info = auth.whoami("bad-token")
    assert info is None


def test_whoami_returns_none_on_network_error():
    import requests as _requests

    with patch(
        "requests.get",
        side_effect=_requests.exceptions.ConnectionError("dns fail"),
    ):
        info = auth.whoami("tb_user_x")
    assert info is None


def test_revoke_token_returns_true_on_2xx():
    fake_response = MagicMock()
    fake_response.status_code = 204
    with patch("requests.delete", return_value=fake_response):
        ok = auth.revoke_token("tok_id_1", "tb_user_x")
    assert ok is True


def test_revoke_token_returns_false_on_failure():
    fake_response = MagicMock()
    fake_response.status_code = 404
    with patch("requests.delete", return_value=fake_response):
        ok = auth.revoke_token("tok_id_1", "tb_user_x")
    assert ok is False


def test_revoke_token_returns_false_on_network_error():
    import requests as _requests

    with patch(
        "requests.delete",
        side_effect=_requests.exceptions.ConnectionError("dns fail"),
    ):
        ok = auth.revoke_token("tok_id_1", "tb_user_x")
    assert ok is False


# ── default-path safety ──────────────────────────────────────────────


def test_user_token_path_is_under_config_dir():
    """Sanity: USER_TOKEN_PATH lives directly under CONFIG_DIR, not in a subdir.

    Pinning this prevents an accidental rename of the canonical location
    breaking publish-flow token resolution silently.
    """
    from toolbase.config import CONFIG_DIR
    assert auth.USER_TOKEN_PATH == CONFIG_DIR / "token"
