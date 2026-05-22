"""Integration tests for `toolbase publish` auto-create on 404.

Closes GitHub issue #5. Before 0.5.5, running ``publish`` against a
toolkit name that hadn't been registered yet would fail at upload time
with an opaque ``Toolkit '<name>' not found`` 404. Users were expected
to run ``toolbase create`` first, but the CLI's own ``init`` scaffold
output didn't make that clear, so they hit the 404 and concluded
publish was broken.

The 0.5.5 fix detects "not yet registered" during the pre-flight GET
to ``/api/toolkits/<name>``, prompts the user (using metadata from
toolkit.yaml), POSTs to ``/api/toolkits`` to register the row, and
then proceeds to upload. ``toolbase create`` is preserved as a
standalone command for the name-reservation use case.

These tests pin the new behavior via Click's CliRunner with the
registry HTTP calls mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import requests
import yaml
from click.testing import CliRunner

from toolbase import cli


# ── helpers ────────────────────────────────────────────────────────────────


def _make_minimal_toolkit(
    tmp_path: Path,
    name: str,
    version: str = "0.1.0",
    *,
    category: str = "utils",
    description: str = "test toolkit",
) -> Path:
    tk = tmp_path / name
    tk.mkdir()
    (tk / "toolkit.yaml").write_text(
        yaml.safe_dump({
            "name": name,
            "version": version,
            "description": description,
            "author": "tester",
            "category": category,
            "tools": [
                {"name": "noop", "function": "tools.noop", "description": "x"},
            ],
        })
    )
    (tk / "tools").mkdir()
    (tk / "tools" / "__init__.py").write_text("from .noop import noop\n")
    (tk / "tools" / "noop.py").write_text("def noop():\n    return '{}'\n")
    (tk / "mcp").mkdir()
    (tk / "mcp" / "__init__.py").write_text("")
    (tk / "mcp" / "server_stdio.py").write_text("")
    (tk / "requirements.txt").write_text("orchestral-ai>=1.0.0\n")
    return tk


def _make_response(status: int, json_body=None, text: str = ""):
    resp = mock.Mock()
    resp.status_code = status
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text or (str(json_body) if json_body else "")
    return resp


def _seed_user_token(tmp_path: Path, monkeypatch) -> Path:
    """Drop an ``tb_user_*`` token at the user-token path."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    fake_config = fake_home / ".toolbase"
    fake_config.mkdir()
    token_path = fake_config / "token"
    token_path.write_text("tb_user_fake_for_tests")
    from toolbase import config as cfg
    from toolbase import auth as auth_mod
    monkeypatch.setattr(cfg, "CONFIG_DIR", fake_config)
    monkeypatch.setattr(auth_mod, "USER_TOKEN_PATH", token_path)
    return token_path


# ── tests ──────────────────────────────────────────────────────────────────


