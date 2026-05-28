"""Tests for ``toolbase/serve/profiles.py`` — per-file profiles, discovery,
and the active-profile resolution chain.

Profiles are one file per curation under ``<scope>/.toolbase/profiles/``.
The resolution chain (``resolve_active_profile_name``) picks the active
profile: --profile flag > serve.yaml default.profile > implicit "default"
profile > error (no "serve everything" fallback).

The per-toolkit ``ToolkitSelection`` (bundles / enabled / disabled) is
parsed here; the bundle->tool expansion and union/blocklist application
happen in the orchestrator (covered in test_orchestrator_*).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from toolbase.serve.config import ServeConfig, DefaultBlock
from toolbase.serve.profiles import (
    NoActiveProfileError,
    ToolkitSelection,
    discover_profiles,
    parse_profile,
    resolve_active_profile_name,
    resolve_profile,
)
from toolbase.serve.config import ServeConfigError


# ── parsing ──────────────────────────────────────────────────────────


def test_parse_empty_toolkit_is_whole_toolkit(tmp_path: Path):
    prof = parse_profile({"toolkits": {"heptapod": {}}}, "p", tmp_path / "p.yaml", "user")
    sel = prof.toolkits["heptapod"]
    assert sel.bundles is None and sel.enabled_tools is None
    assert sel.disabled_tools == []
    assert not sel.is_allowlist


def test_parse_null_toolkit_is_whole_toolkit(tmp_path: Path):
    prof = parse_profile({"toolkits": {"heptapod": None}}, "p", tmp_path / "p.yaml", "user")
    assert not prof.toolkits["heptapod"].is_allowlist


def test_parse_bundles_and_tools(tmp_path: Path):
    body = {
        "toolkits": {
            "heptapod": {
                "bundles": ["inspire", "pythia"],
                "tools": {"enabled": ["extra"], "disabled": ["pythia_debug"]},
            }
        }
    }
    prof = parse_profile(body, "p", tmp_path / "p.yaml", "user")
    sel = prof.toolkits["heptapod"]
    assert sel.bundles == ["inspire", "pythia"]
    assert sel.enabled_tools == ["extra"]
    assert sel.disabled_tools == ["pythia_debug"]
    assert sel.is_allowlist


def test_parse_unknown_toolkit_key_rejected(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_profile(
            {"toolkits": {"heptapod": {"bundlez": ["x"]}}},
            "p", tmp_path / "p.yaml", "user",
        )


def test_parse_unknown_top_level_key_rejected(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_profile(
            {"toolkits": {}, "extra": 1}, "p", tmp_path / "p.yaml", "user",
        )


def test_parse_bundles_must_be_string_list(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_profile(
            {"toolkits": {"heptapod": {"bundles": [1, 2]}}},
            "p", tmp_path / "p.yaml", "user",
        )


# ── discovery + shadowing ─────────────────────────────────────────────


def _write_profile(base: Path, scope_dir: str, name: str, body: dict) -> Path:
    d = base / scope_dir / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


def test_discover_user_only(tmp_path: Path):
    user_base = tmp_path / "user"
    _write_profile(user_base, ".", "alpha", {"toolkits": {"aster": {}}})
    found = discover_profiles(None, user_base=user_base)
    assert set(found) == {"alpha"}
    assert found["alpha"].scope == "user"


def test_discover_project_shadows_user(tmp_path: Path):
    user_base = tmp_path / "user"
    proj = tmp_path / "proj"
    _write_profile(user_base, ".", "shared", {"toolkits": {"aster": {}}})
    # project profile of the same basename, different content
    pdir = proj / ".toolbase" / "profiles"
    pdir.mkdir(parents=True)
    (pdir / "shared.yaml").write_text(
        yaml.safe_dump({"toolkits": {"heptapod": {"bundles": ["pythia"]}}})
    )
    found = discover_profiles(proj, user_base=user_base)
    # project wins whole — heptapod, not aster
    assert found["shared"].scope == "project"
    assert "heptapod" in found["shared"].toolkits
    assert "aster" not in found["shared"].toolkits


# ── active-profile resolution chain ───────────────────────────────────


def _profiles(names):
    from toolbase.serve.profiles import Profile
    return {n: Profile(name=n, path=Path(f"{n}.yaml"), scope="user") for n in names}


def test_resolve_cli_flag_wins():
    cfg = ServeConfig(default=DefaultBlock(profile="from-yaml"))
    name, source = resolve_active_profile_name(
        cfg, _profiles(["from-yaml", "from-flag"]), cli_profile="from-flag",
    )
    assert name == "from-flag"
    assert "flag" in source


def test_resolve_cli_flag_missing_errors():
    cfg = ServeConfig()
    with pytest.raises(ServeConfigError):
        resolve_active_profile_name(cfg, _profiles(["other"]), cli_profile="nope")


def test_resolve_serve_yaml_default():
    cfg = ServeConfig(default=DefaultBlock(profile="paper"))
    name, source = resolve_active_profile_name(cfg, _profiles(["paper"]))
    assert name == "paper"
    assert "serve.yaml" in source


def test_resolve_serve_yaml_default_missing_errors():
    cfg = ServeConfig(default=DefaultBlock(profile="ghost"))
    with pytest.raises(ServeConfigError):
        resolve_active_profile_name(cfg, _profiles(["other"]))


def test_resolve_implicit_default():
    cfg = ServeConfig()
    name, source = resolve_active_profile_name(cfg, _profiles(["default", "x"]))
    assert name == "default"
    assert "implicit" in source


def test_resolve_no_active_profile_errors():
    cfg = ServeConfig()
    with pytest.raises(NoActiveProfileError):
        resolve_active_profile_name(cfg, _profiles(["paper", "x"]))


# ── full resolve_profile (folds in serve.yaml disabled) ───────────────


def test_resolve_profile_folds_disabled(tmp_path: Path):
    user_base = tmp_path / "user"
    # user serve.yaml: default.profile + absolute blocklist
    (user_base).mkdir(parents=True, exist_ok=True)
    (user_base / "serve.yaml").write_text(yaml.safe_dump({
        "default": {
            "profile": "work",
            "disabled": {"toolkits": ["legacy"], "tools": ["aster__noisy"]},
        }
    }))
    _write_profile(user_base, ".", "work", {
        "toolkits": {"heptapod": {"bundles": ["pythia"]}}
    })
    resolved = resolve_profile(None, user_base=user_base)
    assert resolved.name == "work"
    assert resolved.toolkits["heptapod"].bundles == ["pythia"]
    assert resolved.disabled_toolkits == ["legacy"]
    assert resolved.disabled_tools == ["aster__noisy"]


def test_resolve_profile_no_active_raises(tmp_path: Path):
    user_base = tmp_path / "user"
    user_base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(NoActiveProfileError):
        resolve_profile(None, user_base=user_base)


# ── tool_is_served (shared orchestrator/list decision) ────────────────


from toolbase.serve.bundles import BundleAvailability
from toolbase.serve.profiles import tool_is_served


def _avail(available=(), dropped=None, has_block=True):
    return BundleAvailability(
        available_bundles=list(available),
        dropped_bundles=dict(dropped or {}),
        has_bundles_block=has_block,
    )


def test_served_whole_toolkit_no_selection():
    # No profile selection -> serve-all; tool with no bundle is served.
    assert tool_is_served("t", None, None, _avail(has_block=False), set())


def test_served_dropped_bundle_gated_off():
    av = _avail(dropped={"mg5": ["mg5_path"]})
    assert not tool_is_served("gen", "mg5", None, av, set())


def test_served_allowlist_by_bundle():
    sel = ToolkitSelection(bundles=["pythia"])
    av = _avail(available=["pythia", "inspire"])
    assert tool_is_served("run", "pythia", sel, av, set())
    assert not tool_is_served("search", "inspire", sel, av, set())


def test_served_allowlist_union_enabled():
    sel = ToolkitSelection(bundles=["pythia"], enabled_tools=["extra"])
    av = _avail(available=["pythia", "inspire"])
    assert tool_is_served("extra", "inspire", sel, av, set())  # via enabled
    assert tool_is_served("run", "pythia", sel, av, set())     # via bundle


def test_served_per_toolkit_disabled_wins():
    sel = ToolkitSelection(bundles=["pythia"], disabled_tools=["debug"])
    av = _avail(available=["pythia"])
    assert not tool_is_served("debug", "pythia", sel, av, set())


def test_served_global_blocklist():
    sel = ToolkitSelection()  # whole toolkit
    av = _avail(has_block=False)
    assert not tool_is_served("noisy", None, sel, av, {"noisy"})
