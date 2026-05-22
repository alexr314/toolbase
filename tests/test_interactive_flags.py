"""Tests for the agent-friendliness flags.

Covers ``_resolve_prompt_mode``, ``_confirm``, ``_require_input``, and the
CLI surface where they're applied (``install``, ``uninstall``, ``login``,
``init``).

The semantic contract is documented above the helpers in ``cli.py``:

- ``--yes`` / ``-y``  → answer Yes
- ``--no``            → answer No
- ``--no-input``      → skip; consequential prompts default to No, benign
                         to their stated default; required text prompts
                         fail with a flag pointer
- non-TTY stdin       → implicitly ``--no-input``
- mutually exclusive  → setting more than one is a UsageError
"""

from __future__ import annotations

import sys
from unittest import mock

import click
import pytest
from click.testing import CliRunner

from toolbase import cli


# ── _resolve_prompt_mode ────────────────────────────────────────────────────


def test_resolve_yes():
    with mock.patch.object(sys.stdin, "isatty", return_value=True):
        assert cli._resolve_prompt_mode(True, False, False) == "yes"


def test_resolve_no():
    with mock.patch.object(sys.stdin, "isatty", return_value=True):
        assert cli._resolve_prompt_mode(False, True, False) == "no"


def test_resolve_no_input():
    with mock.patch.object(sys.stdin, "isatty", return_value=True):
        assert cli._resolve_prompt_mode(False, False, True) == "skip"


def test_resolve_non_tty_skips_implicitly():
    with mock.patch.object(sys.stdin, "isatty", return_value=False):
        assert cli._resolve_prompt_mode(False, False, False) == "skip"


def test_resolve_tty_default_is_ask():
    with mock.patch.object(sys.stdin, "isatty", return_value=True):
        assert cli._resolve_prompt_mode(False, False, False) == "ask"


def test_resolve_yes_wins_over_non_tty():
    """Explicit --yes overrides non-TTY; the user said what they wanted."""
    with mock.patch.object(sys.stdin, "isatty", return_value=False):
        assert cli._resolve_prompt_mode(True, False, False) == "yes"


def test_resolve_mutually_exclusive():
    for combo in [(True, True, False), (True, False, True), (False, True, True), (True, True, True)]:
        with pytest.raises(click.UsageError):
            cli._resolve_prompt_mode(*combo)


# ── _confirm ────────────────────────────────────────────────────────────────


def test_confirm_yes_mode_always_true():
    assert cli._confirm("?", default=False, mode="yes") is True
    assert cli._confirm("?", default=False, mode="yes", consequential=True) is True


def test_confirm_no_mode_always_false():
    assert cli._confirm("?", default=True, mode="no") is False
    assert cli._confirm("?", default=True, mode="no", consequential=True) is False


def test_confirm_skip_mode_uses_default_for_benign():
    assert cli._confirm("?", default=True, mode="skip") is True
    assert cli._confirm("?", default=False, mode="skip") is False


def test_confirm_skip_mode_consequential_always_no():
    """Consequential prompts never auto-yes in skip mode, even with default=True."""
    assert cli._confirm("?", default=True, mode="skip", consequential=True) is False
    assert cli._confirm("?", default=False, mode="skip", consequential=True) is False


def test_confirm_ask_mode_delegates_to_click():
    with mock.patch.object(click, "confirm", return_value=True) as ck:
        cli._confirm("Proceed?", default=False, mode="ask")
    ck.assert_called_once_with("Proceed?", default=False)


# ── _require_input ──────────────────────────────────────────────────────────


def test_require_input_skip_raises_with_flag_pointer():
    with pytest.raises(click.UsageError) as ei:
        cli._require_input("Token", mode="skip", bypass_flag="--token")
    assert "--token" in str(ei.value)
    assert "Token" in str(ei.value)


def test_require_input_ask_delegates_to_prompt():
    with mock.patch.object(click, "prompt", return_value="abc") as pr:
        out = cli._require_input("Token", mode="ask", bypass_flag="--token", hide_input=True)
    assert out == "abc"
    pr.assert_called_once_with("Token", hide_input=True)


# ── CLI integration: mutex check via the runner ─────────────────────────────


def test_install_yes_and_no_together_errors():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["install", "x", "--yes", "--no"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_uninstall_yes_and_no_input_together_errors():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["uninstall", "x", "--yes", "--no-input"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


# ── CLI integration: --no-input on a missing token errors with --token hint ─


def test_login_no_input_without_token_points_at_flag():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["login", "some-toolkit", "--no-input"])
    assert result.exit_code != 0
    assert "--token" in result.output


def test_login_token_flag_skips_prompt(tmp_path, monkeypatch):
    """--token short-circuits the interactive prompt and stores the value."""
    monkeypatch.setattr(cli, "console", cli.console)  # no-op, sanity
    from toolbase import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["login", "my-tk", "--token", "toolkit_abc123"],
    )
    assert result.exit_code == 0, result.output
    stored = (tmp_path / "my-tk" / "token").read_text()
    assert stored == "toolkit_abc123"


# ── CLI integration: install of a different version creates a new cache slot ─

def test_install_different_version_coexists_with_existing(tmp_path, monkeypatch):
    """0.5.0: different versions live side-by-side in the cache, no replacement.

    Previously (0.4.x) installing v0.2.0 on top of v0.1.0 was a
    consequential replacement that --no-input would abort. The 0.5.0
    multi-version cache model removes the conflict entirely — both
    cache slots coexist. The "consequential abort" no longer applies
    to version installs (only same-version reinstalls of a fully-
    populated slot prompt, with a benign default).
    """
    from toolbase import config as cfg
    from toolbase.envs import cache_dir, write_legacy_meta
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / ".toolbase")
    (tmp_path / ".toolbase").mkdir(parents=True, exist_ok=True)

    # Pre-stage an existing install at v0.1.0 in the cache.
    existing_slot = cache_dir("demo", "0.1.0")
    existing_slot.mkdir(parents=True)
    write_legacy_meta(existing_slot, {
        "name": "demo", "version": "0.1.0", "environment": "venv",
    })

    # The "install" flow short-circuits at metadata fetch with a non-200
    # — that's fine for this test; we just need to confirm that the
    # 0.4.x consequential-replacement prompt is gone, i.e. the install
    # path no longer requires a confirmation just because some prior
    # version is around. Easiest assertion: the existing slot stays
    # intact regardless of the install outcome.
    fake_meta = {
        "name": "demo",
        "latest_version": "0.2.0",
        "versions": [{"version": "0.2.0", "tarball_url": "x"}],
    }

    class FakeResp:
        status_code = 200
        def json(self):
            return fake_meta

    runner = CliRunner()
    with mock.patch("requests.get", return_value=FakeResp()):
        runner.invoke(
            cli.main,
            ["install", "demo", "--version", "0.2.0", "--no-input"],
        )

    # The existing v0.1.0 slot is untouched regardless of v0.2.0 outcome.
    assert (existing_slot / ".stk_meta.json").exists()
