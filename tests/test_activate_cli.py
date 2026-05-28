"""CLI-level tests for ``tb activate`` / ``tb deactivate`` and the
install/uninstall profile hooks (nothing-active model).

Uses the standard CONFIG_DIR monkeypatch + a synthetic cache so the
commands see "installed" toolkits without a real install.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / ".toolbase"
    fake.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def _fake_install(base: Path, name: str, version: str = "1.0.0") -> None:
    slot = base / "cache" / name / version
    slot.mkdir(parents=True, exist_ok=True)
    (slot / ".install_meta.yaml").write_text(
        "schema_version: 1\ninstall_method: venv\npython_version: '3.12'\n"
    )
    (slot / ".tb_meta.json").write_text(f'{{"name": "{name}"}}\n')


def _default_profile(base: Path) -> dict:
    p = base / "profiles" / "default.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def test_activate_toolkit_writes_user_default(isolated: Path):
    _fake_install(isolated, "heptapod")
    r = CliRunner().invoke(cli.main, ["activate", "heptapod", "-g"])
    assert r.exit_code == 0, r.output
    data = _default_profile(isolated)
    assert "heptapod" in data["toolkits"]


def test_activate_bundle_narrows(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-g"])
    r = CliRunner().invoke(cli.main, ["activate", "heptapod/pythia", "-g"])
    assert r.exit_code == 0, r.output
    data = _default_profile(isolated)
    assert data["toolkits"]["heptapod"]["bundles"] == ["pythia"]


def test_activate_not_installed_errors(isolated: Path):
    r = CliRunner().invoke(cli.main, ["activate", "ghost", "-g"])
    assert r.exit_code == 1
    assert "not installed" in r.output


def test_deactivate_removes_entry(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-g"])
    r = CliRunner().invoke(cli.main, ["deactivate", "heptapod", "-g"])
    assert r.exit_code == 0, r.output
    data = _default_profile(isolated)
    assert "heptapod" not in (data.get("toolkits") or {})


def test_list_marks_active_and_inactive(isolated: Path):
    _fake_install(isolated, "heptapod")
    _fake_install(isolated, "aster")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-g"])
    r = CliRunner().invoke(cli.main, ["list"])
    assert r.exit_code == 0, r.output
    # heptapod active, aster inactive
    assert "heptapod" in r.output and "aster" in r.output
    assert "Active profile" in r.output


def test_list_json_has_active_field(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-g"])
    r = CliRunner().invoke(cli.main, ["list", "--json"])
    assert r.exit_code == 0, r.output
    import json
    payload = json.loads(r.output)
    entry = next(e for e in payload if e["name"] == "heptapod")
    assert entry["active"] is True


def test_post_install_activate_helper(isolated: Path):
    _fake_install(isolated, "heptapod")
    cli._post_install_activate("heptapod", local_scope=False)
    data = _default_profile(isolated)
    assert "heptapod" in data["toolkits"]


def test_uninstall_cleanup_profiles_helper(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-g"])
    assert "heptapod" in _default_profile(isolated)["toolkits"]
    cli._uninstall_cleanup_profiles("heptapod")
    assert "heptapod" not in (_default_profile(isolated).get("toolkits") or {})


def test_profile_list_and_set_default(isolated: Path):
    _fake_install(isolated, "heptapod")
    CliRunner().invoke(cli.main, ["activate", "heptapod", "-g"])
    CliRunner().invoke(cli.main, ["profile", "create", "paper", "-g", "--empty"])
    r = CliRunner().invoke(cli.main, ["profile", "set-default", "paper", "-g"])
    assert r.exit_code == 0, r.output
    serve_yaml = yaml.safe_load((isolated / "serve.yaml").read_text())
    assert serve_yaml["default"]["profile"] == "paper"
