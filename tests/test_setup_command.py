"""
Click-CliRunner tests for the ``toolbase setup`` CLI command.

The command lives in ``cli.py`` and orchestrates:

- Tier-1 declarative setup (if config: block) via run_install_setup
- Tier-2 setup.py runner via run_setup_script
- ``--check`` flag → validate(ctx) only
- ``--reset`` flag → delete config first, then re-run

These tests stub the runner functions to avoid spawning subprocesses;
the runner-to-real-subprocess path is covered by test_setup_host.py
and the e2e harness.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner


def _make_install(home: Path, name: str, *,
                  has_setup_py: bool = False, config_block: list = None,
                  declare_setup_script: bool = False) -> Path:
    """Synthesize an installed toolkit in the 0.5.0 cache layout."""
    version = "0.1.0"
    tdir = home / ".toolbase" / "cache" / name / version
    tdir.mkdir(parents=True, exist_ok=True)

    yaml_data = {
        "name": name, "version": version,
        "category": "misc", "description": "test",
    }
    if config_block:
        yaml_data["config"] = config_block
    if declare_setup_script:
        yaml_data["setup_script"] = True
    (tdir / "toolkit.yaml").write_text(yaml.safe_dump(yaml_data))

    (tdir / ".tb_meta.json").write_text(json.dumps({
        "name": name, "version": version,
        "environment": "venv",
        "python_path": sys.executable,
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}"
        ),
    }))

    if has_setup_py:
        (tdir / "setup.py").write_text(
            "def setup(ctx): return True\ndef validate(ctx): return True\n"
        )
    return tdir


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Patch HOME and CONFIG_DIR so the substrate lands under tmp."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".toolbase").mkdir()
    monkeypatch.setenv("HOME", str(home))
    import toolbase.config as _cfg
    monkeypatch.setattr(_cfg, "CONFIG_DIR", home / ".toolbase")
    return home


# ── error paths ───────────────────────────────────────────────────────


def test_setup_unknown_toolkit_errors(isolated_home):
    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "nonexistent"])
    assert result.exit_code != 0
    assert "not installed" in result.output


def test_setup_reset_and_check_mutually_exclusive(isolated_home):
    _make_install(isolated_home, "tk1", has_setup_py=True)
    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--reset", "--check"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


# ── --check mode ──────────────────────────────────────────────────────


def test_setup_check_no_setup_py_is_noop(isolated_home):
    """Tier-1 toolkit (no setup.py) → --check is a no-op with helpful
    message."""
    _make_install(isolated_home, "tk1")  # no setup.py
    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--check"])
    assert result.exit_code == 0
    assert "no setup.py" in result.output.lower() or "tier-1" in result.output.lower()


def test_setup_check_runs_validate(isolated_home, monkeypatch):
    """--check calls validate_setup_script; result is rendered."""
    _make_install(isolated_home, "tk1", has_setup_py=True)

    captured_calls = []
    from toolbase.setup.runner import SetupResult
    def fake_validate(name, **kw):
        captured_calls.append(name)
        return SetupResult(ok=True)
    monkeypatch.setattr(
        "toolbase.setup.validate_setup_script", fake_validate,
    )
    # Also patch the runner module's own attribute (the cli's
    # ``from .setup import validate_setup_script`` binds to the
    # ``toolbase.setup`` namespace at call time).
    monkeypatch.setattr(
        "toolbase.setup.runner.validate_setup_script", fake_validate,
    )

    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--check"])
    assert result.exit_code == 0
    assert captured_calls == ["tk1"]
    assert "validate(ctx) passed" in result.output


def test_setup_check_failure_exits_nonzero(isolated_home, monkeypatch):
    _make_install(isolated_home, "tk1", has_setup_py=True)

    from toolbase.setup.runner import SetupResult
    def fake_validate(name, **kw):
        return SetupResult(
            ok=False, message="api_key missing",
            log_path=isolated_home / "logs" / "x.log",
        )
    monkeypatch.setattr(
        "toolbase.setup.validate_setup_script", fake_validate,
    )
    # Also patch the runner module's own attribute (the cli's
    # ``from .setup import validate_setup_script`` binds to the
    # ``toolbase.setup`` namespace at call time).
    monkeypatch.setattr(
        "toolbase.setup.runner.validate_setup_script", fake_validate,
    )

    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--check"])
    assert result.exit_code == 1
    assert "validate(ctx) failed" in result.output
    assert "api_key missing" in result.output


# ── --reset mode ──────────────────────────────────────────────────────


def test_setup_reset_confirms_by_default(isolated_home, monkeypatch):
    """--reset asks for confirmation in the default (ask) mode.
    With non-TTY (CliRunner default), we go to skip mode where
    consequential prompts auto-fail."""
    _make_install(isolated_home, "tk1", has_setup_py=True)
    # Pre-populate config so there's something to delete.
    from toolbase.setup import set_config_value
    set_config_value("tk1", "k", "v")
    from toolbase.setup import config_path
    assert config_path("tk1").exists()

    # Stub the setup runner so we don't actually subprocess.
    from toolbase.setup.runner import SetupResult
    monkeypatch.setattr(
        "toolbase.setup.run_setup_script",
        lambda name, **kw: SetupResult(ok=True),
    )

    from toolbase.cli import main
    runner = CliRunner()
    # No -y → consequential prompt in skip mode → aborts.
    result = runner.invoke(main, ["setup", "tk1", "--reset"])
    assert result.exit_code == 0
    assert "Aborted" in result.output
    # File is still there.
    assert config_path("tk1").exists()


def test_setup_reset_with_yes_flag_proceeds(isolated_home, monkeypatch):
    _make_install(isolated_home, "tk1", has_setup_py=True)
    from toolbase.setup import set_config_value, config_path
    set_config_value("tk1", "k", "v")
    assert config_path("tk1").exists()

    from toolbase.setup.runner import SetupResult
    monkeypatch.setattr(
        "toolbase.setup.run_setup_script",
        lambda name, **kw: SetupResult(ok=True),
    )

    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--reset", "-y"])
    assert result.exit_code == 0
    # File should be gone (deleted then setup ran but didn't re-create
    # it via the stubbed run_setup_script).
    assert not config_path("tk1").exists()


# ── happy paths ───────────────────────────────────────────────────────


def test_setup_runs_setup_script(isolated_home, monkeypatch):
    _make_install(isolated_home, "tk1", has_setup_py=True)

    captured = []
    from toolbase.setup.runner import SetupResult
    def fake_run(name, **kw):
        captured.append((name, kw.get("prompt_mode")))
        return SetupResult(ok=True)
    monkeypatch.setattr("toolbase.setup.run_setup_script", fake_run)

    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--no-input"])
    assert result.exit_code == 0
    assert captured == [("tk1", "skip")]
    assert "setup complete" in result.output


def test_setup_no_setup_py_with_no_config_is_noop(isolated_home):
    """Tier-1 toolkit (no setup.py, no config:) → setup is a no-op."""
    _make_install(isolated_home, "tk1")
    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--no-input"])
    assert result.exit_code == 0
    assert "Tier-1" in result.output or "no setup.py" in result.output.lower()


def test_setup_failure_exits_nonzero(isolated_home, monkeypatch):
    _make_install(isolated_home, "tk1", has_setup_py=True)

    from toolbase.setup.runner import SetupResult
    def fake_run(name, **kw):
        return SetupResult(
            ok=False,
            message="download failed",
            traceback="Traceback (most recent call last):\n  ...\nValueError: bad",
            log_path=isolated_home / "logs" / "setup-tk1-x.log",
        )
    monkeypatch.setattr("toolbase.setup.run_setup_script", fake_run)

    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "tk1", "--no-input"])
    assert result.exit_code == 1
    assert "setup failed" in result.output
    assert "download failed" in result.output
    # Last line of the traceback shown in the summary.
    assert "ValueError: bad" in result.output


# ── help-text section landing ─────────────────────────────────────────


def test_setup_appears_in_configuration_help_section(isolated_home):
    """Per the manager's sign-off: keep the Configuration section,
    'setup' lands in it alongside 'config'."""
    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "Configuration" in result.output
    # Both commands listed.
    assert "config" in result.output
    assert "setup" in result.output
