"""Tests for ``toolbase/serve/profile_scaffold.py`` — the activate /
deactivate mutation engine on the default profile file.

These call ``activate`` / ``deactivate`` directly with ``scope="user"``
and a ``user_base`` pointing at a tmp dir, so no monkeypatching of the
global config dir is needed. Each asserts on the round-tripped yaml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from toolbase.serve.profile_scaffold import (
    ProfileItemError,
    activate,
    deactivate,
    default_profile_path,
    parse_item,
)


def _read(base: Path) -> dict:
    p = default_profile_path("user", user_base=base)
    return yaml.safe_load(p.read_text())


def _act(base: Path, item: str):
    return activate(item, scope="user", user_base=base)


def _deact(base: Path, item: str):
    return deactivate(item, scope="user", user_base=base)


# ── item parsing ─────────────────────────────────────────────────────


def test_parse_item_toolkit():
    assert parse_item("heptapod") == ("toolkit", "heptapod", None)


def test_parse_item_bundle():
    assert parse_item("heptapod/pythia") == ("bundle", "heptapod", "pythia")


def test_parse_item_tool():
    assert parse_item("heptapod__run") == ("tool", "heptapod", "run")


@pytest.mark.parametrize("bad", ["", "heptapod/", "/x", "heptapod__", "__t", "a/b__c"])
def test_parse_item_malformed(bad):
    with pytest.raises(ProfileItemError):
        parse_item(bad)


# ── activate: toolkit granularity ────────────────────────────────────


def test_activate_toolkit_creates_default(tmp_path: Path):
    res = _act(tmp_path, "heptapod")
    assert res.changed
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"] == {} or data["toolkits"]["heptapod"] is None


def test_activate_toolkit_idempotent(tmp_path: Path):
    _act(tmp_path, "heptapod")
    res = _act(tmp_path, "heptapod")
    assert not res.changed
    assert "already active" in res.message


# ── activate: bundle granularity ─────────────────────────────────────


def test_activate_bundle_on_absent_toolkit(tmp_path: Path):
    res = _act(tmp_path, "heptapod/pythia")
    assert res.changed
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"]["bundles"] == ["pythia"]


def test_activate_bundle_narrows_whole_toolkit(tmp_path: Path):
    _act(tmp_path, "heptapod")              # whole toolkit
    res = _act(tmp_path, "heptapod/pythia")  # narrows
    assert res.changed
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"]["bundles"] == ["pythia"]


def test_activate_bundle_appends(tmp_path: Path):
    _act(tmp_path, "heptapod/pythia")
    _act(tmp_path, "heptapod/inspire")
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"]["bundles"] == ["pythia", "inspire"]


def test_activate_bundle_idempotent(tmp_path: Path):
    _act(tmp_path, "heptapod/pythia")
    res = _act(tmp_path, "heptapod/pythia")
    assert not res.changed


# ── activate: tool granularity ───────────────────────────────────────


def test_activate_tool_adds_enabled(tmp_path: Path):
    res = _act(tmp_path, "heptapod__run_pythia")
    assert res.changed
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"]["tools"]["enabled"] == ["run_pythia"]


def test_activate_tool_clears_prior_disable(tmp_path: Path):
    _deact(tmp_path, "heptapod__foo")   # disables foo (toolkit auto-created)
    res = _act(tmp_path, "heptapod__foo")  # re-enable
    assert res.changed
    data = _read(tmp_path)
    tools = data["toolkits"]["heptapod"]["tools"]
    assert "disabled" not in tools or "foo" not in (tools.get("disabled") or [])
    assert "foo" in tools["enabled"]


# ── deactivate ───────────────────────────────────────────────────────


def test_deactivate_toolkit_removes_entry(tmp_path: Path):
    _act(tmp_path, "heptapod")
    res = _deact(tmp_path, "heptapod")
    assert res.changed
    data = _read(tmp_path)
    assert "heptapod" not in data["toolkits"]


def test_deactivate_toolkit_not_present_noop(tmp_path: Path):
    res = _deact(tmp_path, "heptapod")
    assert not res.changed


def test_deactivate_bundle_removes_from_list(tmp_path: Path):
    _act(tmp_path, "heptapod/pythia")
    _act(tmp_path, "heptapod/inspire")
    res = _deact(tmp_path, "heptapod/inspire")
    assert res.changed
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"]["bundles"] == ["pythia"]


def test_deactivate_tool_on_whole_toolkit_adds_disabled(tmp_path: Path):
    _act(tmp_path, "heptapod")               # whole toolkit
    res = _deact(tmp_path, "heptapod__noisy")  # exclude one tool
    assert res.changed
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"]["tools"]["disabled"] == ["noisy"]


def test_deactivate_tool_removes_from_enabled(tmp_path: Path):
    _act(tmp_path, "heptapod__a")
    _act(tmp_path, "heptapod__b")
    res = _deact(tmp_path, "heptapod__a")
    assert res.changed
    data = _read(tmp_path)
    assert data["toolkits"]["heptapod"]["tools"]["enabled"] == ["b"]


def test_header_comment_survives_round_trip(tmp_path: Path):
    _act(tmp_path, "heptapod")
    _act(tmp_path, "aster")
    raw = default_profile_path("user", user_base=tmp_path).read_text()
    assert "Profile: default" in raw  # the scaffold header comment persists
