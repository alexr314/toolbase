"""
Tests for the Phase 3C-3 init-template changes.

Two surfaces:

1. ``create_toolkit_from_template(...)`` with and without ``with_setup=True``.
2. ``toolbase init`` CLI flag plumbing for ``--with-setup``.

Beyond mechanical "files exist" checks: verify that

- A fresh-init toolkit (no flags) drops a toolkit.yaml whose
  commented-out ``config:`` block doesn't get parsed as live config.
- A ``--with-setup`` init drops setup.py AND flips ``setup_script:
  true`` in toolkit.yaml.
- Both fresh-init and ``--with-setup`` toolkits pass
  ``toolbase validate`` (the install-pipeline sentinel).
- The ``{{name}}`` placeholder is substituted in setup.py too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from toolbase.toolkit import (
    create_toolkit_from_template,
    _insert_setup_script_flag,
)


# ── library surface: create_toolkit_from_template ─────────────────────


def test_default_init_does_not_drop_setup_py(tmp_path):
    """Without ``with_setup=True``, no setup.py is created."""
    target = tmp_path / "tk"
    create_toolkit_from_template(name="tk", path=target)
    assert (target / "toolkit.yaml").exists()
    assert (target / "tools" / "__init__.py").exists()
    assert not (target / "setup.py").exists()


def test_default_init_omits_setup_script_flag(tmp_path):
    """No ``setup_script: true`` line in default toolkit.yaml."""
    target = tmp_path / "tk"
    create_toolkit_from_template(name="tk", path=target)
    yaml_text = (target / "toolkit.yaml").read_text()
    assert "setup_script:" not in yaml_text


def test_with_setup_drops_setup_py(tmp_path):
    target = tmp_path / "tk"
    create_toolkit_from_template(name="tk", path=target, with_setup=True)
    setup_py = target / "setup.py"
    assert setup_py.exists()
    body = setup_py.read_text()
    # Template substitutions worked.
    assert "tk" in body
    # Functions present.
    assert "def setup(ctx):" in body
    assert "def validate(ctx):" in body


def test_with_setup_flips_setup_script_flag(tmp_path):
    target = tmp_path / "tk"
    create_toolkit_from_template(name="tk", path=target, with_setup=True)
    yaml_text = (target / "toolkit.yaml").read_text()
    assert "setup_script: true" in yaml_text
    # Parse to confirm it's actually consumed as a top-level boolean.
    parsed = yaml.safe_load(yaml_text)
    assert parsed["setup_script"] is True


def test_with_setup_inserts_flag_before_tools(tmp_path):
    """The flag should land near other top-level metadata, not buried
    inside the tools list. Sanity: the rendered YAML has the flag
    line BEFORE the ``tools:`` line."""
    target = tmp_path / "tk"
    create_toolkit_from_template(name="tk", path=target, with_setup=True)
    yaml_text = (target / "toolkit.yaml").read_text()
    flag_idx = yaml_text.index("setup_script: true")
    tools_idx = yaml_text.index("\ntools:")
    assert flag_idx < tools_idx


# ── _insert_setup_script_flag helper unit tests ───────────────────────


def test_insert_setup_script_flag_basic():
    yaml_in = "name: foo\nversion: 0.1.0\n\ntools:\n  - name: x\n"
    out = _insert_setup_script_flag(yaml_in)
    assert "setup_script: true" in out
    assert out.index("setup_script: true") < out.index("\ntools:")


def test_insert_setup_script_flag_idempotent():
    """Already-present flag should not be doubled."""
    yaml_in = "name: foo\nsetup_script: true\n\ntools:\n  - name: x\n"
    out = _insert_setup_script_flag(yaml_in)
    assert out.count("setup_script: true") == 1


def test_insert_setup_script_flag_no_tools_block_appends():
    """Defensive: if for some reason there's no tools: marker, the
    helper appends rather than crashing."""
    yaml_in = "name: foo\nversion: 0.1.0\n"
    out = _insert_setup_script_flag(yaml_in)
    assert "setup_script: true" in out


# ── commented-out config: block ───────────────────────────────────────


def test_default_init_config_block_is_commented_out(tmp_path):
    """The sample config: block must be commented; otherwise install
    would prompt for fields the author hasn't wired up."""
    target = tmp_path / "tk"
    create_toolkit_from_template(name="tk", path=target)
    yaml_text = (target / "toolkit.yaml").read_text()
    parsed = yaml.safe_load(yaml_text)
    # No live `config` key — the YAML parser should ignore the
    # commented-out block.
    assert parsed.get("config") is None


