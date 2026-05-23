"""Integration tests for the rewritten ``login`` / ``logout`` / ``whoami``
commands.

These exercise the Click command surface (via ``CliRunner``) with the
filesystem and the browser-flow swapped out for predictable test
doubles. The browser-flow's loopback HTTP server is exercised in
``test_auth.py``; here we mock the ``BrowserFlow`` class so we can
drive the CLI's branching without binding sockets in every test.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from toolbase import auth, cli


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect CONFIG_DIR + USER_TOKEN_PATH to a tmp_path subtree.

    Patches both ``toolbase.config.CONFIG_DIR`` (the canonical source)
    and the ``auth`` module's re-exports. The auth module's
    ``_resolve_config_dir()`` re-reads from ``config.CONFIG_DIR`` at
    call time so the canonical patch is what makes legacy-token helpers
    resolve to tmp_path; the others are belt-and-suspenders.
    """
    from toolbase import config as cfg

    fake_config = tmp_path / "toolbase"
    fake_config.mkdir()
    # Canonical: this is what _resolve_config_dir() re-reads on each call.
    monkeypatch.setattr(cfg, "CONFIG_DIR", fake_config)
    # auth module's import-time bindings, also patched so any code that
    # reads them directly (rather than via the resolvers) still wins.
    monkeypatch.setattr(auth, "CONFIG_DIR", fake_config)
    monkeypatch.setattr(auth, "USER_TOKEN_PATH", fake_config / "token")
    return fake_config


# ── login: legacy per-toolkit form ────────────────────────────────────


def test_login_legacy_with_token_flag(isolated_config: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "aster", "--token", "stk_legacy_abc"],
    )
    assert result.exit_code == 0, result.output
    legacy = isolated_config / "aster" / "token"
    assert legacy.exists()
    assert legacy.read_text() == "stk_legacy_abc"
    # Deprecation hint surfaced.
    assert "deprecat" in result.output.lower() or "phased out" in result.output.lower()


def test_login_legacy_accepts_old_toolkit_prefix(isolated_config: Path):
    """Pre-MVP tokens used 'toolkit_' prefix; we still accept them."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "aster", "--token", "toolkit_abc"],
    )
    assert result.exit_code == 0, result.output
    assert (isolated_config / "aster" / "token").read_text() == "toolkit_abc"


def test_login_legacy_rejects_user_token_pasted_into_legacy_form(isolated_config: Path):
    """A per-user token in the legacy form should error with guidance."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "aster", "--token", "tb_user_abc"],
    )
    assert result.exit_code == 1
    assert "per-user token" in result.output.lower()
    # No file should have been written.
    assert not (isolated_config / "aster" / "token").exists()


def test_login_legacy_rejects_retired_user_token_pasted_into_legacy_form(
    isolated_config: Path,
):
    """A retired per-user token in the legacy form errors with the
    stale-token migration message (and writes nothing)."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "aster", "--token", "stk_user_old"],
    )
    assert result.exit_code == 1
    # Stale-token message references the recovery flow.
    assert "tb_user_" in result.output
    assert "toolbase logout" in result.output
    assert "toolbase login" in result.output
    assert not (isolated_config / "aster" / "token").exists()


def test_login_legacy_unknown_prefix_rejected_in_no_mode(isolated_config: Path):
    """An unrecognized prefix triggers a confirmation; --no rejects it."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "aster", "--token", "weird_xyz", "--no"],
    )
    assert result.exit_code == 0  # confirmed-no exits cleanly per existing UX
    # But no file should have been written.
    assert not (isolated_config / "aster" / "token").exists()


# ── login: per-user paste mode ───────────────────────────────────────


def test_login_paste_user_token(isolated_config: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "--token", "tb_user_abc"],
    )
    assert result.exit_code == 0, result.output
    assert auth.load_user_token() == "tb_user_abc"


