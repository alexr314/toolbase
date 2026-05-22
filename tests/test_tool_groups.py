"""Unit tests for the ``tool_groups.requires:`` evaluation (0.5.1).

These tests cover the pure resolver in
``toolbase.serve.tool_groups`` and the validation rules in
``toolbase.validation`` that enforce the schema at publish time.

End-to-end orchestrator integration is exercised by
``tests/e2e/run_tool_groups_e2e.py``.
"""

from __future__ import annotations

import pytest

from toolbase.serve.tool_groups import (
    GroupAvailability,
    NEEDS_VALUE_SENTINEL,
    evaluate_tool_groups,
    format_skip_log_line,
)


# ────────────────────────────────────────────────────────────────────
# Pure resolver: evaluate_tool_groups
# ────────────────────────────────────────────────────────────────────


class TestEvaluateToolGroups:
    """Pure-function tests for ``evaluate_tool_groups``."""

    def test_no_block_returns_empty_availability(self):
        """No tool_groups block → no gating active, no logs."""
        out = evaluate_tool_groups(None, {})
        assert out.has_tool_groups_block is False
        assert out.available_groups == []
        assert out.dropped_groups == {}

    def test_empty_block_returns_empty_availability(self):
        """``tool_groups: {}`` also means no gating active."""
        out = evaluate_tool_groups({}, {})
        assert out.has_tool_groups_block is False

    def test_group_without_requires_always_available(self):
        """``pdg: {}`` has no ``requires:`` → unconditionally available."""
        out = evaluate_tool_groups({"pdg": {}}, {})
        assert out.has_tool_groups_block is True
        assert "pdg" in out.available_groups
        assert "pdg" not in out.dropped_groups

    def test_single_require_satisfied(self):
        """``mg5: {requires: [mg5_path]}`` with mg5_path set → available."""
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": "/opt/mg5"},
        )
        assert "mg5" in out.available_groups
        assert "mg5" not in out.dropped_groups

    def test_single_require_missing(self):
        """``mg5: {requires: [mg5_path]}`` with mg5_path absent → dropped."""
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}},
            {},
        )
        assert "mg5" not in out.available_groups
        assert out.dropped_groups["mg5"] == ["mg5_path"]

    def test_needs_value_sentinel_counts_as_unset(self):
        """The ``<NEEDS VALUE>`` sentinel is treated as missing."""
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": NEEDS_VALUE_SENTINEL},
        )
        assert "mg5" in out.dropped_groups
        assert out.dropped_groups["mg5"] == ["mg5_path"]

    def test_none_value_counts_as_unset(self):
        """Explicit None on a required key → unset."""
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": None},
        )
        assert "mg5" in out.dropped_groups

    def test_empty_string_counts_as_unset(self):
        """Whitespace-only string → unset (typical of user leaving blank)."""
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": "   "},
        )
        assert "mg5" in out.dropped_groups

    def test_falsy_meaningful_values_count_as_set(self):
        """False and 0 are valid values (user set them deliberately)."""
        out = evaluate_tool_groups(
            {
                "g1": {"requires": ["enabled"]},
                "g2": {"requires": ["count"]},
            },
            {"enabled": False, "count": 0},
        )
        assert "g1" in out.available_groups
        assert "g2" in out.available_groups

    def test_multi_require_partial_missing(self):
        """One of two requires missing → dropped, missing list mentions it."""
        out = evaluate_tool_groups(
            {"feynrules": {"requires": ["wolframscript_path", "feynrules_path"]}},
            {"wolframscript_path": "/usr/bin/wolframscript"},
        )
        assert "feynrules" in out.dropped_groups
        assert out.dropped_groups["feynrules"] == ["feynrules_path"]

    def test_multi_require_all_satisfied(self):
        """All required keys set → group available."""
        out = evaluate_tool_groups(
            {"feynrules": {"requires": ["wolframscript_path", "feynrules_path"]}},
            {
                "wolframscript_path": "/usr/bin/wolframscript",
                "feynrules_path": "/opt/feynrules",
            },
        )
        assert "feynrules" in out.available_groups

    def test_multiple_groups_mixed_availability(self):
        """Several groups with mixed available/dropped states."""
        block = {
            "pdg": {},
            "mg5": {"requires": ["mg5_path"]},
            "eda": {"requires": ["wolframscript_path"]},
            "feynrules": {"requires": ["wolframscript_path", "feynrules_path"]},
        }
        out = evaluate_tool_groups(
            block,
            {"wolframscript_path": "/usr/bin/ws"},
        )
        assert set(out.available_groups) == {"pdg", "eda"}
        assert set(out.dropped_groups) == {"mg5", "feynrules"}
        assert out.dropped_groups["feynrules"] == ["feynrules_path"]
        assert out.dropped_groups["mg5"] == ["mg5_path"]

    def test_empty_requires_list_available(self):
        """``requires: []`` is equivalent to ``{}`` — always available."""
        out = evaluate_tool_groups(
            {"x": {"requires": []}},
            {},
        )
        assert "x" in out.available_groups

    def test_malformed_requires_passthrough(self):
        """Non-list ``requires:`` is defensively accepted (validation
        catches at publish; resolver should not gate over a shape bug)."""
        out = evaluate_tool_groups(
            {"x": {"requires": "not-a-list"}},
            {},
        )
        assert "x" in out.available_groups


