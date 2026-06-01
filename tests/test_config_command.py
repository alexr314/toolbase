"""Integration tests for the ``toolbase config`` command group (3C-1).

Drives every subcommand (``show / edit / path / set / unset /
validate``) via Click's ``CliRunner`` against a tmp config dir.
``edit`` is exercised by mocking ``subprocess.call`` since launching
$EDITOR for real is not test-friendly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml as _yaml
from click.testing import CliRunner

from toolbase import config as toolbase_config
from toolbase import cli
from toolbase.setup import (
    NEEDS_VALUE_SENTINEL,
    config_path,
    load_config,
    save_config,
)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CONFIG_DIR so the entire toolbase substrate lands in tmp.

    The 0.5.0 cache layout puts toolkit binaries under
    ``~/.toolbase/cache/<name>/<version>/``. The resolver pattern in
    ``envs/paths.py`` re-reads ``CONFIG_DIR`` on every call so a single
    monkeypatch suffices for the *user* scope.

    The *project* scope is discovered by walking up from ``cwd`` for a
    ``.toolbase/manifest.yaml`` (``envs/discovery.find_project_root``).
    A monkeypatch of CONFIG_DIR does NOT redirect that walk — so if the
    test process's cwd is inside a tree that has a ``.toolbase/`` above
    it (e.g. running pytest from the repo root after a ``tb install -l``
    dropped one there), config commands that resolve a project layer would
    read/write the real repo's ``.toolbase/`` instead of tmp, and tests
    would both fail and pollute the repo. Pin cwd to a clean tmp project
    dir so the upward walk finds nothing and falls back to the
    (CONFIG_DIR-rooted) default-project. This keeps the whole substrate —
    user scope AND project scope — inside tmp regardless of where pytest
    is invoked from.
    """
    fake = tmp_path / "toolbase"
    fake.mkdir()
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return fake


def _install_synthetic(
    base: Path,
    name: str = "demo",
    config_block=None,
) -> Path:
    """Drop a minimal cache slot with toolkit.yaml + .tb_meta.json.

    Mirrors the 0.5.0 layout: ``base/cache/<name>/<version>/``.
    """
    version = "0.1.0"
    tk = base / "cache" / name / version
    tk.mkdir(parents=True)
    yaml_data = {
        "name": name,
        "version": version,
        "description": "x",
        "author": "test",
        "category": "other",
        "tools": [{"name": "t", "function": "tools.t", "description": "d"}],
    }
    if config_block is not None:
        yaml_data["config"] = config_block
    (tk / "toolkit.yaml").write_text(_yaml.safe_dump(yaml_data))
    (tk / ".tb_meta.json").write_text(json.dumps({
        "name": name, "version": version, "environment": "venv",
        "python_path": "/usr/bin/python", "python_version": "3.12",
    }))
    return tk


# ── path ────────────────────────────────────────────────────────────


def test_config_path_prints_absolute(isolated: Path):
    _install_synthetic(isolated)
    r = CliRunner().invoke(cli.main, ["config", "path", "demo", "--user"])
    assert r.exit_code == 0
    assert "demo.yaml" in r.output
    assert str(isolated) in r.output


def test_config_path_unknown_toolkit_errors(isolated: Path):
    r = CliRunner().invoke(cli.main, ["config", "path", "ghost"])
    assert r.exit_code == 1
    assert "not installed" in r.output.lower()


# ── show ────────────────────────────────────────────────────────────


def test_config_show_no_file_prints_hint(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "api_key", "type": "secret"},
    ])
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert r.exit_code == 0
    assert "no config file yet" in r.output.lower()


def test_config_show_renders_fields(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string"},
        {"name": "port", "type": "integer"},
    ])
    save_config("demo", {"host": "localhost", "port": 8080})
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert r.exit_code == 0
    assert "host" in r.output
    assert "localhost" in r.output
    assert "port" in r.output
    assert "8080" in r.output


def test_config_show_masks_secrets(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "api_key", "type": "secret"},
    ])
    save_config("demo", {"api_key": "supersecret"})
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert "supersecret" not in r.output
    assert "<set>" in r.output


def test_config_show_marks_needs_value_sentinel(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "api_key", "type": "secret", "required": True},
    ])
    save_config("demo", {"api_key": NEEDS_VALUE_SENTINEL})
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert NEEDS_VALUE_SENTINEL in r.output


# ── set ─────────────────────────────────────────────────────────────


def test_config_set_writes_value(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string"},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "host", "myhost", "--user"])
    assert r.exit_code == 0
    assert load_config("demo")["host"] == "myhost"


def test_config_set_coerces_per_type(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "port", "type": "integer", "min": 1, "max": 65535},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "port", "8080", "--user"])
    assert r.exit_code == 0
    # Stored as int, not str.
    assert load_config("demo")["port"] == 8080


