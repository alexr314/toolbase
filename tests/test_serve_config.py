"""Tests for ``toolbase/serve/config.py`` — load/save and the resolver.

The resolver is the unit worth heavy coverage. The orchestrator is downstream
of a correct resolver, so we pin behavior here in isolation.

Test matrix targets:

- with/without serve.yaml present
- with/without positional toolkits
- with/without --group
- with/without --enable-tool / --disable-tool
- the contradictory case (`--enable-tool X` and `--disable-tool X`)
- `--enable-tool` referencing a toolkit not in the session
- positional toolkit not installed
- group references missing toolkit (warn-and-skip)
- group references missing group (error)
- empty disabled lists (no exclusions, not exclude-everything)
- malformed serve.yaml (clear error with path)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from toolbase.serve.config import (
    DefaultBlock,
    Group,
    ServeConfig,
    ServeConfigError,
    load_serve_config,
    resolve_serve_set,
    save_serve_config,
)


# ── load / save round trip ──────────────────────────────────────────────────


def test_load_missing_returns_empty(tmp_path: Path):
    cfg = load_serve_config(tmp_path / "serve.yaml")
    assert cfg.default.disabled_toolkits == []
    assert cfg.default.disabled_tools == []
    assert cfg.groups == {}


def test_load_blocklist_only_default(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text(yaml.safe_dump({
        "default": {
            "toolkits": {"disabled": ["heptapod"]},
            "tools": {"disabled": ["aster__heavy"]},
        }
    }))
    cfg = load_serve_config(p)
    assert cfg.default.disabled_toolkits == ["heptapod"]
    assert cfg.default.disabled_tools == ["aster__heavy"]


def test_load_groups(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text(yaml.safe_dump({
        "groups": {
            "exo": {
                "toolkits": ["aster", "arxiv-search"],
                "tools": {"disabled": ["aster__heavy"]},
            }
        }
    }))
    cfg = load_serve_config(p)
    assert "exo" in cfg.groups
    assert cfg.groups["exo"].toolkits == ["aster", "arxiv-search"]
    assert cfg.groups["exo"].disabled_tools == ["aster__heavy"]


def test_load_malformed_yaml_clear_error(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text(":::not valid yaml:::\n  - [")
    with pytest.raises(ServeConfigError) as ei:
        load_serve_config(p)
    assert str(p) in str(ei.value) or "could not parse" in str(ei.value)


def test_load_top_level_must_be_mapping(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    p.write_text("- item1\n- item2\n")
    with pytest.raises(ServeConfigError):
        load_serve_config(p)


def test_save_and_reload_roundtrip(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    cfg = ServeConfig(
        default=DefaultBlock(
            disabled_toolkits=["heptapod"],
            disabled_tools=["aster__heavy"],
        ),
        groups={
            "exo": Group(
                name="exo",
                toolkits=["aster", "arxiv-search"],
                disabled_tools=["aster__slow"],
            )
        },
    )
    save_serve_config(cfg, p)
    reloaded = load_serve_config(p)
    assert reloaded.default.disabled_toolkits == ["heptapod"]
    assert reloaded.default.disabled_tools == ["aster__heavy"]
    assert "exo" in reloaded.groups
    assert reloaded.groups["exo"].toolkits == ["aster", "arxiv-search"]


def test_save_empty_config_drops_empty_keys(tmp_path: Path):
    p = tmp_path / "serve.yaml"
    save_serve_config(ServeConfig(), p)
    # Should produce something parseable and not contain stray keys.
    reloaded = load_serve_config(p)
    assert reloaded.default.disabled_toolkits == []
    assert reloaded.groups == {}


# ── resolver: default path ──────────────────────────────────────────────────


def test_resolve_no_config_serves_all_installed():
    out = resolve_serve_set(
        installed_toolkits=["aster", "heptapod", "arxiv-search"],
        config=ServeConfig(),
    )
    assert out.toolkits == ["aster", "heptapod", "arxiv-search"]
    assert all(out.tools[t] is None for t in out.toolkits)
    assert any("default" in p for p in out.resolution_path)


def test_resolve_default_blocklist_excludes_listed_toolkit():
    cfg = ServeConfig(default=DefaultBlock(disabled_toolkits=["heptapod"]))
    out = resolve_serve_set(
        installed_toolkits=["aster", "heptapod", "arxiv-search"],
        config=cfg,
    )
    assert "heptapod" not in out.toolkits
    assert out.toolkits == ["aster", "arxiv-search"]


def test_resolve_default_empty_disabled_no_exclusions():
    """Empty disabled list must mean 'no exclusions', not 'exclude all'."""
    cfg = ServeConfig(default=DefaultBlock(disabled_toolkits=[]))
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=cfg,
    )
    assert out.toolkits == ["aster"]


def test_resolve_default_stale_entry_warns():
    cfg = ServeConfig(default=DefaultBlock(disabled_toolkits=["ghost"]))
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=cfg,
    )
    assert any("ghost" in w for w in out.warnings)


# ── resolver: positional ────────────────────────────────────────────────────


def test_resolve_positional_overrides_default():
    cfg = ServeConfig(default=DefaultBlock(disabled_toolkits=["aster"]))
    out = resolve_serve_set(
        installed_toolkits=["aster", "heptapod"],
        config=cfg,
        positional_toolkits=["aster"],  # explicitly requested despite default-disable
    )
    assert out.toolkits == ["aster"]


def test_resolve_positional_unknown_errors_clearly():
    with pytest.raises(ServeConfigError) as ei:
        resolve_serve_set(
            installed_toolkits=["aster"],
            config=ServeConfig(),
            positional_toolkits=["nope"],
        )
    assert "nope" in str(ei.value)
    assert "toolbase install" in str(ei.value)


# ── resolver: groups ────────────────────────────────────────────────────────


def test_resolve_group_uses_allowlist():
    cfg = ServeConfig(groups={
        "exo": Group(name="exo", toolkits=["aster", "arxiv-search"])
    })
    out = resolve_serve_set(
        installed_toolkits=["aster", "heptapod", "arxiv-search"],
        config=cfg,
        group_name="exo",
    )
    assert out.toolkits == ["aster", "arxiv-search"]


def test_resolve_group_warns_on_uninstalled_toolkit():
    cfg = ServeConfig(groups={
        "exo": Group(name="exo", toolkits=["aster", "ghost"])
    })
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=cfg,
        group_name="exo",
    )
    assert out.toolkits == ["aster"]
    assert any("ghost" in w for w in out.warnings)


def test_resolve_group_all_uninstalled_errors():
    cfg = ServeConfig(groups={
        "exo": Group(name="exo", toolkits=["ghost"])
    })
    with pytest.raises(ServeConfigError):
        resolve_serve_set(
            installed_toolkits=["aster"],
            config=cfg,
            group_name="exo",
        )


def test_resolve_unknown_group_errors():
    with pytest.raises(ServeConfigError) as ei:
        resolve_serve_set(
            installed_toolkits=["aster"],
            config=ServeConfig(),
            group_name="exo",
        )
    assert "exo" in str(ei.value)


# ── resolver: tool flags ────────────────────────────────────────────────────


def test_resolve_enable_tool_switches_to_allowlist():
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=ServeConfig(),
        positional_toolkits=["aster"],
        enable_tools=["aster__transit"],
    )
    assert out.toolkits == ["aster"]
    assert out.tools["aster"] == ["transit"]


def test_resolve_enable_tool_for_uninstalled_toolkit_errors():
    with pytest.raises(ServeConfigError) as ei:
        resolve_serve_set(
            installed_toolkits=["aster"],
            config=ServeConfig(),
            positional_toolkits=["aster"],
            enable_tools=["heptapod__foo"],
        )
    assert "heptapod" in str(ei.value)


def test_resolve_disable_tool_subtracts_without_allowlist():
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=ServeConfig(),
        positional_toolkits=["aster"],
        disable_tools=["aster__heavy"],
    )
    # No allowlist active → tools[tk] is None; orchestrator handles the
    # subtraction at spawn time. The resolution_path should mention it.
    assert out.tools["aster"] is None
    assert any("aster__heavy" in p for p in out.resolution_path)


def test_resolve_disable_wins_over_enable_for_same_tool():
    """Contradictory: --enable-tool X + --disable-tool X. Disable wins."""
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=ServeConfig(),
        positional_toolkits=["aster"],
        enable_tools=["aster__transit"],
        disable_tools=["aster__transit"],
    )
    # aster's allowlist would be {transit}, then disabled → empty → drop toolkit
    assert "aster" not in out.toolkits
    assert any("no tools enabled" in w for w in out.warnings)


def test_resolve_default_disabled_tools_seed_into_subtraction():
    cfg = ServeConfig(default=DefaultBlock(disabled_tools=["aster__heavy"]))
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=cfg,
    )
    assert out.tools["aster"] is None  # orchestrator handles it
    assert any("aster__heavy" in p for p in out.resolution_path)


def test_resolve_group_disabled_tools_seed_into_subtraction():
    cfg = ServeConfig(groups={
        "exo": Group(name="exo", toolkits=["aster"], disabled_tools=["aster__heavy"])
    })
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=cfg,
        group_name="exo",
    )
    assert out.tools["aster"] is None
    assert any("aster__heavy" in p for p in out.resolution_path)


# ── resolver: malformed tool refs ──────────────────────────────────────────


def test_resolve_bad_tool_ref_errors():
    for bad in ["asterheavy", "aster__", "__heavy", ""]:
        with pytest.raises(ServeConfigError):
            resolve_serve_set(
                installed_toolkits=["aster"],
                config=ServeConfig(),
                positional_toolkits=["aster"],
                disable_tools=[bad],
            )


# ── resolver: combined cases ────────────────────────────────────────────────


def test_resolve_positional_plus_enable_tool_scopes_within():
    """`toolbase serve aster --enable-tool aster__transit` → just transit."""
    out = resolve_serve_set(
        installed_toolkits=["aster", "arxiv-search"],
        config=ServeConfig(),
        positional_toolkits=["aster"],
        enable_tools=["aster__transit"],
    )
    assert out.toolkits == ["aster"]
    assert out.tools["aster"] == ["transit"]


def test_resolve_group_plus_disable_tool_filters_further():
    cfg = ServeConfig(groups={
        "exo": Group(name="exo", toolkits=["aster"], disabled_tools=["aster__heavy"])
    })
    out = resolve_serve_set(
        installed_toolkits=["aster"],
        config=cfg,
        group_name="exo",
        disable_tools=["aster__slow"],
    )
    assert out.toolkits == ["aster"]
    # Both disables should appear in the resolution path narrative.
    path_str = " ".join(out.resolution_path)
    assert "aster__heavy" in path_str
    assert "aster__slow" in path_str


# ── 0.5.1: --enable-bundle (per-toolkit bundle selection) ────────────────


def test_resolve_enable_bundle_captures_request():
    """``--enable-bundle heptapod__mg5`` lands in ``ResolvedSet.enable_bundles``
    keyed by toolkit name, with the group name in the list."""
    out = resolve_serve_set(
        installed_toolkits=["heptapod"],
        config=ServeConfig(),
        enable_bundles=["heptapod__mg5"],
    )
    assert out.enable_bundles == {"heptapod": ["mg5"]}
    assert "heptapod" in out.toolkits
    # The orchestrator handles the available/unavailable decision —
    # the resolver just routes the request.


def test_resolve_enable_bundle_multiple_per_toolkit():
    out = resolve_serve_set(
        installed_toolkits=["heptapod"],
        config=ServeConfig(),
        enable_bundles=["heptapod__pdg", "heptapod__nda"],
    )
    assert out.enable_bundles["heptapod"] == ["pdg", "nda"]


def test_resolve_enable_bundle_across_toolkits():
    out = resolve_serve_set(
        installed_toolkits=["heptapod", "aster"],
        config=ServeConfig(),
        enable_bundles=["heptapod__pdg", "aster__transit"],
    )
    assert out.enable_bundles == {
        "heptapod": ["pdg"],
        "aster": ["transit"],
    }


def test_resolve_enable_bundle_malformed_rejected():
    """``--enable-bundle <bad-shape>`` errors with the toolkit__bundle hint."""
    with pytest.raises(ServeConfigError) as exc:
        resolve_serve_set(
            installed_toolkits=["heptapod"],
            config=ServeConfig(),
            enable_bundles=["just-a-name"],
        )
    assert "toolkit__bundle" in str(exc.value)


def test_resolve_enable_bundle_for_toolkit_not_in_session_errors():
    """Can't request a group for a toolkit that isn't installed /
    isn't in the resolved session."""
    with pytest.raises(ServeConfigError) as exc:
        resolve_serve_set(
            installed_toolkits=["aster"],
            config=ServeConfig(),
            enable_bundles=["heptapod__mg5"],
        )
    assert "heptapod" in str(exc.value)


def test_resolve_enable_bundle_resolution_path_narrative():
    """``--enable-bundle`` appears in the dry-run resolution path."""
    out = resolve_serve_set(
        installed_toolkits=["heptapod"],
        config=ServeConfig(),
        enable_bundles=["heptapod__mg5"],
    )
    assert any("--enable-bundle" in step for step in out.resolution_path)


def test_resolve_enable_bundle_empty_omitted():
    """No ``--enable-bundle`` flags → ``enable_bundles`` is empty."""
    out = resolve_serve_set(
        installed_toolkits=["heptapod"],
        config=ServeConfig(),
    )
    assert out.enable_bundles == {}
