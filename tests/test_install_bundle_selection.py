"""Tests for install-time bundle selection (`tb install foo[a,b]`).

Layered:

1. ``_parse_bundle_extras`` — pure parser for the pip-style suffix.
2. Manifest/cache round-trip — ``ManifestEntry.bundles`` and
   ``installed_bundles`` / ``update_install_meta_bundles``.
3. End-to-end install — exercises the install command with a fake
   pip-build (so the test is fast + offline), covering fresh subset
   install, additive add, noop on repeat, --rebuild, and unknown-bundle
   rejection. Mirrors the test_install_flags.py pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest
import yaml
from click.testing import CliRunner

from toolbase import cli


# ── _parse_bundle_extras: pure parser ─────────────────────────────────


def test_no_extras_returns_none():
    name, bundles = cli._parse_bundle_extras("aster")
    assert name == "aster"
    assert bundles is None


def test_single_bundle_extras():
    name, bundles = cli._parse_bundle_extras("aster[basic]")
    assert name == "aster"
    assert bundles == ["basic"]


def test_multi_bundle_extras():
    name, bundles = cli._parse_bundle_extras("aster[basic,scientific]")
    assert name == "aster"
    assert bundles == ["basic", "scientific"]


def test_extras_with_spaces_around_commas():
    name, bundles = cli._parse_bundle_extras("aster[ basic , scientific ]")
    assert name == "aster"
    assert bundles == ["basic", "scientific"]


def test_extras_empty_brackets_is_empty_list():
    """``foo[]`` is valid: base only, no optional bundles."""
    name, bundles = cli._parse_bundle_extras("aster[]")
    assert name == "aster"
    assert bundles == []


def test_extras_path_form():
    """Local paths can carry extras too."""
    name, bundles = cli._parse_bundle_extras("./local-toolkit[alpha]")
    assert name == "./local-toolkit"
    assert bundles == ["alpha"]


def test_empty_name_rejected():
    import click
    with pytest.raises(click.UsageError, match="Empty toolkit name"):
        cli._parse_bundle_extras("[basic]")


def test_invalid_bundle_name_rejected():
    import click
    with pytest.raises(click.UsageError, match="alphanumeric"):
        cli._parse_bundle_extras("aster[bad name!]")


def test_trailing_comma_tolerated():
    """``foo[a,b,]`` is the same as ``foo[a,b]`` — empty entries dropped."""
    name, bundles = cli._parse_bundle_extras("aster[basic,]")
    assert bundles == ["basic"]


# ── ManifestEntry: bundles round-trip ─────────────────────────────────


def test_manifest_entry_default_bundles_is_none():
    from toolbase.envs.manifest import ManifestEntry
    e = ManifestEntry(name="aster", version="1.0.0")
    assert e.bundles is None
    # to_dict omits the field for backward compat with existing manifests.
    assert "bundles" not in e.to_dict()


def test_manifest_entry_with_bundles_round_trip():
    from toolbase.envs.manifest import ManifestEntry
    e = ManifestEntry(
        name="aster", version="1.0.0",
        bundles=["scientific", "basic"],
    )
    d = e.to_dict()
    # Sorted on emission for stable diffs.
    assert d["bundles"] == ["basic", "scientific"]
    e2 = ManifestEntry.from_dict(d)
    assert e2.bundles == ["basic", "scientific"]


def test_manifest_entry_bundles_empty_list_preserved():
    """``bundles: []`` means "base only, no optional bundles" — distinct
    from absent (which means "all bundles")."""
    from toolbase.envs.manifest import ManifestEntry
    e = ManifestEntry(name="aster", version="1.0.0", bundles=[])
    d = e.to_dict()
    assert d["bundles"] == []
    e2 = ManifestEntry.from_dict(d)
    assert e2.bundles == []


def test_manifest_entry_bundles_legacy_load_no_field():
    """Old manifest entries without a ``bundles`` field load with bundles=None."""
    from toolbase.envs.manifest import ManifestEntry
    e = ManifestEntry.from_dict({
        "name": "aster", "version": "1.0.0", "pinned_at": "",
    })
    assert e.bundles is None


def test_manifest_entry_bundles_invalid_type_rejected():
    from toolbase.envs.manifest import ManifestEntry
    with pytest.raises(ValueError, match="must be a list"):
        ManifestEntry.from_dict({
            "name": "aster", "version": "1.0.0", "bundles": "not-a-list",
        })


# ── cache.installed_bundles / update_install_meta_bundles ─────────────


def test_installed_bundles_absent_means_none(tmp_path: Path):
    from toolbase.envs.cache import (
        write_install_meta, installed_bundles,
    )
    write_install_meta(
        tmp_path,
        name="x", version="1.0.0",
        install_method="venv", python_version="3.12",
    )
    # No bundles field written → installed_bundles returns None ("all").
    assert installed_bundles(tmp_path) is None


def test_installed_bundles_round_trips_list(tmp_path: Path):
    from toolbase.envs.cache import (
        write_install_meta, installed_bundles,
    )
    write_install_meta(
        tmp_path,
        name="x", version="1.0.0",
        install_method="venv", python_version="3.12",
        extras={"bundles": ["alpha", "beta"]},
    )
    assert installed_bundles(tmp_path) == ["alpha", "beta"]


def test_update_install_meta_bundles_replaces_field(tmp_path: Path):
    from toolbase.envs.cache import (
        write_install_meta, update_install_meta_bundles, installed_bundles,
    )
    write_install_meta(
        tmp_path,
        name="x", version="1.0.0",
        install_method="venv", python_version="3.12",
        extras={"bundles": ["alpha"]},
    )
    update_install_meta_bundles(tmp_path, ["alpha", "beta"])
    assert installed_bundles(tmp_path) == ["alpha", "beta"]


def test_update_install_meta_bundles_sorts_and_dedupes(tmp_path: Path):
    from toolbase.envs.cache import (
        write_install_meta, update_install_meta_bundles, installed_bundles,
    )
    write_install_meta(
        tmp_path,
        name="x", version="1.0.0",
        install_method="venv", python_version="3.12",
    )
    update_install_meta_bundles(tmp_path, ["gamma", "alpha", "alpha", "beta"])
    assert installed_bundles(tmp_path) == ["alpha", "beta", "gamma"]


def test_update_install_meta_bundles_noop_when_no_meta(tmp_path: Path):
    """Safe to call when slot has no .install_meta.yaml; does nothing."""
    from toolbase.envs.cache import update_install_meta_bundles
    update_install_meta_bundles(tmp_path, ["alpha"])
    assert not (tmp_path / ".install_meta.yaml").exists()


# ── tool_is_served install-time gating ────────────────────────────────


def test_tool_is_served_passes_when_installed_bundles_is_none():
    """Backward compat: no install-time gating when installed_bundles=None."""
    from toolbase.serve.bundles import BundleAvailability
    from toolbase.serve.profiles import tool_is_served
    av = BundleAvailability(
        available_bundles=["alpha"], dropped_bundles={},
        has_bundles_block=True,
    )
    assert tool_is_served(
        "t", ["alpha"], None, av, set(), installed_bundles=None,
    )


def test_tool_is_served_excludes_when_bundle_not_installed():
    """A tool's bundles must intersect installed_bundles when set."""
    from toolbase.serve.bundles import BundleAvailability
    from toolbase.serve.profiles import tool_is_served
    av = BundleAvailability(
        available_bundles=["alpha", "beta"], dropped_bundles={},
        has_bundles_block=True,
    )
    # Tool in [beta], installed_bundles={alpha} → excluded.
    assert not tool_is_served(
        "t", ["beta"], None, av, set(),
        installed_bundles={"alpha"},
    )
    # Tool in [alpha, beta], installed_bundles={alpha} → served (any).
    assert tool_is_served(
        "t", ["alpha", "beta"], None, av, set(),
        installed_bundles={"alpha"},
    )