def test_login_paste_rejects_legacy_token(isolated_config: Path):
    """A legacy stk_/toolkit_ token in the new form errors with guidance."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "--token", "stk_legacy_xx"],
    )
    assert result.exit_code == 1
    assert "legacy" in result.output.lower() or "per-toolkit" in result.output.lower()
    assert auth.load_user_token() is None


def test_login_paste_unknown_prefix_with_no_flag(isolated_config: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "--token", "weird_xx", "--no"],
    )
    assert result.exit_code == 1  # consequential prompt + --no aborts
    assert auth.load_user_token() is None


# ── login: browser-flow + migration ──────────────────────────────────


class _FakeFlow:
    """Stand-in for auth.BrowserFlow that returns a canned result."""

    def __init__(self, result: auth.BrowserFlowResult, **_kwargs):
        self._result = result
        self.state = "fake-state"
        self.web_base = _kwargs.get("web_base", "https://toolbase-ai.com")

    def run(self) -> auth.BrowserFlowResult:
        return self._result


def _patch_browser_flow(monkeypatch, result: auth.BrowserFlowResult):
    """Inject _FakeFlow as the BrowserFlow class for the duration of a test."""
    def factory(**kwargs):
        return _FakeFlow(result, **kwargs)
    monkeypatch.setattr(auth, "BrowserFlow", factory)


def test_login_browser_flow_happy_path(isolated_config: Path, monkeypatch):
    _patch_browser_flow(
        monkeypatch,
        auth.BrowserFlowResult(token="tb_user_browser"),
    )
    runner = CliRunner()
    # Force interactive mode so the flow doesn't get short-circuited
    # by skip-mode (CliRunner's stdin is non-TTY, which is normally
    # auto-detected as skip).
    result = runner.invoke(cli.main, ["login", "--yes"])
    assert result.exit_code == 0, result.output
    assert auth.load_user_token() == "tb_user_browser"


def test_login_browser_flow_denied(isolated_config: Path, monkeypatch):
    _patch_browser_flow(
        monkeypatch,
        auth.BrowserFlowResult(denied=True),
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["login", "--yes"])
    assert result.exit_code == 1
    assert "denied" in result.output.lower()
    assert auth.load_user_token() is None


def test_login_browser_flow_timeout(isolated_config: Path, monkeypatch):
    _patch_browser_flow(
        monkeypatch,
        auth.BrowserFlowResult(timed_out=True),
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["login", "--yes"])
    assert result.exit_code == 1
    assert "timed out" in result.output.lower()


def test_login_browser_flow_state_mismatch_error(isolated_config: Path, monkeypatch):
    _patch_browser_flow(
        monkeypatch,
        auth.BrowserFlowResult(error="state mismatch (possible CSRF)"),
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["login", "--yes"])
    assert result.exit_code == 1
    assert "state mismatch" in result.output.lower()


def test_login_browser_flow_returns_malformed_token_rejected(
    isolated_config: Path, monkeypatch,
):
    """Defense-in-depth: if the website ever returns a non-tb_user_ token, refuse."""
    _patch_browser_flow(
        monkeypatch,
        auth.BrowserFlowResult(token="garbage"),
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["login", "--yes"])
    assert result.exit_code == 1
    assert "unexpected token format" in result.output.lower()
    assert auth.load_user_token() is None


def test_login_no_input_mode_blocks_browser_flow(isolated_config: Path):
    """In --no-input mode, browser-flow can't run; user gets a clear error."""
    runner = CliRunner()
    result = runner.invoke(cli.main, ["login", "--no-input"])
    assert result.exit_code != 0
    assert "non-interactive" in result.output.lower() or "--token" in result.output


def test_login_migration_prompt_detects_legacy_files(
    isolated_config: Path, monkeypatch,
):
    """When legacy tokens exist, login surfaces a migration prompt before browser-flow."""
    auth.save_legacy_toolkit_token("aster", "stk_a")
    auth.save_legacy_toolkit_token("heptapod", "stk_h")

    _patch_browser_flow(
        monkeypatch,
        auth.BrowserFlowResult(token="tb_user_consolidated"),
    )

    runner = CliRunner()
    # --yes accepts the migration prompt; the legacy files stay on disk
    # but the per-user token takes precedence at publish time.
    result = runner.invoke(cli.main, ["login", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Detected legacy per-toolkit tokens" in result.output
    assert "aster" in result.output and "heptapod" in result.output
    assert auth.load_user_token() == "tb_user_consolidated"
    # Legacy files preserved (cleanup is logout --clean-legacy's job).
    assert (isolated_config / "aster" / "token").exists()
    assert (isolated_config / "heptapod" / "token").exists()


def test_login_migration_prompt_user_declines_exits_zero(
    isolated_config: Path, monkeypatch,
):
    auth.save_legacy_toolkit_token("aster", "stk_a")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["login", "--no"])
    # --no on the migration prompt exits without running browser-flow.
    assert result.exit_code == 0
    assert auth.load_user_token() is None


# ── logout ─────────────────────────────────────────────────────────


def test_logout_removes_user_token(isolated_config: Path):
    auth.save_user_token("tb_user_x")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["logout"])
    assert result.exit_code == 0, result.output
    assert auth.load_user_token() is None