def test_publish_auto_registers_on_404_and_uploads(tmp_path, monkeypatch):
    """Happy path: pre-flight 404 → registration prompt -y → POST 200 → upload 201."""
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0", category="utils")
    monkeypatch.chdir(tk)
    _seed_user_token(tmp_path, monkeypatch)

    get_calls = []
    post_calls = []

    def fake_get(url, *a, **kw):
        get_calls.append(url)
        return _make_response(404, json_body={"detail": "not found"})

    def fake_post(url, *a, **kw):
        post_calls.append((url, kw))
        if url.endswith("/api/toolkits"):
            return _make_response(201, json_body={"id": 1, "name": "demo"})
        if url.endswith("/api/toolkits/demo/publish"):
            return _make_response(201, json_body={
                "toolkit_name": "demo",
                "version": "0.1.0",
                "file_size": 1024,
                "published_at": "2026-05-15T00:00:00Z",
            })
        raise AssertionError(f"unexpected POST: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    result = CliRunner().invoke(
        cli.main, ["publish", "-y"], catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "not yet registered" in result.output
    assert "Registered toolkit" in result.output
    assert "Successfully published" in result.output

    # Asserted call shape: registration body matches toolkit.yaml metadata.
    create_call = next(c for c in post_calls if c[0].endswith("/api/toolkits"))
    body = create_call[1].get("json")
    assert body == {
        "name": "demo",
        "category": "utils",
        "description": "test toolkit",
        "version": "0.1.0",
    }


def test_publish_auto_register_user_declines(tmp_path, monkeypatch):
    """Pre-flight 404 → registration prompt --no → exit 1, no POST."""
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tk)
    _seed_user_token(tmp_path, monkeypatch)

    monkeypatch.setattr(
        requests, "get",
        lambda *a, **kw: _make_response(404, json_body={"detail": "not found"}),
    )

    post_calls = []

    def fake_post(url, *a, **kw):
        post_calls.append(url)
        raise AssertionError("publish should not POST when user declines")

    monkeypatch.setattr(requests, "post", fake_post)

    result = CliRunner().invoke(
        cli.main, ["publish", "--no"], catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "declined" in result.output.lower() or "not registered" in result.output.lower()
    assert post_calls == []


def test_publish_auto_register_name_taken(tmp_path, monkeypatch):
    """Pre-flight 404 → POST returns 409 (taken by other user) → clear error."""
    tk = _make_minimal_toolkit(tmp_path, "heptapod", "1.0.0", category="hep")
    monkeypatch.chdir(tk)
    _seed_user_token(tmp_path, monkeypatch)

    monkeypatch.setattr(
        requests, "get",
        lambda *a, **kw: _make_response(404, json_body={"detail": "not found"}),
    )

    monkeypatch.setattr(
        requests, "post",
        lambda url, *a, **kw: _make_response(409, json_body={"detail": "taken"}),
    )

    result = CliRunner().invoke(
        cli.main, ["publish", "-y"], catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "already taken" in result.output
    assert "different name" in result.output


def test_publish_auto_register_then_upload_fails(tmp_path, monkeypatch):
    """Pre-flight 404 → POST 201 (registered) → upload 5xx → registered-but-empty hint."""
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tk)
    _seed_user_token(tmp_path, monkeypatch)

    monkeypatch.setattr(
        requests, "get",
        lambda *a, **kw: _make_response(404, json_body={"detail": "not found"}),
    )

    def fake_post(url, *a, **kw):
        if url.endswith("/api/toolkits"):
            return _make_response(201, json_body={"id": 1, "name": "demo"})
        if url.endswith("/api/toolkits/demo/publish"):
            return _make_response(503, json_body={"detail": "storage down"})
        raise AssertionError(f"unexpected POST: {url}")

    monkeypatch.setattr(requests, "post", fake_post)

    result = CliRunner().invoke(
        cli.main, ["publish", "-y"], catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Upload failed" in result.output
    assert "just\nregistered" in result.output or "just registered" in result.output
    assert "no need to register again" in result.output


def test_publish_happy_path_no_auto_register_when_registered(tmp_path, monkeypatch):
    """200 from pre-flight GET → no registration prompt, just upload."""
    tk = _make_minimal_toolkit(tmp_path, "demo", "0.2.0")
    monkeypatch.chdir(tk)
    _seed_user_token(tmp_path, monkeypatch)

    # Pre-flight returns a registered toolkit with v0.1.0 published.
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **kw: _make_response(200, json_body={
            "name": "demo",
            "latest_version": "0.1.0",
            "versions": [{"version": "0.1.0"}],
        }),
    )

    post_calls = []

    def fake_post(url, *a, **kw):
        post_calls.append(url)
        if url.endswith("/api/toolkits"):
            raise AssertionError(
                "should not auto-register when toolkit is already registered"
            )
        return _make_response(201, json_body={
            "toolkit_name": "demo",
            "version": "0.2.0",
            "file_size": 1024,
            "published_at": "2026-05-15T00:00:00Z",
        })

    monkeypatch.setattr(requests, "post", fake_post)

    result = CliRunner().invoke(
        cli.main, ["publish", "-y"], catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "not yet registered" not in result.output
    assert "Registered toolkit" not in result.output
    assert "Successfully published" in result.output


def test_publish_auto_register_missing_category_fails_loudly(tmp_path, monkeypatch):
    """toolkit.yaml without 'category' → can't auto-register; clear error."""
    tk = tmp_path / "demo"
    tk.mkdir()
    (tk / "toolkit.yaml").write_text(
        yaml.safe_dump({
            "name": "demo",
            "version": "0.1.0",
            "description": "no category here",
            "author": "tester",
            # category intentionally absent
            "tools": [
                {"name": "noop", "function": "tools.noop", "description": "x"},
            ],
        })
    )
    (tk / "tools").mkdir()
    (tk / "tools" / "__init__.py").write_text("from .noop import noop\n")
    (tk / "tools" / "noop.py").write_text("def noop():\n    return '{}'\n")
    (tk / "mcp").mkdir()
    (tk / "mcp" / "__init__.py").write_text("")
    (tk / "mcp" / "server_stdio.py").write_text("")
    (tk / "requirements.txt").write_text("orchestral-ai>=1.0.0\n")
    monkeypatch.chdir(tk)
    _seed_user_token(tmp_path, monkeypatch)

    monkeypatch.setattr(
        requests, "get",
        lambda *a, **kw: _make_response(404, json_body={"detail": "not found"}),
    )

    # validate_toolkit may reject the missing category before we even
    # reach the auto-register branch. Either way, the exit must be 1 —
    # what matters here is that we don't silently call POST /api/toolkits
    # with an empty category and pollute the registry.
    post_calls = []
    monkeypatch.setattr(
        requests, "post",
        lambda url, *a, **kw: post_calls.append(url) or _make_response(
            201, json_body={"id": 1, "name": "demo"},
        ),
    )

    result = CliRunner().invoke(
        cli.main, ["publish", "-y"], catch_exceptions=False,
    )

    assert result.exit_code == 1
    # No POST to /api/toolkits with empty category — either we never
    # got there (validate caught it), or we explicitly refused.
    assert not any(url.endswith("/api/toolkits") for url in post_calls)