def test_tool_is_served_bundle_less_tool_always_passes_install_gate():
    """A tool with no declared bundles is always installed (no extras
    gating it); it passes the install-time check regardless of the set."""
    from toolbase.serve.bundles import BundleAvailability
    from toolbase.serve.profiles import tool_is_served
    av = BundleAvailability(
        available_bundles=[], dropped_bundles={},
        has_bundles_block=False,
    )
    assert tool_is_served(
        "t", [], None, av, set(),
        installed_bundles={"alpha"},
    )


def test_tool_is_served_empty_installed_bundles_excludes_bundled_tools():
    """``installed_bundles=set()`` (= base-only install) excludes every
    bundle-aware tool, leaves bundle-less tools served."""
    from toolbase.serve.bundles import BundleAvailability
    from toolbase.serve.profiles import tool_is_served
    av = BundleAvailability(
        available_bundles=["alpha"], dropped_bundles={},
        has_bundles_block=True,
    )
    assert not tool_is_served(
        "t", ["alpha"], None, av, set(),
        installed_bundles=set(),
    )
    assert tool_is_served(
        "loose", [], None, av, set(),
        installed_bundles=set(),
    )


# ── end-to-end install with bundle selection ─────────────────────────


def _make_source_toolkit(
    src: Path, name: str = "demo",
    bundles_block: Optional[dict] = None,
    tool_bundles: Optional[dict] = None,
) -> Path:
    """Synthesize a minimal toolkit source tree under ``src``."""
    src.mkdir(parents=True, exist_ok=True)
    config = {
        "name": name,
        "version": "0.1.0",
        "description": "x",
        "author": "tester",
        "category": "other",
        "tools": [],
    }
    # Add one tool per bundle, with a bundle-less tool too.
    tool_bundles = tool_bundles or {}
    for tname, b in tool_bundles.items():
        entry = {
            "name": tname,
            "module": f"tools.{tname}",
            "description": "t",
        }
        if b is not None:
            entry["bundle"] = b
        config["tools"].append(entry)
    if bundles_block is not None:
        config["bundles"] = bundles_block
    (src / "toolkit.yaml").write_text(yaml.safe_dump(config))
    (src / "requirements.txt").write_text("")
    return src


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch):
    """Same shape as test_install_flags.py's fake_env: redirect CONFIG_DIR
    and Claude-skills dir into tmp_path, and stub the venv builder so
    tests are fast + offline."""
    from toolbase import config as cfg_mod
    from toolbase import skills as skills_mod
    fake_home = tmp_path / "_home" / ".toolbase"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_claude = tmp_path / "claude-skills"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_home)
    monkeypatch.setattr(skills_mod, "CLAUDE_SKILLS_DIR", fake_claude)
    captured: dict = {"extra_pip_specs_calls": []}

    def fake_setup_venv(toolkit_path: Path, console, *, extra_pip_specs=None):
        import os as _os
        venv = Path(toolkit_path) / ".venv"
        (venv / "bin").mkdir(parents=True, exist_ok=True)
        py = venv / "bin" / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
        pip = venv / "bin" / "pip"
        # Fake pip that just exits 0 — the additive-install subprocess
        # invokes this directly, so it has to be executable.
        pip.write_text('#!/bin/sh\nexit 0\n')
        pip.chmod(0o755)
        captured["extra_pip_specs_calls"].append(list(extra_pip_specs or []))
        return py

    monkeypatch.setattr(cli, "setup_venv_environment", fake_setup_venv)
    return {"home": fake_home, "claude": fake_claude, "captured": captured}