# ────────────────────────────────────────────────────────────────────
# is_group_available semantics
# ────────────────────────────────────────────────────────────────────


class TestIsGroupAvailable:

    def test_tool_without_group_always_available(self):
        out = GroupAvailability(
            available_groups=[], dropped_groups={"mg5": ["mg5_path"]},
            has_tool_groups_block=True,
        )
        # Tools with group=None are unaffected by tool_groups gating.
        assert out.is_group_available(None) is True

    def test_no_block_means_no_gating(self):
        out = GroupAvailability(
            available_groups=[], dropped_groups={},
            has_tool_groups_block=False,
        )
        # Backward compat: a tool may carry group="foo" with no
        # tool_groups: block. No gate fires.
        assert out.is_group_available("foo") is True

    def test_available_group_passes(self):
        out = GroupAvailability(
            available_groups=["pdg"], dropped_groups={},
            has_tool_groups_block=True,
        )
        assert out.is_group_available("pdg") is True

    def test_dropped_group_blocked(self):
        out = GroupAvailability(
            available_groups=["pdg"], dropped_groups={"mg5": ["mg5_path"]},
            has_tool_groups_block=True,
        )
        assert out.is_group_available("mg5") is False

    def test_unknown_group_blocked_when_block_present(self):
        """A tool naming a group not in the block is blocked when gating
        is active. Validation catches this at publish, but the resolver
        is defensive.
        """
        out = GroupAvailability(
            available_groups=["pdg"], dropped_groups={},
            has_tool_groups_block=True,
        )
        assert out.is_group_available("nonexistent") is False


# ────────────────────────────────────────────────────────────────────
# Log line format
# ────────────────────────────────────────────────────────────────────


class TestFormatSkipLogLine:

    def test_format_single_key(self):
        line = format_skip_log_line("heptapod", "mg5", ["mg5_path"])
        assert "[toolbase.serve] group_skipped" in line
        assert "toolkit=heptapod" in line
        assert "name=mg5" in line
        assert "reason=missing_config" in line
        assert "keys=mg5_path" in line

    def test_format_multi_key(self):
        line = format_skip_log_line(
            "heptapod", "feynrules",
            ["wolframscript_path", "feynrules_path"],
        )
        assert "keys=wolframscript_path,feynrules_path" in line


# ────────────────────────────────────────────────────────────────────
# Validation: schema + cross-reference rules at publish time
# ────────────────────────────────────────────────────────────────────


