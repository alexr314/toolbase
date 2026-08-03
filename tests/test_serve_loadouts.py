"""Tests for ``toolbase/serve/loadouts.py`` — per-file loadouts, discovery,
and the active-loadout resolution chain.

Loadouts are one file per curation under ``<scope>/.toolbase/loadouts/``.
The resolution chain (``resolve_active_loadout_name``) picks the active
loadout: --loadout flag > serve.yaml default.loadout > implicit "default"
loadout > error (no "serve everything" fallback).

The per-toolkit ``ToolkitSelection`` (bundles / enabled / disabled) is
parsed here; the bundle->tool expansion and union/blocklist application
happen in the orchestrator (covered in test_orchestrator_*).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from toolbase.serve.config import ServeConfig, DefaultBlock
from toolbase.serve.loadouts import (
    NoActiveLoadoutError,
    ToolkitSelection,
    discover_loadouts,
    parse_loadout,
    resolve_active_loadout_name,
    resolve_loadout,
)
from toolbase.serve.config import ServeConfigError


# ── parsing ──────────────────────────────────────────────────────────


def test_parse_empty_toolkit_is_whole_toolkit(tmp_path: Path):
    prof = parse_loadout({"toolkits": {"heptapod": {}}}, "p", tmp_path / "p.yaml", "user")
    sel = prof.toolkits["heptapod"]
    assert sel.bundles is None and sel.enabled_tools is None
    assert sel.disabled_tools == []
    assert not sel.is_allowlist


def test_parse_null_toolkit_is_whole_toolkit(tmp_path: Path):
    prof = parse_loadout({"toolkits": {"heptapod": None}}, "p", tmp_path / "p.yaml", "user")
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
    prof = parse_loadout(body, "p", tmp_path / "p.yaml", "user")
    sel = prof.toolkits["heptapod"]
    assert sel.bundles == ["inspire", "pythia"]
    assert sel.enabled_tools == ["extra"]
    assert sel.disabled_tools == ["pythia_debug"]
    assert sel.is_allowlist


def test_parse_disabled_skills(tmp_path: Path):
    body = {
        "toolkits": {
            "heptapod": {
                "bundles": ["pythia"],
                "skills": {"disabled": ["debug_guide", "old_guide"]},
            }
        }
    }
    prof = parse_loadout(body, "p", tmp_path / "p.yaml", "user")
    sel = prof.toolkits["heptapod"]
    assert sel.disabled_skills == ["debug_guide", "old_guide"]


def test_parse_disabled_skills_defaults_empty(tmp_path: Path):
    prof = parse_loadout(
        {"toolkits": {"heptapod": {}}}, "p", tmp_path / "p.yaml", "user",
    )
    assert prof.toolkits["heptapod"].disabled_skills == []


def test_parse_skills_must_be_string_list(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_loadout(
            {"toolkits": {"heptapod": {"skills": {"disabled": [1]}}}},
            "p", tmp_path / "p.yaml", "user",
        )


def test_parse_unknown_skills_key_rejected(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_loadout(
            {"toolkits": {"heptapod": {"skills": {"enabled": ["x"]}}}},
            "p", tmp_path / "p.yaml", "user",
        )


def test_parse_unknown_toolkit_key_rejected(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_loadout(
            {"toolkits": {"heptapod": {"bundlez": ["x"]}}},
            "p", tmp_path / "p.yaml", "user",
        )


def test_parse_unknown_top_level_key_rejected(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_loadout(
            {"toolkits": {}, "extra": 1}, "p", tmp_path / "p.yaml", "user",
        )


def test_parse_bundles_must_be_string_list(tmp_path: Path):
    with pytest.raises(ServeConfigError):
        parse_loadout(
            {"toolkits": {"heptapod": {"bundles": [1, 2]}}},
            "p", tmp_path / "p.yaml", "user",
        )


# ── discovery + shadowing ─────────────────────────────────────────────


def _write_loadout(base: Path, scope_dir: str, name: str, body: dict) -> Path:
    d = base / scope_dir / "loadouts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


def test_discover_user_only(tmp_path: Path):
    user_base = tmp_path / "user"
    _write_loadout(user_base, ".", "alpha", {"toolkits": {"calculator": {}}})
    found = discover_loadouts(None, user_base=user_base)
    assert set(found) == {"alpha"}
    assert found["alpha"].scope == "user"


def test_discover_project_shadows_user(tmp_path: Path):
    user_base = tmp_path / "user"
    proj = tmp_path / "proj"
    _write_loadout(user_base, ".", "shared", {"toolkits": {"calculator": {}}})
    # project loadout of the same basename, different content
    pdir = proj / ".toolbase" / "loadouts"
    pdir.mkdir(parents=True)
    (pdir / "shared.yaml").write_text(
        yaml.safe_dump({"toolkits": {"heptapod": {"bundles": ["pythia"]}}})
    )
    found = discover_loadouts(proj, user_base=user_base)
    # project wins whole — heptapod, not calculator
    assert found["shared"].scope == "project"
    assert "heptapod" in found["shared"].toolkits
    assert "calculator" not in found["shared"].toolkits


# ── active-loadout resolution chain ───────────────────────────────────


def _loadouts(names):
    from toolbase.serve.loadouts import Loadout
    return {n: Loadout(name=n, path=Path(f"{n}.yaml"), scope="user") for n in names}


def test_resolve_cli_flag_wins():
    cfg = ServeConfig(default=DefaultBlock(loadout="from-yaml"))
    name, source = resolve_active_loadout_name(
        cfg, _loadouts(["from-yaml", "from-flag"]), cli_loadout="from-flag",
    )
    assert name == "from-flag"
    assert "flag" in source


def test_resolve_cli_flag_missing_errors():
    cfg = ServeConfig()
    with pytest.raises(ServeConfigError):
        resolve_active_loadout_name(cfg, _loadouts(["other"]), cli_loadout="nope")


def test_resolve_serve_yaml_default():
    cfg = ServeConfig(default=DefaultBlock(loadout="paper"))
    name, source = resolve_active_loadout_name(cfg, _loadouts(["paper"]))
    assert name == "paper"
    assert "serve.yaml" in source


def test_resolve_serve_yaml_default_missing_errors():
    cfg = ServeConfig(default=DefaultBlock(loadout="ghost"))
    with pytest.raises(ServeConfigError):
        resolve_active_loadout_name(cfg, _loadouts(["other"]))


def test_resolve_implicit_default():
    cfg = ServeConfig()
    name, source = resolve_active_loadout_name(cfg, _loadouts(["default", "x"]))
    assert name == "default"
    assert "implicit" in source


def test_resolve_no_active_loadout_errors():
    cfg = ServeConfig()
    with pytest.raises(NoActiveLoadoutError):
        resolve_active_loadout_name(cfg, _loadouts(["paper", "x"]))


# ── full resolve_loadout (folds in serve.yaml disabled) ───────────────


def test_resolve_loadout_folds_disabled(tmp_path: Path):
    user_base = tmp_path / "user"
    # user serve.yaml: default.loadout + absolute blocklist
    (user_base).mkdir(parents=True, exist_ok=True)
    (user_base / "serve.yaml").write_text(yaml.safe_dump({
        "default": {
            "loadout": "work",
            "disabled": {"toolkits": ["legacy"], "tools": ["calculator__noisy"]},
        }
    }))
    _write_loadout(user_base, ".", "work", {
        "toolkits": {"heptapod": {"bundles": ["pythia"]}}
    })
    resolved = resolve_loadout(None, user_base=user_base)
    assert resolved.name == "work"
    assert resolved.toolkits["heptapod"].bundles == ["pythia"]
    assert resolved.disabled_toolkits == ["legacy"]
    assert resolved.disabled_tools == ["calculator__noisy"]


def test_resolve_loadout_no_active_raises(tmp_path: Path):
    user_base = tmp_path / "user"
    user_base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(NoActiveLoadoutError):
        resolve_loadout(None, user_base=user_base)


# ── tool_is_served (shared orchestrator/list decision) ────────────────


from toolbase.serve.bundles import BundleAvailability
from toolbase.serve.loadouts import tool_is_served


def _avail(available=(), dropped=None, has_block=True):
    return BundleAvailability(
        available_bundles=list(available),
        dropped_bundles=dict(dropped or {}),
        has_bundles_block=has_block,
    )


def test_served_whole_toolkit_no_selection():
    # No loadout selection -> serve-all; tool with no bundle is served.
    assert tool_is_served("t", [], None, _avail(has_block=False), set())


def test_served_dropped_bundle_gated_off():
    av = _avail(dropped={"mg5": ["mg5_path"]})
    assert not tool_is_served("gen", ["mg5"], None, av, set())


def test_served_allowlist_by_bundle():
    sel = ToolkitSelection(bundles=["pythia"])
    av = _avail(available=["pythia", "inspire"])
    assert tool_is_served("run", ["pythia"], sel, av, set())
    assert not tool_is_served("search", ["inspire"], sel, av, set())


def test_served_allowlist_union_enabled():
    sel = ToolkitSelection(bundles=["pythia"], enabled_tools=["extra"])
    av = _avail(available=["pythia", "inspire"])
    assert tool_is_served("extra", ["inspire"], sel, av, set())  # via enabled
    assert tool_is_served("run", ["pythia"], sel, av, set())     # via bundle


def test_served_per_toolkit_disabled_wins():
    sel = ToolkitSelection(bundles=["pythia"], disabled_tools=["debug"])
    av = _avail(available=["pythia"])
    assert not tool_is_served("debug", ["pythia"], sel, av, set())


def test_served_global_blocklist():
    sel = ToolkitSelection()  # whole toolkit
    av = _avail(has_block=False)
    assert not tool_is_served("noisy", [], sel, av, {"noisy"})


# ── multi-bundle membership (post-0.5.x) ──────────────────────────────


def test_served_multi_bundle_any_available():
    """A tool in multiple bundles is served if ANY of them is available."""
    av = _avail(available=["alpha"], dropped={"beta": ["beta_key"]})
    # Tool is in both alpha (available) and beta (dropped) → served.
    assert tool_is_served("hybrid", ["alpha", "beta"], None, av, set())


def test_served_multi_bundle_all_dropped_excluded():
    """A tool whose every bundle is dropped is excluded."""
    av = _avail(dropped={"beta": ["beta_key"], "gamma": ["gamma_key"]})
    assert not tool_is_served(
        "hybrid", ["beta", "gamma"], None, av, set()
    )


def test_served_multi_bundle_intersects_loadout_allowlist():
    """Loadout allowlist matches if ANY of the tool's bundles is in it."""
    sel = ToolkitSelection(bundles=["alpha"])
    av = _avail(available=["alpha", "beta", "gamma"])
    # Tool in [gamma, alpha]: alpha is in allowlist → served.
    assert tool_is_served("hybrid", ["gamma", "alpha"], sel, av, set())
    # Tool in [gamma, beta]: neither in allowlist → excluded.
    assert not tool_is_served("other", ["gamma", "beta"], sel, av, set())