def test_install_fresh_subset_records_bundles(fake_env, tmp_path: Path):
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={
            "alpha": {"deps": ["dep-a"]},
            "beta": {"deps": ["dep-b"]},
        },
        tool_bundles={"ta": "alpha", "tb": "beta"},
    )
    r = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    # Bundle deps got passed to the venv builder.
    assert fake_env["captured"]["extra_pip_specs_calls"] == [["dep-a"]]
    # Cache metadata records the installed bundle.
    from toolbase.envs.cache import installed_bundles
    slot = fake_env["home"] / "cache" / "demo" / "0.1.0"
    assert installed_bundles(slot) == ["alpha"]


def test_install_full_install_omits_bundles_field(fake_env, tmp_path: Path):
    """Bare ``tb install foo`` keeps the historical behavior: install
    every declared bundle, but record NO ``bundles`` field (= "all")."""
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={
            "alpha": {"deps": ["dep-a"]},
            "beta": {"deps": ["dep-b"]},
        },
        tool_bundles={"ta": "alpha", "tb": "beta"},
    )
    r = CliRunner().invoke(
        cli.main, ["install", str(src), "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    # Both bundles' deps pip-installed.
    assert sorted(fake_env["captured"]["extra_pip_specs_calls"][0]) == [
        "dep-a", "dep-b",
    ]
    from toolbase.envs.cache import installed_bundles
    slot = fake_env["home"] / "cache" / "demo" / "0.1.0"
    # None = "all bundles installed" — the field is absent.
    assert installed_bundles(slot) is None


def test_install_unknown_bundle_rejected(fake_env, tmp_path: Path):
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={"alpha": {"deps": ["dep-a"]}},
        tool_bundles={"ta": "alpha"},
    )
    r = CliRunner().invoke(
        cli.main, ["install", f"{src}[ghost]", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code != 0
    assert "Unknown bundle" in r.output


def test_install_bundle_flag_form_equivalent_to_extras(fake_env, tmp_path: Path):
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={
            "alpha": {"deps": ["dep-a"]},
            "beta": {"deps": ["dep-b"]},
        },
        tool_bundles={"ta": "alpha", "tb": "beta"},
    )
    r = CliRunner().invoke(
        cli.main, ["install", str(src), "--bundle", "beta", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    assert fake_env["captured"]["extra_pip_specs_calls"] == [["dep-b"]]


def test_install_extras_and_bundle_flag_mutually_exclusive(fake_env, tmp_path: Path):
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={"alpha": {"deps": ["dep-a"]}},
        tool_bundles={"ta": "alpha"},
    )
    r = CliRunner().invoke(
        cli.main,
        ["install", f"{src}[alpha]", "--bundle", "alpha", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code != 0
    assert "extras-form" in r.output.lower() or "mutually exclusive" in r.output.lower()


def test_install_additive_re_install_extends_bundles(fake_env, tmp_path: Path):
    """Re-installing with a new bundle adds it to the existing slot
    (pip-installs just the new deps; metadata reflects the union)."""
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={
            "alpha": {"deps": ["dep-a"]},
            "beta": {"deps": ["dep-b"]},
        },
        tool_bundles={"ta": "alpha", "tb": "beta"},
    )
    # First install: just alpha.
    r1 = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r1.exit_code == 0, r1.output

    # Second install: add beta. Additive — doesn't blow away the slot.
    r2 = CliRunner().invoke(
        cli.main, ["install", f"{src}[beta]", "--no-input"],
        catch_exceptions=False,
    )
    assert r2.exit_code == 0, r2.output
    assert "Adding bundle" in r2.output

    from toolbase.envs.cache import installed_bundles
    slot = fake_env["home"] / "cache" / "demo" / "0.1.0"
    assert installed_bundles(slot) == ["alpha", "beta"]


def test_install_repeat_same_subset_noop(fake_env, tmp_path: Path):
    """Re-installing the same bundle set says "already installed"."""
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={"alpha": {"deps": ["dep-a"]}},
        tool_bundles={"ta": "alpha"},
    )
    r1 = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r1.exit_code == 0
    r2 = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r2.exit_code == 0
    assert "already" in r2.output.lower()


def test_install_rebuild_destructively_reinstalls(fake_env, tmp_path: Path):
    """``--rebuild`` blows the slot away regardless of current install state."""
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={
            "alpha": {"deps": ["dep-a"]},
            "beta": {"deps": ["dep-b"]},
        },
        tool_bundles={"ta": "alpha", "tb": "beta"},
    )
    # First install with [alpha, beta].
    r1 = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha,beta]", "--no-input"],
        catch_exceptions=False,
    )
    assert r1.exit_code == 0

    # --rebuild with just [alpha] → fresh install, scopes back down.
    r2 = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--rebuild", "--no-input"],
        catch_exceptions=False,
    )
    assert r2.exit_code == 0, r2.output

    from toolbase.envs.cache import installed_bundles
    slot = fake_env["home"] / "cache" / "demo" / "0.1.0"
    assert installed_bundles(slot) == ["alpha"]


