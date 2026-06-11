"""The project-local config layer (config/<toolkit>.local.yaml).

Project scope and git-shareability were welded together in the config
stack: machine truth (absolute tool paths) either leaked into the
committed project file or had to be mis-scoped to the user layer. The
local layer separates them — project-scoped, gitignored, highest
precedence — completing the symmetry with manifest.local.yaml. Pins:

  - config_path dispatches layer="local" to <toolkit>.local.yaml
  - merge order: user < project < local, in both resolvers
    (envs.resolve_toolkit_config and setup.load_state_config — the
    serve gate and host injection paths)
  - tb config set --local writes the file and self-gitignores
  - tb config set --local/--project/--user are mutually exclusive
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from toolbase import cli
from toolbase.setup.storage import config_path


def _write_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"schema_version": 1, **data}))


def _layers(tmp_path: Path):
    """(user_base, project_root) with .toolbase/config dirs ready."""
    user = tmp_path / "userhome"
    proj = tmp_path / "repo"
    (user / "config").mkdir(parents=True)
    (proj / ".toolbase" / "config").mkdir(parents=True)
    return user, proj


# ── path dispatch ──────────────────────────────────────────────────────────


def test_config_path_local_layer(tmp_path):
    p = config_path("heptapod", layer="local", project_root=tmp_path)
    assert p == tmp_path / ".toolbase" / "config" / "heptapod.local.yaml"


def test_config_path_local_requires_project_root():
    with pytest.raises(ValueError, match="requires project_root"):
        config_path("heptapod", layer="local")


# ── merge order in both resolvers ─────────────────────────────────────────


def test_resolver_merge_order(tmp_path):
    from toolbase.envs.config import resolve_toolkit_config
    user, proj = _layers(tmp_path)
    _write_yaml(user / "config" / "kit.yaml",
                {"a": "user", "b": "user", "c": "user"})
    _write_yaml(proj / ".toolbase" / "config" / "kit.yaml",
                {"b": "project", "c": "project"})
    _write_yaml(proj / ".toolbase" / "config" / "kit.local.yaml",
                {"c": "local"})
    merged = resolve_toolkit_config("kit", proj, user_base=user)
    assert merged == {"a": "user", "b": "project", "c": "local"}


def test_state_config_sees_local_layer(tmp_path, monkeypatch):
    # The serve-time path (bundle gates + host --state-config) must see
    # the same merge, or a path set with --local wouldn't satisfy a
    # bundle's `requires:` gate.
    from toolbase.setup import parse_config_block
    from toolbase.setup.declarative import load_state_config
    user, proj = _layers(tmp_path)
    monkeypatch.setattr("toolbase.setup.storage.config_dir",
                        lambda base=None: user / "config")
    _write_yaml(proj / ".toolbase" / "config" / "kit.local.yaml",
                {"delphes_path": "/srv/delphes"})
    schema = parse_config_block(
        [{"name": "delphes_path", "type": "string", "required": False}])
    res = load_state_config("kit", schema, project_root=proj)
    assert res.ok
    assert res.state_config["delphes_path"] == "/srv/delphes"


# ── CLI ────────────────────────────────────────────────────────────────────


@pytest.fixture
def project_cwd(tmp_path, monkeypatch):
    proj = tmp_path / "repo"
    (proj / ".toolbase" / "config").mkdir(parents=True)
    monkeypatch.chdir(proj)
    # config set resolves the toolkit for schema validation; tolerate
    # an unknown toolkit (warns, stores raw).
    monkeypatch.setattr(cli, "_resolve_toolkit_for_config",
                        lambda name: (None, None))
    return proj


def test_config_set_local_writes_and_gitignores(project_cwd):
    result = CliRunner().invoke(
        cli.main,
        ["config", "set", "kit", "delphes_path", "/srv/delphes", "--local"])
    assert result.exit_code == 0, result.output
    f = project_cwd / ".toolbase" / "config" / "kit.local.yaml"
    assert yaml.safe_load(f.read_text())["delphes_path"] == "/srv/delphes"
    gi = (project_cwd / ".toolbase" / ".gitignore").read_text()
    assert "config/*.local.yaml" in gi and "manifest.local.yaml" in gi
    # Committed project file untouched.
    assert not (project_cwd / ".toolbase" / "config" / "kit.yaml").exists()


def test_layer_flags_mutually_exclusive(project_cwd):
    result = CliRunner().invoke(
        cli.main,
        ["config", "set", "kit", "k", "v", "--local", "--project"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