def test_logout_no_token_says_already_logged_out(isolated_config: Path):
    runner = CliRunner()
    result = runner.invoke(cli.main, ["logout"])
    assert result.exit_code == 0
    assert "already logged out" in result.output.lower()


def test_logout_no_user_but_legacy_present_hints_at_clean_legacy(
    isolated_config: Path,
):
    """If only legacy tokens exist, logout (no flag) suggests --clean-legacy."""
    auth.save_legacy_toolkit_token("aster", "stk_a")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["logout"])
    assert result.exit_code == 0
    assert "--clean-legacy" in result.output
    assert auth.load_legacy_toolkit_token("aster") == "stk_a"  # untouched


def test_logout_clean_legacy_removes_both(isolated_config: Path):
    auth.save_user_token("tb_user_x")
    auth.save_legacy_toolkit_token("aster", "stk_a")
    auth.save_legacy_toolkit_token("heptapod", "stk_h")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["logout", "--clean-legacy", "--yes"])
    assert result.exit_code == 0, result.output
    assert auth.load_user_token() is None
    assert auth.find_legacy_token_files(base=isolated_config) == []


def test_logout_clean_legacy_no_to_prompt_keeps_files(isolated_config: Path):
    """--clean-legacy --no aborts the cleanup confirmation."""
    auth.save_user_token("tb_user_x")
    auth.save_legacy_toolkit_token("aster", "stk_a")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["logout", "--clean-legacy", "--no"])
    assert result.exit_code == 0
    # User token IS removed (no confirmation needed). Legacy stays.
    assert auth.load_user_token() is None
    assert auth.load_legacy_toolkit_token("aster", base=isolated_config) == "stk_a"


# ── whoami ────────────────────────────────────────────────────────


def test_whoami_no_token_exits_nonzero(isolated_config: Path):
    runner = CliRunner()
    result = runner.invoke(cli.main, ["whoami"])
    assert result.exit_code == 1
    assert "not logged in" in result.output.lower()


def test_whoami_legacy_only_hints_at_consolidation(isolated_config: Path):
    auth.save_legacy_toolkit_token("aster", "stk_a")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["whoami"])
    assert result.exit_code == 1
    assert "legacy" in result.output.lower()
    assert "toolbase login" in result.output


