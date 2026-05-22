"""Phase 6 tests — legacy-layout heads-up + ``tb reset``.

The 0.5.0 cutover broke compatibility with the 0.4.x install layout
under ``~/.toolbase/toolkits/`` (Alex authorized the clean break).
This file covers:

- Legacy-layout detection fires on `tb` invocation (any subcommand)
  when ``~/.toolbase/toolkits/`` exists with content.
- The heads-up goes to stderr, not stdout. ``tb list --json`` stays
  clean (parseable JSON on stdout).
- The heads-up is suppressed when the legacy dir is absent or empty.
- ``tb reset`` default mode (no flags) deletes ``toolkits/`` only,
  preserving cache/, config/, default-project/, logs/, config.json.
- ``tb reset --all`` deletes cache/, toolkits/, downloads/,
  default-project/. Preserves config.json + logs/. Preserves config/
  unless --include-config is also passed.
- ``tb reset --all --include-config`` additionally deletes config/.
- ``tb reset --dry-run`` lists what would be deleted, removes nothing.
- ``tb reset --yes`` skips confirmation for CI.
- Default-N confirmation: aborted prompts delete nothing.
- ``--include-config`` without ``--all`` is a UsageError.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import config as toolbase_config
from toolbase import cli


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    # Clear any inherited suppression from earlier test runs in the
    # same process.
    monkeypatch.delenv("TOOLBASE_SUPPRESS_LEGACY_WARNING", raising=False)
    return fake


def _seed_legacy(fake_home: Path, names=("foo", "bar")) -> Path:
    """Drop ``~/.toolbase/toolkits/<name>/`` directories with placeholder content."""
    legacy = fake_home / "toolkits"
    legacy.mkdir(parents=True, exist_ok=True)
    for n in names:
        (legacy / n).mkdir()
        (legacy / n / "placeholder").write_text("legacy install\n")
    return legacy


def _seed_cache(fake_home: Path) -> Path:
    cache = fake_home / "cache" / "arxiv-search" / "0.2.0"
    cache.mkdir(parents=True)
    (cache / ".install_meta.yaml").write_text(
        "schema_version: 1\nname: arxiv-search\nversion: 0.2.0\n"
    )
    return cache


def _seed_config(fake_home: Path) -> Path:
    cfg = fake_home / "config"
    cfg.mkdir(parents=True)
    (cfg / "arxiv-search.yaml").write_text("schema_version: 1\napi_key: secret\n")
    return cfg


def _seed_default_project(fake_home: Path) -> Path:
    dp = fake_home / "default-project"
    dp.mkdir(parents=True)
    (dp / "manifest.yaml").write_text("schema_version: 1\ntoolkits: []\n")
    return dp


def _seed_downloads(fake_home: Path) -> Path:
    d = fake_home / "downloads"
    d.mkdir(parents=True)
    (d / "blob.bin").write_text("data\n")
    return d


def _seed_config_json(fake_home: Path) -> Path:
    """Seed ``config.json`` — the login state we must always preserve."""
    p = fake_home / "config.json"
    p.write_text('{"token": "secret"}')
    return p


def _seed_logs(fake_home: Path) -> Path:
    logs = fake_home / "logs"
    logs.mkdir(parents=True)
    (logs / "serve.log").write_text("log line\n")
    return logs


# ── legacy-layout detection (heads-up) ──────────────────────────────


class TestLegacyDetection:
    def test_heads_up_fires_when_legacy_dir_has_content(self, fake_home):
        _seed_legacy(fake_home)
        runner = CliRunner()
        # Run a benign command that doesn't itself touch the legacy dir.
        result = runner.invoke(cli.main, ["list"])
        # Heads-up on stderr.
        assert "Heads up: 0.5.0 adds multi-version installs" in result.stderr
        # ``list`` itself prints to stdout, not stderr.
        assert "Heads up" not in result.stdout

    def test_no_heads_up_when_legacy_dir_absent(self, fake_home):
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert "Heads up" not in result.stderr
        assert "Heads up" not in result.stdout

    def test_no_heads_up_when_legacy_dir_empty(self, fake_home):
        # Empty dir doesn't count.
        (fake_home / "toolkits").mkdir()
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert "Heads up" not in result.stderr

    def test_heads_up_does_not_corrupt_json_output(self, fake_home):
        """``tb list --json`` stays parseable even with the legacy dir present."""
        _seed_legacy(fake_home)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        assert result.exit_code == 0
        # stdout MUST be parseable JSON — heads-up is on stderr only.
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        # Heads-up still surfaces on stderr.
        assert "Heads up" in result.stderr

    def test_env_var_suppresses_heads_up(self, fake_home, monkeypatch):
        _seed_legacy(fake_home)
        monkeypatch.setenv("TOOLBASE_SUPPRESS_LEGACY_WARNING", "1")
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert "Heads up" not in result.stderr

    def test_heads_up_points_at_reset(self, fake_home):
        _seed_legacy(fake_home)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert "tb reset" in result.stderr


# ── tb reset (cutover mode, no flags) ──────────────────────────────


class TestResetCutoverMode:
    def test_with_no_legacy_layout_reports_nothing_to_do(self, fake_home):
        _seed_cache(fake_home)
        _seed_config(fake_home)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--yes"])
        assert result.exit_code == 0
        assert "Nothing to reset" in result.output
        # Cache and config still intact.
        assert (fake_home / "cache" / "arxiv-search" / "0.2.0").exists()
        assert (fake_home / "config" / "arxiv-search.yaml").exists()

    def test_default_removes_only_legacy_layout(self, fake_home):
        _seed_legacy(fake_home, names=("foo", "bar"))
        _seed_cache(fake_home)
        _seed_config(fake_home)
        _seed_default_project(fake_home)
        _seed_downloads(fake_home)
        _seed_config_json(fake_home)
        _seed_logs(fake_home)

        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--yes"])

        assert result.exit_code == 0, result.output
        # Legacy gone.
        assert not (fake_home / "toolkits").exists()
        # Everything else preserved.
        assert (fake_home / "cache" / "arxiv-search" / "0.2.0").exists()
        assert (fake_home / "config" / "arxiv-search.yaml").exists()
        assert (fake_home / "default-project").exists()
        assert (fake_home / "downloads").exists()
        assert (fake_home / "config.json").exists()
        assert (fake_home / "logs").exists()

    def test_lists_paths_before_asking(self, fake_home):
        legacy = _seed_legacy(fake_home)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--yes"])
        assert result.exit_code == 0
        # Path was named in the output before deletion.
        assert "toolkits" in result.output
        assert str(legacy) in result.output

    def test_aborted_confirmation_deletes_nothing(self, fake_home):
        _seed_legacy(fake_home, names=("foo",))
        runner = CliRunner()
        # Send "n" to the confirmation; we don't pass --yes.
        result = runner.invoke(cli.main, ["reset"], input="n\n")
        assert (fake_home / "toolkits" / "foo").exists()

    def test_no_input_defaults_to_no_for_consequential_prompts(self, fake_home):
        """``--no-input`` is the CI-without-confirm-flag path; reset must
        treat consequential prompts as default-N rather than auto-yes."""
        _seed_legacy(fake_home, names=("foo",))
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--no-input"])
        assert result.exit_code == 0
        # Legacy still present — refused implicitly.
        assert (fake_home / "toolkits" / "foo").exists()
        assert "Cancelled" in result.output


# ── tb reset --dry-run ─────────────────────────────────────────────


class TestResetDryRun:
    def test_dry_run_lists_but_deletes_nothing(self, fake_home):
        _seed_legacy(fake_home, names=("foo",))
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--dry-run"])
        assert result.exit_code == 0
        assert "Dry-run" in result.output or "dry-run" in result.output.lower()
        assert (fake_home / "toolkits" / "foo").exists()

    def test_dry_run_all_lists_full_target_set(self, fake_home):
        _seed_legacy(fake_home)
        _seed_cache(fake_home)
        _seed_downloads(fake_home)
        _seed_default_project(fake_home)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--all", "--dry-run"])
        assert result.exit_code == 0
        # All four targets named.
        for kw in ("cache", "toolkits", "downloads", "default-project"):
            assert kw in result.output
        # Nothing deleted.
        assert (fake_home / "cache").exists()
        assert (fake_home / "toolkits").exists()
        assert (fake_home / "downloads").exists()
        assert (fake_home / "default-project").exists()


# ── tb reset --all ─────────────────────────────────────────────────


class TestResetAll:
    def test_all_removes_scorched_targets_preserves_config_and_logs(self, fake_home):
        _seed_legacy(fake_home, names=("foo",))
        _seed_cache(fake_home)
        _seed_config(fake_home)
        _seed_default_project(fake_home)
        _seed_downloads(fake_home)
        _seed_config_json(fake_home)
        _seed_logs(fake_home)

        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--all", "--yes"])

        assert result.exit_code == 0, result.output
        # Removed:
        assert not (fake_home / "toolkits").exists()
        assert not (fake_home / "cache").exists()
        assert not (fake_home / "downloads").exists()
        assert not (fake_home / "default-project").exists()
        # Preserved:
        assert (fake_home / "config").exists()
        assert (fake_home / "config" / "arxiv-search.yaml").exists()
        assert (fake_home / "config.json").exists()
        assert (fake_home / "logs").exists()

    def test_all_with_include_config_removes_config_too(self, fake_home):
        _seed_legacy(fake_home, names=("foo",))
        _seed_cache(fake_home)
        _seed_config(fake_home)
        _seed_config_json(fake_home)
        _seed_logs(fake_home)

        runner = CliRunner()
        result = runner.invoke(
            cli.main, ["reset", "--all", "--include-config", "--yes"]
        )

        assert result.exit_code == 0, result.output
        # config/ is gone.
        assert not (fake_home / "config").exists()
        # config.json and logs/ still preserved.
        assert (fake_home / "config.json").exists()
        assert (fake_home / "logs").exists()

    def test_include_config_without_all_is_usage_error(self, fake_home):
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--include-config", "--yes"])
        assert result.exit_code != 0
        # Error mentions --all somewhere.
        combined = result.output + (result.stderr or "")
        assert "--all" in combined or "all" in combined.lower()

    def test_all_aborted_first_prompt_deletes_nothing(self, fake_home):
        _seed_legacy(fake_home)
        _seed_cache(fake_home)
        runner = CliRunner()
        # Refuse the first prompt.
        result = runner.invoke(cli.main, ["reset", "--all"], input="n\n")
        assert (fake_home / "toolkits").exists()
        assert (fake_home / "cache").exists()

    def test_all_aborted_second_prompt_deletes_nothing(self, fake_home):
        _seed_legacy(fake_home)
        _seed_cache(fake_home)
        runner = CliRunner()
        # First yes, second no. Two confirms.
        result = runner.invoke(cli.main, ["reset", "--all"], input="y\nn\n")
        assert (fake_home / "toolkits").exists()
        assert (fake_home / "cache").exists()

    def test_all_no_input_defaults_to_no(self, fake_home):
        _seed_legacy(fake_home)
        _seed_cache(fake_home)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--all", "--no-input"])
        assert result.exit_code == 0
        assert (fake_home / "toolkits").exists()
        assert (fake_home / "cache").exists()

    def test_all_with_nothing_to_delete_reports_nothing_to_do(self, fake_home):
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--all", "--yes"])
        assert result.exit_code == 0
        assert "Nothing to reset" in result.output


# ── --help ──────────────────────────────────────────────────────────


class TestResetHelp:
    def test_help_lists_all_three_modes(self):
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--help"])
        assert result.exit_code == 0
        # Each mode is documented.
        assert "tb reset " in result.output
        assert "--all" in result.output
        assert "--include-config" in result.output
        assert "--dry-run" in result.output

    def test_help_documents_preserved_paths(self):
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--help"])
        # The help text should mention what's preserved so users
        # aren't surprised.
        assert "config.json" in result.output
        assert "logs/" in result.output


# ── reset suppresses subsequent heads-up ────────────────────────────


class TestResetSuppressesHeadsUp:
    def test_reset_sets_suppression_env_var_after_cleanup(self, fake_home, monkeypatch):
        """After reset runs (legacy cleanup successful), subsequent ``tb``
        calls in the same Python process don't re-emit the heads-up.

        Captures via the env var the command exports."""
        # Clear before to be sure.
        monkeypatch.delenv("TOOLBASE_SUPPRESS_LEGACY_WARNING", raising=False)
        _seed_legacy(fake_home, names=("foo",))
        runner = CliRunner()
        result = runner.invoke(cli.main, ["reset", "--yes"])
        assert result.exit_code == 0
        # Env var was set during reset execution.
        assert os.environ.get("TOOLBASE_SUPPRESS_LEGACY_WARNING") == "1"