def test_install_records_bundles_in_manifest(fake_env, tmp_path: Path):
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={
            "alpha": {"deps": ["dep-a"]},
            "beta": {"deps": ["dep-b"]},
        },
        tool_bundles={"ta": "alpha", "tb": "beta"},
    )
    r = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    # Manifest entry should record the subset.
    from toolbase.envs.manifest import load_manifest
    from toolbase.envs.paths import project_manifest_path
    manifest_path = project_manifest_path(fake_env["home"] / "default-project")
    m = load_manifest(manifest_path)
    entry = m.find("demo")
    assert entry is not None
    assert entry.bundles == ["alpha"]


# ────────────────────────────────────────────────────────────────────
# Interrupt safety: half-built slots get cleaned up
# ────────────────────────────────────────────────────────────────────


def test_install_keyboardinterrupt_cleans_partial_slot(tmp_path: Path, monkeypatch):
    """A Ctrl-C during pip install (simulated via KeyboardInterrupt from
    ``setup_venv_environment``) must remove the partially-built cache slot.

    Otherwise the next ``tb install`` finds a slot with no
    ``.install_meta.yaml``, mis-detects it as "already installed with all
    bundles", and silently no-ops on the user's subset request.
    """
    from toolbase import config as cfg_mod
    from toolbase import skills as skills_mod
    fake_home = tmp_path / "_home" / ".toolbase"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_home)
    monkeypatch.setattr(skills_mod, "CLAUDE_SKILLS_DIR", tmp_path / "claude-skills")

    def fake_setup_venv_interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, "setup_venv_environment", fake_setup_venv_interrupted)

    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={"alpha": {"deps": ["dep-a"]}},
        tool_bundles={"ta": "alpha"},
    )
    slot = fake_home / "cache" / "demo" / "0.1.0"

    # Click converts KeyboardInterrupt to exit-code-1 + "Aborted!" output;
    # the contract we care about is that the finally block ran and the slot
    # is gone before the abort.
    r = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code != 0
    assert "Aborted" in r.output
    assert not slot.exists(), (
        "partial cache slot survived a KeyboardInterrupt — future installs "
        "would mis-detect it as a complete install"
    )