def test_config_set_rejects_invalid_per_type(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "port", "type": "integer", "min": 1, "max": 100},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "port", "9999"])
    assert r.exit_code == 1
    assert "above max" in r.output


def test_config_set_undeclared_field_warns_but_writes(isolated: Path):
    """A field not in the schema is still writable (with a warning)."""
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string"},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "extra", "value", "--user"])
    assert r.exit_code == 0
    assert "not declared" in r.output.lower() or "warning" in r.output.lower()
    assert load_config("demo")["extra"] == "value"


def test_config_set_preserves_other_fields(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "a", "type": "string"},
        {"name": "b", "type": "string"},
    ])
    save_config("demo", {"a": "1", "b": "2"})
    CliRunner().invoke(cli.main, ["config", "set", "demo", "a", "99", "--user"])
    data = load_config("demo")
    assert data["a"] == "99"
    assert data["b"] == "2"


# ── unset ───────────────────────────────────────────────────────────


def test_config_unset_removes_field(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "a", "type": "string"},
    ])
    save_config("demo", {"a": "x", "extra": "y"})
    r = CliRunner().invoke(cli.main, ["config", "unset", "demo", "a", "--user"])
    assert r.exit_code == 0
    assert "a" not in load_config("demo")


def test_config_unset_missing_key_warns(isolated: Path):
    _install_synthetic(isolated)
    r = CliRunner().invoke(cli.main, ["config", "unset", "demo", "ghost"])
    assert r.exit_code == 0
    assert "no such field" in r.output.lower()


# ── validate ────────────────────────────────────────────────────────


def test_config_validate_clean_passes(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string", "required": True},
    ])
    save_config("demo", {"host": "myhost"})
    r = CliRunner().invoke(cli.main, ["config", "validate", "demo"])
    assert r.exit_code == 0
    assert "valid" in r.output.lower()


def test_config_validate_missing_required_fails(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string", "required": True},
        {"name": "port", "type": "integer", "default": 8080},
    ])
    r = CliRunner().invoke(cli.main, ["config", "validate", "demo"])
    assert r.exit_code == 1
    assert "missing required" in r.output.lower()
    assert "host" in r.output


def test_config_validate_no_schema_says_so(isolated: Path):
    _install_synthetic(isolated)  # no config: block
    r = CliRunner().invoke(cli.main, ["config", "validate", "demo"])
    assert r.exit_code == 0
    assert "no config" in r.output.lower() or "nothing to validate" in r.output.lower()


# ── init ────────────────────────────────────────────────────────────


def _schema_with_one_of_each_kind():
    return [
        {"name": "base_directory", "type": "string", "required": True,
         "description": "Working directory."},
        {"name": "verbose", "type": "boolean", "default": False,
         "description": "Whether to log loudly."},
        {"name": "mg5_path", "type": "string", "required": False,
         "description": "Optional path to MG5."},
    ]


def test_config_init_scaffolds_project_layer_by_default(isolated: Path):
    _install_synthetic(isolated, config_block=_schema_with_one_of_each_kind())
    r = CliRunner().invoke(cli.main, ["config", "init", "demo"])
    assert r.exit_code == 0, r.output
    # Default layer is project (matches set/unset).
    out_path = Path.cwd() / ".toolbase" / "config" / "demo.yaml"
    assert out_path.exists()
    body = out_path.read_text()
    # Required + no default → <NEEDS VALUE>
    assert "base_directory: <NEEDS VALUE>" in body
    # Optional + default → default value, written uncommented
    assert "verbose: false" in body
    # Optional + no default → commented out
    assert "# mg5_path:" in body
    # Descriptions are preserved as YAML comments above each key
    assert "# Working directory." in body
    assert "# Optional path to MG5." in body


def test_config_init_user_layer(isolated: Path):
    _install_synthetic(isolated, config_block=_schema_with_one_of_each_kind())
    r = CliRunner().invoke(cli.main, ["config", "init", "demo", "--user"])
    assert r.exit_code == 0, r.output
    out_path = isolated / "config" / "demo.yaml"
    assert out_path.exists()
    assert "base_directory: <NEEDS VALUE>" in out_path.read_text()


def test_config_init_refuses_overwrite(isolated: Path):
    _install_synthetic(isolated, config_block=_schema_with_one_of_each_kind())
    out = isolated / "config" / "demo.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("schema_version: 1\nbase_directory: /already/set\n")
    r = CliRunner().invoke(cli.main, ["config", "init", "demo", "--user"])
    assert r.exit_code == 1
    assert "already exists" in r.output.lower()
    # File should be unchanged.
    assert "/already/set" in out.read_text()