def test_whoami_renders_user_info(isolated_config: Path, monkeypatch):
    auth.save_user_token("tb_user_x")
    monkeypatch.setattr(
        auth, "whoami",
        lambda token, **kw: {
            "uid": "u123",
            "email": "alice@example.com",
            "name": "Alice",
            "auth_method": "cli_token",
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "alice@example.com" in result.output
    assert "Alice" in result.output
    assert "cli_token" in result.output


def test_whoami_api_failure_surfaces_clearly(isolated_config: Path, monkeypatch):
    auth.save_user_token("tb_user_x")
    monkeypatch.setattr(auth, "whoami", lambda token, **kw: None)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["whoami"])
    assert result.exit_code == 1
    assert "could not reach" in result.output.lower() or "invalid" in result.output.lower()


# ── publish flow: token resolution ────────────────────────────────────


def test_publish_uses_user_token_when_available(isolated_config: Path, tmp_path: Path, monkeypatch):
    """Smoke test: publish flow loads per-user token and dies at network step."""
    auth.save_user_token("tb_user_x")
    auth.save_legacy_toolkit_token("demo", "stk_old")

    # Build a minimal toolkit dir to satisfy the up-front yaml + validate steps.
    project = tmp_path / "demo-toolkit"
    project.mkdir()
    (project / "toolkit.yaml").write_text(
        "name: demo\nversion: 0.1.0\ndescription: x\nauthor: a\ncategory: other\n"
        "tools:\n  - name: t\n    function: tools.t\n    description: d\n"
    )
    (project / "tools").mkdir()
    (project / "tools" / "__init__.py").write_text("from orchestral import define_tool\n@define_tool\ndef t() -> str:\n    return ''\nTOOLS = [t]\n")
    (project / "requirements.txt").write_text("")
    (project / "README.md").write_text("# demo\n")

    # Just check the resolution helper itself — running the full publish
    # flow brings in network calls we'd have to mock extensively. The
    # behavior tested here is "user token wins over legacy token."
    token, source = auth.load_token_for_publish("demo")
    assert source == "user"
    assert token == "tb_user_x"


def test_publish_falls_back_to_legacy_when_no_user_token(isolated_config: Path):
    auth.save_legacy_toolkit_token("demo", "stk_legacy")
    token, source = auth.load_token_for_publish("demo")
    assert token == "stk_legacy"
    assert source == "legacy"


def test_publish_no_token_returns_none(isolated_config: Path):
    token, source = auth.load_token_for_publish("ghost")
    assert token is None
    assert source == "none"


# ── 2026-05-15 prefix-rotation behaviors ──────────────────────────────
#
# The per-user token prefix evolved sct_user_ -> stk_user_ -> tb_user_.
# The CLI now (a) rejects the retired prefixes at paste-time before
# anything hits disk, and (b) short-circuits any authenticated command
# whose stored ~/.toolbase/token still uses a retired prefix,
# before any HTTP request.


def test_login_paste_rejects_retired_user_token(isolated_config: Path):
    """Paste-mode rejects a retired per-user token with the migration
    message, and does NOT write it to disk."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "--token", "stk_user_old"],
    )
    assert result.exit_code == 1
    # Migration message references the recovery path.
    assert "tb_user_" in result.output
    assert "toolbase login" in result.output
    # Critical: the stale token must NOT land on disk.
    assert auth.load_user_token() is None


def test_whoami_stale_token_short_circuits_before_http(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A stored retired-prefix token short-circuits ``whoami`` with the
    migration message before any HTTP request is made."""
    # Write the retired token directly (save_user_token rejects retired prefixes).
    auth._resolve_user_token_path().parent.mkdir(parents=True, exist_ok=True)
    auth._resolve_user_token_path().write_text("stk_user_stale")

    called = {"n": 0}

    def fake_whoami(*a, **kw):
        called["n"] += 1
        return {"email": "should-not-be-called@example.com"}

    monkeypatch.setattr(auth, "whoami", fake_whoami)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["whoami"])

    assert result.exit_code == 1
    assert called["n"] == 0, "whoami() should not be called when token is stale"
    assert "tb_user_" in result.output
    assert "toolbase logout" in result.output
    assert "toolbase login" in result.output


def test_create_stale_token_short_circuits_before_http(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A stored retired-prefix token short-circuits ``create`` before any
    requests.post call to the registry."""
    import requests as rq

    # Write the retired token directly (save_user_token rejects retired prefixes).
    auth._resolve_user_token_path().parent.mkdir(parents=True, exist_ok=True)
    auth._resolve_user_token_path().write_text("stk_user_stale")

    def fake_post(*a, **kw):
        raise AssertionError("requests.post should not be called")

    monkeypatch.setattr(rq, "post", fake_post)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["create", "tk-demo", "--category", "other",
         "--description", "x", "--no-input"],
    )
    assert result.exit_code == 1
    assert "tb_user_" in result.output


def test_login_paste_accepts_fresh_tb_user_token(isolated_config: Path):
    """Round-trip: tb_user_ token via --token paste stores cleanly."""
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "--token", "tb_user_fresh"],
    )
    assert result.exit_code == 0, result.output
    assert auth.load_user_token() == "tb_user_fresh"


def test_whoami_proceeds_with_fresh_tb_user_token(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Counter-test: a fresh tb_user_ token does NOT trigger the
    pre-flight short-circuit; whoami proceeds to the backend call."""
    auth.save_user_token("tb_user_fresh")

    called = {"n": 0}

    def fake_whoami(token, **kw):
        called["n"] += 1
        return {"email": "alice@example.com", "auth_method": "stk_user"}

    monkeypatch.setattr(auth, "whoami", fake_whoami)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["whoami"])
    assert result.exit_code == 0, result.output
    assert called["n"] == 1
    assert "alice@example.com" in result.output