class TestValidation:
    """The ToolkitMetadata Pydantic model enforces the schema."""

    def _base_yaml(self):
        """Return a minimum-valid toolkit.yaml dict (with one tool)."""
        return {
            "name": "fake",
            "version": "0.1.0",
            "description": "test",
            "author": "Tester",
            "tools": [
                {
                    "name": "t1",
                    "function": "tools.t1.t1",
                    "description": "tool one",
                },
            ],
        }

    def test_no_tool_groups_field_accepted(self):
        """Backward compat: omitting tool_groups parses fine."""
        from toolbase.validation import ToolkitMetadata
        m = ToolkitMetadata(**self._base_yaml())
        assert m.tool_groups is None

    def test_empty_tool_groups_accepted(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["tool_groups"] = {}
        m = ToolkitMetadata(**y)
        assert m.tool_groups == {}

    def test_group_without_requires_accepted(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["tool_groups"] = {"pdg": {}}
        m = ToolkitMetadata(**y)
        assert m.tool_groups == {"pdg": {}}

    def test_requires_key_must_be_in_config_block(self):
        """A ``requires:`` entry that doesn't reference a declared
        config key is rejected at validate time."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["tool_groups"] = {"mg5": {"requires": ["mg5_path"]}}
        # No config: block declares mg5_path.
        with pytest.raises(Exception) as exc:
            ToolkitMetadata(**y)
        assert "mg5_path" in str(exc.value)

    def test_requires_key_present_in_config_accepted(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "mg5_path", "type": "path", "required": False},
        ]
        y["tool_groups"] = {"mg5": {"requires": ["mg5_path"]}}
        m = ToolkitMetadata(**y)
        assert m.tool_groups["mg5"]["requires"] == ["mg5_path"]

    def test_unknown_key_in_group_entry_rejected(self):
        """Typo defense: anything other than ``requires:`` is rejected."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "mg5_path", "type": "path", "required": False},
        ]
        y["tool_groups"] = {"mg5": {"require": ["mg5_path"]}}  # typo
        with pytest.raises(Exception) as exc:
            ToolkitMetadata(**y)
        assert "require" in str(exc.value).lower()

    def test_requires_must_be_list(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "x", "type": "string", "required": False},
        ]
        y["tool_groups"] = {"g": {"requires": "x"}}  # string, not list
        with pytest.raises(Exception):
            ToolkitMetadata(**y)

    def test_tool_group_field_must_reference_declared_group(self):
        """A tool whose ``group:`` references an undeclared group is
        rejected at validate time."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["tool_groups"] = {"declared": {}}
        y["tools"] = [
            {
                "name": "t1",
                "function": "tools.t1.t1",
                "description": "tool one",
                "group": "undeclared",
            },
        ]
        with pytest.raises(Exception) as exc:
            ToolkitMetadata(**y)
        assert "undeclared" in str(exc.value)

    def test_tool_group_field_referencing_declared_accepted(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["tool_groups"] = {"pdg": {}}
        y["tools"] = [
            {
                "name": "t1",
                "function": "tools.t1.t1",
                "description": "tool one",
                "group": "pdg",
            },
        ]
        m = ToolkitMetadata(**y)
        assert m.tools[0].group == "pdg"

    def test_tool_group_without_tool_groups_block_accepted(self):
        """Per the backward-compat rule: tools may declare ``group:``
        without a ``tool_groups:`` block — no semantic gating applies.
        """
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["tools"][0]["group"] = "foo"
        m = ToolkitMetadata(**y)
        assert m.tools[0].group == "foo"
        assert m.tool_groups is None

    def test_group_name_format_alphanumeric(self):
        """Group names must follow the alphanumeric+_-rule."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "x", "type": "string", "required": False},
        ]
        y["tool_groups"] = {"bad name!": {"requires": ["x"]}}
        with pytest.raises(Exception):
            ToolkitMetadata(**y)

    def test_tool_group_name_format_validator(self):
        """The per-tool ``group:`` field uses the same name shape."""
        from toolbase.validation import ToolDefinition
        with pytest.raises(Exception):
            ToolDefinition(
                name="t", function="tools.t.t", description="x",
                group="bad name!",
            )

    def test_validate_toolkit_surfaces_tool_groups_error_at_publish(self, tmp_path):
        """Full ``validate_toolkit()`` exercises the cross-reference
        check, mirroring what ``toolbase validate`` and
        ``toolbase publish`` do."""
        from toolbase.validation import validate_toolkit
        yaml_text = """\
name: fake
version: 0.1.0
description: test
author: Tester
config:
  - name: declared_key
    type: string
    required: false
tool_groups:
  bad:
    requires: [undeclared_key]
tools:
  - name: t1
    function: tools.t1.t1
    description: tool one
"""
        (tmp_path / "toolkit.yaml").write_text(yaml_text)
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "__init__.py").write_text("")
        (tmp_path / "mcp").mkdir()
        (tmp_path / "mcp" / "__init__.py").write_text("")
        (tmp_path / "mcp" / "server_stdio.py").write_text("")
        (tmp_path / "requirements.txt").write_text("orchestral-ai>=1.0.0\n")
        result = validate_toolkit(tmp_path)
        assert result.is_valid is False
        joined = " ".join(result.errors)
        assert "undeclared_key" in joined