def test_config_init_force_overwrites(isolated: Path):
    _install_synthetic(isolated, config_block=_schema_with_one_of_each_kind())
    out = isolated / "config" / "demo.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("schema_version: 1\nbase_directory: /already/set\n")
    r = CliRunner().invoke(
        cli.main, ["config", "init", "demo", "--user", "--force"],
    )
    assert r.exit_code == 0, r.output
    # File overwritten with scaffold.
    assert "<NEEDS VALUE>" in out.read_text()


def test_config_init_no_schema_says_so(isolated: Path):
    _install_synthetic(isolated)  # no config: block
    r = CliRunner().invoke(cli.main, ["config", "init", "demo"])
    assert r.exit_code == 0
    assert "no config" in r.output.lower() or "nothing to scaffold" in r.output.lower()


def test_config_init_warns_about_required_in_output(isolated: Path):
    _install_synthetic(isolated, config_block=_schema_with_one_of_each_kind())
    r = CliRunner().invoke(cli.main, ["config", "init", "demo", "--user"])
    assert "base_directory" in r.output
    assert "required" in r.output.lower() or "fill in" in r.output.lower()


def test_config_init_scaffold_is_single_document(isolated: Path):
    """``yaml.safe_dump(scalar)`` appends a ``\\n...`` document-end marker
    that the older ``_yaml_repr`` only partially stripped. The corrupted
    scaffold parsed as two YAML documents, so ``yaml.safe_load`` later
    refused it with "expected a single document in the stream" — at
    serve time the orchestrator dropped the toolkit with a "config
    incomplete" message and Claude Code reported "Failed to reconnect:
    -32000" with no obvious cause. Exercise every default-value branch
    (string, path-template, integer, secret) and assert (1) no embedded
    ``...`` survives in the scaffold and (2) it round-trips through
    ``yaml.safe_load``."""
    import yaml
    _install_synthetic(isolated, config_block=[
        {"name": "base_directory", "type": "path", "required": True,
         "default": "${CWD}", "description": "workdir template default"},
        {"name": "host", "type": "string", "default": "localhost"},
        {"name": "port", "type": "integer", "default": 8080},
        {"name": "api_key", "type": "secret", "default": "your-key-here"},
        {"name": "verbose", "type": "boolean", "default": False},
    ])
    r = CliRunner().invoke(cli.main, ["config", "init", "demo", "--user"])
    assert r.exit_code == 0, r.output

    body = (isolated / "config" / "demo.yaml").read_text()
    # No line consists solely of the YAML document-end marker. Even one
    # such line splits the file into multiple documents and breaks
    # safe_load.
    for i, line in enumerate(body.splitlines(), start=1):
        assert line.strip() != "...", (
            f"line {i} is a YAML document-end marker — scaffolded file "
            f"is a multi-document stream:\n{body}"
        )
    # The whole file must safe_load (not safe_load_all) — i.e. parse as
    # a single document mapping with every default populated.
    loaded = yaml.safe_load(body)
    assert loaded["base_directory"] == "${CWD}"
    assert loaded["host"] == "localhost"
    assert loaded["port"] == 8080
    assert loaded["api_key"] == "your-key-here"
    assert loaded["verbose"] is False


# ── edit ────────────────────────────────────────────────────────────


def test_config_edit_invokes_editor(isolated: Path, monkeypatch):
    _install_synthetic(isolated)
    called = {}

    def fake_call(argv):
        called["argv"] = argv
        return 0

    monkeypatch.setenv("EDITOR", "/usr/bin/nano")
    monkeypatch.setattr("toolbase.cli.subprocess.call", fake_call)
    r = CliRunner().invoke(cli.main, ["config", "edit", "demo"])
    assert r.exit_code == 0
    assert called["argv"][0] == "/usr/bin/nano"
    assert called["argv"][1].endswith("demo.yaml")


def test_config_edit_drops_template_for_schema(isolated: Path, monkeypatch):
    """Editing a never-edited toolkit's config drops a template populated
    with defaults and NEEDS_VALUE markers for required fields."""
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string", "default": "localhost"},
        {"name": "api_key", "type": "secret", "required": True},
    ])
    monkeypatch.setenv("EDITOR", "/usr/bin/nano")
    monkeypatch.setattr("toolbase.cli.subprocess.call", lambda argv: 0)

    r = CliRunner().invoke(cli.main, ["config", "edit", "demo", "--user"])
    assert r.exit_code == 0

    cfg = config_path("demo")
    assert cfg.exists()
    data = load_config("demo")
    assert data["host"] == "localhost"
    assert data["api_key"] == NEEDS_VALUE_SENTINEL
