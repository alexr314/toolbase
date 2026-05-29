"""Phase 4 tests — two-layer config resolution + layer-aware CLI surface.

Covers:

- ``setup.storage`` layer-aware functions (read/write project layer,
  default-project special case, schema_version stamping).
- ``setup.declarative.load_state_config`` two-layer merge.
- ``tb config show / set / unset / edit / path / validate`` with the
  ``--layer / --user / --project`` flag set.
- Default-layer selection (project layer in a project, user layer in
  default-project).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml as _yaml
from click.testing import CliRunner

from toolbase import config as toolbase_config
from toolbase import cli
from toolbase.setup import storage, declarative
from toolbase.setup.schema import parse_config_block


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def _drop_manifest(project_dir: Path) -> Path:
    sc = project_dir / ".toolbase"
    sc.mkdir(parents=True, exist_ok=True)
    manifest = sc / "manifest.yaml"
    manifest.write_text("schema_version: 1\ntoolkits: []\n")
    return manifest


def _install_synthetic(
    base: Path,
    name: str = "demo",
    config_block=None,
) -> Path:
    """Drop a minimal cache slot with toolkit.yaml + .tb_meta.json."""
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


# ── storage layer — user vs project ─────────────────────────────────


def test_storage_save_load_project_layer(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)

    storage.save_config(
        "demo", {"api_key": "from-project"},
        layer="project", project_root=project,
    )
    data = storage.load_config(
        "demo", layer="project", project_root=project,
    )
    body = {k: v for k, v in data.items() if k != "schema_version"}
    assert body == {"api_key": "from-project"}
    # And the file is at the expected path.
    assert (project / ".toolbase" / "config" / "demo.yaml").exists()


def test_storage_project_layer_requires_project_root(fake_home):
    with pytest.raises(ValueError, match="requires project_root"):
        storage.config_path("demo", layer="project", project_root=None)


def test_storage_user_and_project_are_independent(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)

    storage.save_config("demo", {"key": "user-value"}, layer="user")
    storage.save_config(
        "demo", {"key": "project-value"},
        layer="project", project_root=project,
    )

    user_data = storage.load_config("demo", layer="user")
    project_data = storage.load_config(
        "demo", layer="project", project_root=project,
    )
    assert user_data["key"] == "user-value"
    assert project_data["key"] == "project-value"


def test_storage_schema_version_stamped_on_save(fake_home):
    storage.save_config("demo", {"x": 1})
    text = storage.config_path("demo").read_text()
    assert "schema_version: 1" in text


def test_storage_schema_version_legacy_v0_files_still_load(fake_home):
    """A legacy file with no schema_version field should still load."""
    cfg = storage.config_path("demo")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("api_key: existing\n")  # no schema_version
    data = storage.load_config("demo")
    assert data["api_key"] == "existing"


def test_storage_refuses_too_new_schema(fake_home):
    """A file claiming schema_version > MAX raises SchemaTooNewError."""
    from toolbase.envs.schema import SchemaTooNewError
    cfg = storage.config_path("demo")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("schema_version: 99\napi_key: x\n")
    with pytest.raises(SchemaTooNewError):
        storage.load_config("demo")


def test_storage_set_unset_on_project_layer(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)

    storage.set_config_value(
        "demo", "key", "v1", layer="project", project_root=project,
    )
    assert storage.load_config(
        "demo", layer="project", project_root=project,
    )["key"] == "v1"

    storage.unset_config_value(
        "demo", "key", layer="project", project_root=project,
    )
    data = storage.load_config(
        "demo", layer="project", project_root=project,
    )
    assert "key" not in data


# ── load_state_config two-layer merge ────────────────────────────────


def test_load_state_config_user_only_path(tmp_path, fake_home):
    schema = parse_config_block([
        {"name": "host", "type": "string", "default": "localhost"},
        {"name": "api_key", "type": "secret", "required": True},
    ])
    storage.save_config("demo", {"host": "user-host", "api_key": "k"})
    res = declarative.load_state_config("demo", schema)
    assert res.ok
    assert res.state_config["host"] == "user-host"
    assert res.state_config["api_key"] == "k"


def test_load_state_config_project_overrides_user(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)

    schema = parse_config_block([
        {"name": "host", "type": "string"},
        {"name": "port", "type": "integer", "default": 8080},
    ])
    storage.save_config("demo", {"host": "user-host", "port": 80})
    storage.save_config(
        "demo", {"host": "project-host"},
        layer="project", project_root=project,
    )
    res = declarative.load_state_config(
        "demo", schema, project_root=project,
    )
    assert res.ok
    assert res.state_config["host"] == "project-host"  # project wins
    assert res.state_config["port"] == 80  # only in user → preserved


def test_load_state_config_project_only_keys_surface(tmp_path, fake_home):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)

    schema = parse_config_block([
        {"name": "extra", "type": "string"},
    ])
    storage.save_config(
        "demo", {"extra": "project-only"},
        layer="project", project_root=project,
    )
    res = declarative.load_state_config(
        "demo", schema, project_root=project,
    )
    assert res.ok
    assert res.state_config["extra"] == "project-only"


def test_load_state_config_needs_value_in_project_surfaces(tmp_path, fake_home):
    """NEEDS_VALUE sentinel in project layer overrides a real user value as data.

    The Phase 1 design note in envs.config says: the sentinel is just a
    string and the resolver doesn't special-case it. If both layers
    have the sentinel, "missing required" fires. If project has the
    sentinel and user has a real value, the project sentinel wins.
    """
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)

    schema = parse_config_block([
        {"name": "k", "type": "string", "required": True},
    ])
    storage.save_config("demo", {"k": "user-real-value"})
    from toolbase.setup import NEEDS_VALUE_SENTINEL
    storage.save_config(
        "demo", {"k": NEEDS_VALUE_SENTINEL},
        layer="project", project_root=project,
    )
    res = declarative.load_state_config(
        "demo", schema, project_root=project,
    )
    assert not res.ok
    assert "k" in res.missing_required


# ── CLI: config show with --layer ───────────────────────────────────


def test_config_show_merged_view_annotates_layers(tmp_path, fake_home, monkeypatch):
    """Default show in a project context = merged view with `# from <layer>`."""
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
        {"name": "port", "type": "integer"},
    ])
    storage.save_config("demo", {"host": "user-host", "port": 80})
    storage.save_config(
        "demo", {"host": "project-host"},
        layer="project", project_root=project,
    )

    monkeypatch.chdir(project)
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert r.exit_code == 0, r.output
    # host: project-host  # from project
    assert "host" in r.output
    assert "project-host" in r.output
    assert "from project" in r.output
    # port came only from user — should be annotated as user.
    assert "from user" in r.output


def test_config_show_layer_user_hides_project(tmp_path, fake_home, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
    ])
    storage.save_config("demo", {"host": "user-host"})
    storage.save_config(
        "demo", {"host": "project-host"},
        layer="project", project_root=project,
    )

    monkeypatch.chdir(project)
    r = CliRunner().invoke(
        cli.main, ["config", "show", "demo", "--layer", "user"],
    )
    assert r.exit_code == 0, r.output
    assert "user-host" in r.output
    assert "project-host" not in r.output


def test_config_show_layer_project_hides_user(tmp_path, fake_home, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
    ])
    storage.save_config("demo", {"host": "user-host"})
    storage.save_config(
        "demo", {"host": "project-host"},
        layer="project", project_root=project,
    )

    monkeypatch.chdir(project)
    r = CliRunner().invoke(
        cli.main, ["config", "show", "demo", "--project"],
    )
    assert r.exit_code == 0, r.output
    assert "project-host" in r.output
    assert "user-host" not in r.output


# ── CLI: config set default-layer rules ─────────────────────────────


def test_config_set_in_project_writes_project_layer_by_default(
    tmp_path, fake_home, monkeypatch,
):
    """In a project context, ``tb config set`` writes to the project layer."""
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
    ])

    monkeypatch.chdir(project)
    r = CliRunner().invoke(
        cli.main, ["config", "set", "demo", "host", "in-project"],
    )
    assert r.exit_code == 0, r.output
    # Project layer file exists with the value.
    project_file = project / ".toolbase" / "config" / "demo.yaml"
    assert project_file.exists()
    text = project_file.read_text()
    assert "in-project" in text
    # User layer file does NOT exist (we created a new project-only file).
    assert not (fake_home / "config" / "demo.yaml").exists()


def test_config_set_outside_project_creates_cwd_project(
    tmp_path, fake_home, monkeypatch,
):
    """No project upward → config writes to a .toolbase/ created in the cwd
    (project-first default), not the user layer."""
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()

    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
    ])

    monkeypatch.chdir(nowhere)
    r = CliRunner().invoke(
        cli.main, ["config", "set", "demo", "host", "cwd-default"],
    )
    assert r.exit_code == 0, r.output
    # Project layer in the cwd's new .toolbase/.
    project_file = nowhere / ".toolbase" / "config" / "demo.yaml"
    assert project_file.exists()
    assert "cwd-default" in project_file.read_text()
    # The user layer is left untouched.
    assert not (fake_home / "config" / "demo.yaml").exists()


def test_config_set_explicit_user_flag_in_project_writes_user(
    tmp_path, fake_home, monkeypatch,
):
    """``--user`` flag overrides the project-context default."""
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
    ])

    monkeypatch.chdir(project)
    r = CliRunner().invoke(
        cli.main,
        ["config", "set", "demo", "host", "explicit-user", "--user"],
    )
    assert r.exit_code == 0, r.output
    # User layer file should be where it landed.
    assert (fake_home / "config" / "demo.yaml").exists()
    # Project layer file should NOT have been created.
    assert not (
        project / ".toolbase" / "config" / "demo.yaml"
    ).exists()


def test_config_set_mutex_layer_flags_error(tmp_path, fake_home, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
    ])
    monkeypatch.chdir(project)
    r = CliRunner().invoke(
        cli.main,
        ["config", "set", "demo", "host", "x", "--user", "--project"],
    )
    assert r.exit_code != 0
    assert "mutually exclusive" in r.output.lower()


def test_config_set_creates_only_override_key_in_project(
    tmp_path, fake_home, monkeypatch,
):
    """Phase 4 Q3 lean (confirmed): a new project-layer file contains ONLY
    the key the user set, not a full copy of the user-layer file."""
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
        {"name": "port", "type": "integer"},
    ])
    storage.save_config("demo", {"host": "user-host", "port": 80})

    monkeypatch.chdir(project)
    r = CliRunner().invoke(
        cli.main, ["config", "set", "demo", "host", "in-project"],
    )
    assert r.exit_code == 0

    project_data = storage.load_config(
        "demo", layer="project", project_root=project,
    )
    body = {k: v for k, v in project_data.items() if k != "schema_version"}
    # Only the override key — port is NOT copied over.
    assert body == {"host": "in-project"}


# ── CLI: config path with --layer ───────────────────────────────────


def test_config_path_in_project_defaults_to_project_file(
    tmp_path, fake_home, monkeypatch,
):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home)

    monkeypatch.chdir(project)
    r = CliRunner().invoke(cli.main, ["config", "path", "demo"])
    assert r.exit_code == 0
    out = r.output.strip()
    # The printed path lives under the project's .toolbase/.
    assert str(project / ".toolbase" / "config") in out


def test_config_path_user_layer_explicit(tmp_path, fake_home, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home)
    monkeypatch.chdir(project)
    r = CliRunner().invoke(
        cli.main, ["config", "path", "demo", "--user"],
    )
    assert r.exit_code == 0
    assert str(fake_home / "config") in r.output.strip()


# ── CLI: config edit with --layer ───────────────────────────────────


def test_config_edit_project_layer_creates_empty_file(
    tmp_path, fake_home, monkeypatch,
):
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string", "required": True},
    ])
    monkeypatch.setenv("EDITOR", "/usr/bin/nano")
    monkeypatch.setattr("toolbase.cli.subprocess.call", lambda argv: 0)
    monkeypatch.chdir(project)

    r = CliRunner().invoke(
        cli.main, ["config", "edit", "demo", "--project"],
    )
    assert r.exit_code == 0, r.output
    project_file = project / ".toolbase" / "config" / "demo.yaml"
    assert project_file.exists()
    # Project layer template is empty — no NEEDS_VALUE flooding.
    text = project_file.read_text()
    # schema_version stamped, but no host: <NEEDS VALUE>.
    assert "schema_version" in text
    assert "<NEEDS VALUE>" not in text


# ── CLI: project-dir override impacts config commands ──────────────


def test_config_set_with_project_dir_override(tmp_path, fake_home, monkeypatch):
    forced = tmp_path / "elsewhere"
    forced.mkdir()
    _drop_manifest(forced)
    _install_synthetic(fake_home, config_block=[
        {"name": "host", "type": "string"},
    ])

    nowhere = tmp_path / "actual-cwd"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)

    r = CliRunner().invoke(
        cli.main,
        ["--project-dir", str(forced),
         "config", "set", "demo", "host", "v1"],
    )
    assert r.exit_code == 0, r.output
    project_file = forced / ".toolbase" / "config" / "demo.yaml"
    assert project_file.exists()
    assert "v1" in project_file.read_text()


# ── CLI: config validate uses merged view ──────────────────────────


def test_config_validate_uses_merged_view(tmp_path, fake_home, monkeypatch):
    """Required field missing in user but supplied in project = OK."""
    project = tmp_path / "proj"
    project.mkdir()
    _drop_manifest(project)
    _install_synthetic(fake_home, config_block=[
        {"name": "api_key", "type": "secret", "required": True},
    ])
    # User layer doesn't have the key.
    # Project layer supplies it.
    storage.save_config(
        "demo", {"api_key": "real"},
        layer="project", project_root=project,
    )
    monkeypatch.chdir(project)
    r = CliRunner().invoke(cli.main, ["config", "validate", "demo"])
    assert r.exit_code == 0, r.output
    assert "valid" in r.output.lower()
