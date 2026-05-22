"""Unit tests for ``toolbase create``.

The command hits ``POST /api/toolkits`` against the registry; tests
mock ``requests.post`` so we exercise the CLI's flag handling, auth
resolution, validation, and response handling without network.

Cover:

- Happy path: writes the right body, prints next-step pointers,
  exit 0.
- No token → exit 1 with "log in first" pointer.
- Invalid category → rejected locally (no network call).
- Invalid name format → rejected locally.
- Description too long → rejected locally.
- Backend 401 / 409 / 422 / 500 each handled with a clear error
  message and non-zero exit.
- Network failure (requests exception) → exit 1.
- Returned legacy token is NOT persisted to disk (intentional —
  the per-user CLI token already covers publish).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from toolbase.cli import main


@pytest.fixture(autouse=True)
def _redirect_user_token(tmp_path, monkeypatch):
    """Stub the user-token path so tests don't read the developer's real
    ``~/.toolbase/token``.
    """
    fake_home = tmp_path / "fake-home"
    (fake_home / ".toolbase").mkdir(parents=True)
    monkeypatch.setattr(
        "toolbase.auth._resolve_user_token_path",
        lambda: fake_home / ".toolbase" / "token",
    )
    monkeypatch.setattr(
        "toolbase.auth._resolve_config_dir",
        lambda: fake_home / ".toolbase",
    )
    monkeypatch.setenv("TOOLBASE_API_URL", "https://test.example.com")
    return fake_home


def _login(fake_home: Path, token: str = "tb_user_FAKETOKEN") -> None:
    (fake_home / ".toolbase" / "token").write_text(token, encoding="utf-8")


def _mk_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "" if body is None else str(body)
    resp.json.return_value = body or {}
    return resp


class TestAuth:
    def test_no_token_exits_with_pointer(self, _redirect_user_token):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "create", "my-tk",
                "--category", "astro",
                "--description", "A toolkit",
                "--no-input",
            ],
        )
        assert result.exit_code == 1
        assert "Not logged in" in result.output
        assert "toolbase login" in result.output

    def test_401_response_mentions_login(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch("requests.post", return_value=_mk_response(401)):
            result = runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        assert result.exit_code == 1
        assert "Token rejected" in result.output


class TestLocalValidation:
    def test_invalid_category_rejected_locally(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch("requests.post") as post:
            result = runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "not-a-real-category",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        assert result.exit_code == 1
        assert "Invalid category" in result.output
        post.assert_not_called()

    def test_invalid_name_rejected_locally(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch("requests.post") as post:
            result = runner.invoke(
                main,
                [
                    "create", "Bad Name With Spaces",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        assert result.exit_code == 1
        assert "Invalid toolkit name" in result.output
        post.assert_not_called()

    def test_short_name_rejected_locally(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch("requests.post") as post:
            result = runner.invoke(
                main,
                [
                    "create", "ab",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        assert result.exit_code == 1
        assert "at least 3 characters" in result.output
        post.assert_not_called()

    def test_long_description_rejected_locally(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch("requests.post") as post:
            result = runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "x" * 201,
                    "--no-input",
                ],
            )
        assert result.exit_code == 1
        assert "Description too long" in result.output
        post.assert_not_called()


class TestHappyPath:
    def test_writes_correct_body(self, _redirect_user_token):
        _login(_redirect_user_token, token="tb_user_FAKE")
        runner = CliRunner()
        ok_payload = {"id": "tk-uuid", "name": "my-tk", "token": "stk_legacy"}
        with patch("requests.post", return_value=_mk_response(201, ok_payload)) as post:
            result = runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "Astro toolkit.",
                    "--no-input",
                ],
            )
        assert result.exit_code == 0, result.output
        call = post.call_args
        url = call.args[0]
        assert "/api/toolkits" in url
        body = call.kwargs["json"]
        assert body["name"] == "my-tk"
        assert body["category"] == "astro"
        assert body["description"] == "Astro toolkit."
        assert body["version"] == "0.1.0"
        headers = call.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tb_user_FAKE"

    def test_prints_next_step_pointers(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch(
            "requests.post",
            return_value=_mk_response(
                201, {"id": "x", "name": "my-tk", "token": "y"}
            ),
        ):
            result = runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        assert result.exit_code == 0
        assert "toolbase init" in result.output
        assert "toolbase ingest" in result.output

    def test_does_not_persist_returned_legacy_token(
        self, _redirect_user_token
    ):
        _login(_redirect_user_token, token="tb_user_FAKE")
        runner = CliRunner()
        ok_payload = {"id": "x", "name": "my-tk", "token": "stk_legacy_FAKE"}
        with patch("requests.post", return_value=_mk_response(201, ok_payload)):
            runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        # Per-user token at fake_home/.toolbase/token is unchanged.
        assert (
            (_redirect_user_token / ".toolbase" / "token").read_text()
            == "tb_user_FAKE"
        )
        # No legacy per-toolkit token file was written.
        legacy = _redirect_user_token / ".toolbase" / "my-tk" / "token"
        assert not legacy.exists()

    def test_organization_passed_through(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch(
            "requests.post",
            return_value=_mk_response(
                201, {"id": "x", "name": "my-tk", "token": "y"}
            ),
        ) as post:
            runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--organization", "my-org",
                    "--no-input",
                ],
            )
        body = post.call_args.kwargs["json"]
        assert body.get("organization") == "my-org"

    def test_organization_omitted_when_not_set(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch(
            "requests.post",
            return_value=_mk_response(
                201, {"id": "x", "name": "my-tk", "token": "y"}
            ),
        ) as post:
            runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        body = post.call_args.kwargs["json"]
        assert "organization" not in body


class TestBackendErrors:
    def _run_with_status(self, fake_home, status, body=None):
        _login(fake_home)
        runner = CliRunner()
        with patch("requests.post", return_value=_mk_response(status, body)):
            return runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )

    def test_409_name_taken(self, _redirect_user_token):
        result = self._run_with_status(_redirect_user_token, 409)
        assert result.exit_code == 1
        assert "already taken" in result.output

    def test_422_validation_error(self, _redirect_user_token):
        result = self._run_with_status(
            _redirect_user_token, 422,
            body={"detail": "category must be one of [...]"},
        )
        assert result.exit_code == 1
        assert "Registry rejected" in result.output
        assert "category must be one of" in result.output

    def test_5xx_surfaced(self, _redirect_user_token):
        result = self._run_with_status(_redirect_user_token, 503)
        assert result.exit_code == 1
        assert "503" in result.output

    def test_network_exception(self, _redirect_user_token):
        _login(_redirect_user_token)
        import requests as rq
        runner = CliRunner()
        with patch("requests.post", side_effect=rq.exceptions.ConnectionError("nope")):
            result = runner.invoke(
                main,
                [
                    "create", "my-tk",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        assert result.exit_code == 1
        assert "Could not reach registry" in result.output


class TestNameNormalization:
    def test_uppercase_name_lowered_before_send(self, _redirect_user_token):
        _login(_redirect_user_token)
        runner = CliRunner()
        with patch(
            "requests.post",
            return_value=_mk_response(
                201, {"id": "x", "name": "my-tk", "token": "y"}
            ),
        ) as post:
            result = runner.invoke(
                main,
                [
                    "create", "MY-TK",
                    "--category", "astro",
                    "--description", "A toolkit",
                    "--no-input",
                ],
            )
        assert result.exit_code == 0
        body = post.call_args.kwargs["json"]
        assert body["name"] == "my-tk"