def test_default_init_yaml_contains_commented_examples(tmp_path):
    """Authors should see sample fields they can uncomment. Verify
    the literal strings are in the file, even though they're behind
    `#`s."""
    target = tmp_path / "tk"
    create_toolkit_from_template(name="tk", path=target)
    yaml_text = (target / "toolkit.yaml").read_text()
    # Each sampled type's name appears as a comment.
    assert "type: secret" in yaml_text
    assert "type: path" in yaml_text
    assert "type: integer" in yaml_text
    assert "type: choice" in yaml_text
    # And the docs link.
    assert "https://tool-base.org/docs/configuration" in yaml_text


def test_default_init_substitutes_name_in_default_path(tmp_path):
    """The data_path default uses {{name}} so a copy-paste author
    gets a sensible per-toolkit path."""
    target = tmp_path / "my-special-tk"
    create_toolkit_from_template(name="my-special-tk", path=target)
    yaml_text = (target / "toolkit.yaml").read_text()
    assert "~/.toolbase/data/my-special-tk" in yaml_text
    assert "{{name}}" not in yaml_text  # placeholder fully substituted


# ── validation passes for both fresh and --with-setup ─────────────────


def test_fresh_init_passes_validate(tmp_path):
    """Sentinel: a fresh-init toolkit must pass toolbase validate.
    This is the install-pipeline gate; if validation fails on a
    template-only toolkit, the entire init flow is broken."""
    from toolbase.validation import validate_toolkit

    target = tmp_path / "tk-fresh"
    create_toolkit_from_template(name="tk-fresh", path=target)
    result = validate_toolkit(target)
    assert result.is_valid, f"validation failed: {result.errors}"


def test_with_setup_init_passes_validate(tmp_path):
    """Sentinel: --with-setup toolkit must also pass validate.
    Crucially: the validate path checks that setup_script: true
    is paired with a setup.py at root. If the helper or template
    drift, this catches it."""
    from toolbase.validation import validate_toolkit

    target = tmp_path / "tk-with-setup"
    create_toolkit_from_template(
        name="tk-with-setup", path=target, with_setup=True,
    )
    result = validate_toolkit(target)
    assert result.is_valid, f"validation failed: {result.errors}"


# ── CLI flag plumbing ─────────────────────────────────────────────────


def test_cli_init_with_setup_flag_drops_setup_py(tmp_path, monkeypatch):
    """``toolbase init my-tk --with-setup`` plumbs through.

    Network calls to the registry would otherwise be made; stub them
    out so the test is offline-safe.
    """
    import requests

    class _FakeResp:
        status_code = 404

    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: _FakeResp(),
    )

    from toolbase.cli import main
    runner = CliRunner()
    # Use isolated_filesystem so init doesn't pollute cwd.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(
            main, ["init", "my-tk", "--with-setup", "--no-input"],
        )
        assert result.exit_code == 0, result.output
        assert (Path("my-tk") / "setup.py").exists()
        yaml_text = (Path("my-tk") / "toolkit.yaml").read_text()
        assert "setup_script: true" in yaml_text


def test_cli_init_without_with_setup_omits_setup_py(tmp_path, monkeypatch):
    """Default ``toolbase init my-tk`` does NOT drop setup.py."""
    import requests

    class _FakeResp:
        status_code = 404

    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: _FakeResp(),
    )

    from toolbase.cli import main
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(
            main, ["init", "my-tk", "--no-input"],
        )
        assert result.exit_code == 0, result.output
        assert not (Path("my-tk") / "setup.py").exists()


def test_cli_init_help_mentions_with_setup(tmp_path):
    """The ``--help`` output documents the new flag."""
    from toolbase.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--help"])
    assert "--with-setup" in result.output
    # And the existing --with-docker is still there.
    assert "--with-docker" in result.output


# ── registry-prefilled path also gets the flag ────────────────────────


def test_with_setup_works_on_registry_prefilled_path(tmp_path):
    """The toolkit.py YAML generation has TWO paths — template-rendered
    (no registry hit) and inline-f-string (registry-prefilled). Both
    must honor with_setup. The other tests cover the template path;
    this hits the f-string path."""
    target = tmp_path / "tk"
    create_toolkit_from_template(
        name="tk", path=target, with_setup=True,
        registry_metadata={
            "name": "tk", "latest_version": "0.5.0",
            "category": "astro", "description": "test", "author": "X",
            "license": "MIT", "homepage": "https://example.com",
            "keywords": ["astro", "x"],
        },
    )
    yaml_text = (target / "toolkit.yaml").read_text()
    assert "setup_script: true" in yaml_text
    parsed = yaml.safe_load(yaml_text)
    assert parsed["setup_script"] is True
    assert (target / "setup.py").exists()