# ────────────────────────────────────────────────────────────────────
# Two-layer config integration (lightweight; full e2e in run_tool_groups_e2e.py)
# ────────────────────────────────────────────────────────────────────


class TestTwoLayerConfigIntegration:
    """Verify the resolver honors the user→project two-layer merge.

    We exercise the merge directly via ``resolve_toolkit_config`` and
    feed the result into ``evaluate_tool_groups`` — same code path the
    orchestrator follows at startup.
    """

    def test_project_layer_unlocks_group(self, tmp_path, monkeypatch):
        """User layer is empty; project layer fills in the required
        key → group available."""
        from toolbase import config as toolbase_config
        from toolbase.envs.config import resolve_toolkit_config

        # Isolated user-level config dir.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(
            toolbase_config, "CONFIG_DIR", fake_home / ".toolbase",
        )

        project = tmp_path / "proj"
        (project / ".toolbase" / "config").mkdir(parents=True)
        (project / ".toolbase" / "config" / "heptapod.yaml").write_text(
            "schema_version: 1\nmg5_path: /opt/mg5\n"
        )
        merged = resolve_toolkit_config("heptapod", project)
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}}, merged,
        )
        assert "mg5" in out.available_groups

    def test_project_overrides_user_layer(self, tmp_path, monkeypatch):
        """User has the sentinel, project fills in a real value →
        group available (project wins key-by-key)."""
        from toolbase import config as toolbase_config
        from toolbase.envs.config import resolve_toolkit_config

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(
            toolbase_config, "CONFIG_DIR", fake_home / ".toolbase",
        )
        user_cfg = fake_home / ".toolbase" / "config"
        user_cfg.mkdir(parents=True)
        (user_cfg / "heptapod.yaml").write_text(
            "schema_version: 1\nmg5_path: <NEEDS VALUE>\n"
        )

        project = tmp_path / "proj"
        (project / ".toolbase" / "config").mkdir(parents=True)
        (project / ".toolbase" / "config" / "heptapod.yaml").write_text(
            "schema_version: 1\nmg5_path: /opt/mg5\n"
        )
        merged = resolve_toolkit_config("heptapod", project)
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}}, merged,
        )
        assert "mg5" in out.available_groups

    def test_neither_layer_set_drops_group(self, tmp_path, monkeypatch):
        from toolbase import config as toolbase_config
        from toolbase.envs.config import resolve_toolkit_config

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(
            toolbase_config, "CONFIG_DIR", fake_home / ".toolbase",
        )
        project = tmp_path / "proj"
        project.mkdir()
        merged = resolve_toolkit_config("heptapod", project)
        out = evaluate_tool_groups(
            {"mg5": {"requires": ["mg5_path"]}}, merged,
        )
        assert "mg5" in out.dropped_groups