def test_install_recovers_from_corrupted_slot(fake_env, tmp_path: Path):
    """A pre-existing cache slot with no ``.install_meta.yaml`` is
    recognised as half-built (interrupted prior install) and replaced
    rather than silently no-op'd as "already installed with all bundles".

    Without this guard, the user-facing symptom is `tb install foo[a,b]`
    appearing to succeed but actually doing nothing — and serve-time
    filtering still surfaces every tool because `installed_bundles()`
    returns ``None`` for a missing meta, equivalent to "all bundles
    installed".
    """
    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={
            "alpha": {"deps": ["dep-a"]},
            "beta": {"deps": ["dep-b"]},
        },
        tool_bundles={"ta": "alpha", "tb": "beta"},
    )

    # Fabricate a half-built slot the way a Ctrl-C'd prior install would
    # leave it: source files present, no .install_meta.yaml, no real venv.
    slot = fake_env["home"] / "cache" / "demo" / "0.1.0"
    slot.mkdir(parents=True)
    (slot / "toolkit.yaml").write_text((src / "toolkit.yaml").read_text())
    (slot / "marker.txt").write_text("leftover-from-broken-install")
    assert not (slot / ".install_meta.yaml").exists()

    r = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    # User sees a clear explanation of what happened.
    assert "missing" in r.output.lower() and "install_meta" in r.output.lower()

    # The leftover marker is gone — the slot was rebuilt from scratch.
    assert not (slot / "marker.txt").exists()
    # The new install has the expected scope.
    from toolbase.envs.cache import installed_bundles
    assert installed_bundles(slot) == ["alpha"]
    # And bundle filtering at install-time worked — only alpha's deps
    # were pip-installed.
    assert fake_env["captured"]["extra_pip_specs_calls"] == [["dep-a"]]


def test_install_exception_cleans_partial_slot(tmp_path: Path, monkeypatch):
    """An ordinary exception during env setup also leaves no slot behind."""
    from toolbase import config as cfg_mod
    from toolbase import skills as skills_mod
    fake_home = tmp_path / "_home" / ".toolbase"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_home)
    monkeypatch.setattr(skills_mod, "CLAUDE_SKILLS_DIR", tmp_path / "claude-skills")

    def fake_setup_venv_failing(*args, **kwargs):
        raise RuntimeError("simulated pip failure")
    monkeypatch.setattr(cli, "setup_venv_environment", fake_setup_venv_failing)

    src = _make_source_toolkit(
        tmp_path / "src",
        bundles_block={"alpha": {"deps": ["dep-a"]}},
        tool_bundles={"ta": "alpha"},
    )
    slot = fake_home / "cache" / "demo" / "0.1.0"
    r = CliRunner().invoke(
        cli.main, ["install", f"{src}[alpha]", "--no-input"],
        catch_exceptions=False,
    )
    assert r.exit_code != 0
    assert not slot.exists()
