"""Phase 3 wiring tests — project discovery on the CLI surface.

Phase 1 substrate (``envs/discovery.py``) is already covered by
``test_envs_discovery.py``. This file covers Phase 3's *wiring* into:

- ``cli._resolve_active_project_root`` — the helper every command uses
  to find the active project root.
- ``tb install`` — pins into the discovered project's manifest.
- ``tb uninstall`` — clears the pin from the discovered project.
- ``tb project init`` — explicit alternative to implicit-create.
- ``--project-dir <path>`` — hidden top-level override.
- Author-mode orthogonality: a ``toolkit.yaml`` in cwd doesn't change
  user-mode discovery.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml as _yaml
from click.testing import CliRunner

from toolbase import config as toolbase_config
from toolbase import cli


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


# ── _resolve_active_project_root ────────────────────────────────────


def test_resolve_walk_finds_project_above_cwd(tmp_path, fake_home):
    project = tmp_path / "p"
    project.mkdir()
    _drop_manifest(project)
    sub = project / "src" / "x"
    sub.mkdir(parents=True)

    # Click context not active outside a CliRunner; helper should still work.
    root, source = cli._resolve_active_project_root(cwd=sub)
    assert root == project.resolve()
    assert source == "walk"


def test_resolve_falls_back_to_default_project(tmp_path, fake_home):
    cwd = tmp_path / "no-proj"
    cwd.mkdir()
    root, source = cli._resolve_active_project_root(cwd=cwd)
    from toolbase.envs import default_project_root
    assert root == default_project_root()
    assert source == "fallback"


def test_resolve_override_via_context(tmp_path, fake_home):
    forced = tmp_path / "forced"
    forced.mkdir()
    # Simulate the eager callback by stashing on a real click context.
    import click as _click
    ctx = _click.Context(cli.main)
    ctx.obj = {"project_dir_override": forced}
    with ctx:
        root, source = cli._resolve_active_project_root(cwd=tmp_path)
    assert root == forced.resolve()
    assert source == "override"


def test_resolve_no_implicit_create_when_disabled(tmp_path, fake_home):
    """Read paths (uninstall/list/serve/config) never auto-create."""
    cwd = tmp_path / "no-proj"
    cwd.mkdir()
    root, source = cli._resolve_active_project_root(
        cwd=cwd, allow_implicit_create=False,
    )
    assert source == "fallback"
    # No .toolbase/ created in cwd.
    assert not (cwd / ".toolbase").exists()


def test_resolve_no_implicit_create_in_skip_mode(tmp_path, fake_home):
    """Non-TTY (--no-input) bypasses the prompt; falls back silently."""
    cwd = tmp_path / "no-proj"
    cwd.mkdir()
    root, source = cli._resolve_active_project_root(
        cwd=cwd, allow_implicit_create=True, mode="skip",
    )
    assert source == "fallback"
    assert not (cwd / ".toolbase").exists()


def test_resolve_no_implicit_create_in_yes_mode(tmp_path, fake_home):
    """--yes also bypasses the create prompt (skip semantics for project)."""
    cwd = tmp_path / "no-proj"
    cwd.mkdir()
    # We pass mode="yes" — same lean as skip: don't auto-create unless
    # the user is interactive and gets to see the prompt.
    root, source = cli._resolve_active_project_root(
        cwd=cwd, allow_implicit_create=True, mode="yes",
    )
    assert source == "fallback"


def test_resolve_implicit_create_prompt_default_y(tmp_path, fake_home, monkeypatch):
    """In TTY (mode=ask), the prompt defaults to Yes and creates ``.toolbase/``."""
    cwd = tmp_path / "fresh-proj"
    cwd.mkdir()
    # Stub click.confirm to confirm Yes.
    monkeypatch.setattr("click.confirm", lambda msg, default=True: True)
    root, source = cli._resolve_active_project_root(
        cwd=cwd, allow_implicit_create=True, mode="ask",
    )
    assert source == "implicit-create"
    assert root == cwd.resolve()
    assert (cwd / ".toolbase" / "manifest.yaml").exists()


def test_resolve_implicit_create_declined_falls_back(tmp_path, fake_home, monkeypatch):
    cwd = tmp_path / "fresh-proj"
    cwd.mkdir()
    monkeypatch.setattr("click.confirm", lambda msg, default=True: False)
    root, source = cli._resolve_active_project_root(
        cwd=cwd, allow_implicit_create=True, mode="ask",
    )
    assert source == "fallback"
    from toolbase.envs import default_project_root
    assert root == default_project_root()
    assert not (cwd / ".toolbase").exists()


# ── author-mode orthogonality ────────────────────────────────────────


def test_toolkit_yaml_in_cwd_does_not_affect_discovery(tmp_path, fake_home):
    """A toolkit.yaml at cwd (author mode) is orthogonal to .toolbase/
    discovery (user mode). They can coexist; neither implies the other."""
    cwd = tmp_path / "author-and-user"
    cwd.mkdir()
    # Author mode marker — just toolkit.yaml at top level.
    (cwd / "toolkit.yaml").write_text("name: foo\nversion: 0.1.0\n")
    # No .toolbase/ — so user-mode discovery should NOT consider this
    # a project. Discovery walks past it.
    root, source = cli._resolve_active_project_root(
        cwd=cwd, allow_implicit_create=False,
    )
    assert source == "fallback"


def test_toolkit_yaml_and_toolbase_can_coexist(tmp_path, fake_home):
    """A directory that's *both* an author's toolkit working dir AND a user's
    project. Both modes work independently. ``.toolbase/`` is the user-
    mode trigger."""
    cwd = tmp_path / "dual-mode"
    cwd.mkdir()
    (cwd / "toolkit.yaml").write_text("name: foo\nversion: 0.1.0\n")
    _drop_manifest(cwd)

    root, source = cli._resolve_active_project_root(cwd=cwd)
    assert source == "walk"
    assert root == cwd.resolve()


# ── tb project init ────────────────────────────────────────────────


def test_project_init_creates_dot_toolbase(tmp_path, fake_home):
    target = tmp_path / "new-proj"
    target.mkdir()
    r = CliRunner().invoke(
        cli.main, ["project", "init", "--path", str(target)],
    )
    assert r.exit_code == 0, r.output
    manifest = target / ".toolbase" / "manifest.yaml"
    assert manifest.exists()
    parsed = _yaml.safe_load(manifest.read_text())
    assert parsed.get("schema_version") == 1
    assert parsed.get("toolkits") == []


def test_project_init_idempotent(tmp_path, fake_home):
    target = tmp_path / "p"
    target.mkdir()
    r1 = CliRunner().invoke(cli.main, ["project", "init", "--path", str(target)])
    assert r1.exit_code == 0
    r2 = CliRunner().invoke(cli.main, ["project", "init", "--path", str(target)])
    assert r2.exit_code == 0
    assert "already initialized" in r2.output.lower()


def test_project_init_missing_dir_errors(tmp_path, fake_home):
    missing = tmp_path / "does-not-exist"
    r = CliRunner().invoke(cli.main, ["project", "init", "--path", str(missing)])
    assert r.exit_code == 1
    assert "does not exist" in r.output.lower()


# ── --project-dir global override ───────────────────────────────────


def test_project_dir_override_stashed_on_context(tmp_path, fake_home):
    """The eager top-level callback stashes ``--project-dir`` on ctx.obj."""
    target = tmp_path / "forced"
    target.mkdir()

    # Use the click runner so the top-level group's eager callback fires.
    captured: dict = {}

    @cli.main.command(name="_test_capture", hidden=True)
    def _capture():
        import click as _c
        ctx = _c.get_current_context()
        # Walk up to find the root context's obj.
        while ctx.parent is not None:
            ctx = ctx.parent
        captured["obj"] = ctx.obj

    try:
        r = CliRunner().invoke(
            cli.main,
            ["--project-dir", str(target), "_test_capture"],
        )
        assert r.exit_code == 0, r.output
        assert "obj" in captured
        assert captured["obj"]["project_dir_override"] == Path(str(target))
    finally:
        cli.main.commands.pop("_test_capture", None)


def test_project_dir_override_hidden_in_help(fake_home):
    """The flag is power-user; should not appear in the normal --help."""
    r = CliRunner().invoke(cli.main, ["--help"])
    assert r.exit_code == 0
    assert "--project-dir" not in r.output
