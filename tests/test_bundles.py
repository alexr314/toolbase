"""Unit tests for the ``bundles.requires:`` evaluation (0.5.1).

These tests cover the pure resolver in
``toolbase.serve.bundles`` and the validation rules in
``toolbase.validation`` that enforce the schema at publish time.

End-to-end orchestrator integration is exercised by
``tests/e2e/run_bundles_e2e.py``.
"""

from __future__ import annotations

import pytest

from toolbase.serve.bundles import (
    BundleAvailability,
    NEEDS_VALUE_SENTINEL,
    evaluate_bundles,
    format_skip_log_line,
)


# ────────────────────────────────────────────────────────────────────
# Pure resolver: evaluate_bundles
# ────────────────────────────────────────────────────────────────────


class TestEvaluateToolBundles:
    """Pure-function tests for ``evaluate_bundles``."""

    def test_no_block_returns_empty_availability(self):
        """No bundles block → no gating active, no logs."""
        out = evaluate_bundles(None, {})
        assert out.has_bundles_block is False
        assert out.available_bundles == []
        assert out.dropped_bundles == {}

    def test_empty_block_returns_empty_availability(self):
        """``bundles: {}`` also means no gating active."""
        out = evaluate_bundles({}, {})
        assert out.has_bundles_block is False

    def test_bundle_without_requires_always_available(self):
        """``pdg: {}`` has no ``requires:`` → unconditionally available."""
        out = evaluate_bundles({"pdg": {}}, {})
        assert out.has_bundles_block is True
        assert "pdg" in out.available_bundles
        assert "pdg" not in out.dropped_bundles

    def test_single_require_satisfied(self):
        """``mg5: {requires: [mg5_path]}`` with mg5_path set → available."""
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": "/opt/mg5"},
        )
        assert "mg5" in out.available_bundles
        assert "mg5" not in out.dropped_bundles

    def test_single_require_missing(self):
        """``mg5: {requires: [mg5_path]}`` with mg5_path absent → dropped."""
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}},
            {},
        )
        assert "mg5" not in out.available_bundles
        assert out.dropped_bundles["mg5"] == ["mg5_path"]

    def test_needs_value_sentinel_counts_as_unset(self):
        """The ``<NEEDS VALUE>`` sentinel is treated as missing."""
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": NEEDS_VALUE_SENTINEL},
        )
        assert "mg5" in out.dropped_bundles
        assert out.dropped_bundles["mg5"] == ["mg5_path"]

    def test_none_value_counts_as_unset(self):
        """Explicit None on a required key → unset."""
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": None},
        )
        assert "mg5" in out.dropped_bundles

    def test_empty_string_counts_as_unset(self):
        """Whitespace-only string → unset (typical of user leaving blank)."""
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}},
            {"mg5_path": "   "},
        )
        assert "mg5" in out.dropped_bundles

    def test_falsy_meaningful_values_count_as_set(self):
        """False and 0 are valid values (user set them deliberately)."""
        out = evaluate_bundles(
            {
                "g1": {"requires": ["enabled"]},
                "g2": {"requires": ["count"]},
            },
            {"enabled": False, "count": 0},
        )
        assert "g1" in out.available_bundles
        assert "g2" in out.available_bundles

    def test_multi_require_partial_missing(self):
        """One of two requires missing → dropped, missing list mentions it."""
        out = evaluate_bundles(
            {"feynrules": {"requires": ["wolframscript_path", "feynrules_path"]}},
            {"wolframscript_path": "/usr/bin/wolframscript"},
        )
        assert "feynrules" in out.dropped_bundles
        assert out.dropped_bundles["feynrules"] == ["feynrules_path"]

    def test_multi_require_all_satisfied(self):
        """All required keys set → bundle available."""
        out = evaluate_bundles(
            {"feynrules": {"requires": ["wolframscript_path", "feynrules_path"]}},
            {
                "wolframscript_path": "/usr/bin/wolframscript",
                "feynrules_path": "/opt/feynrules",
            },
        )
        assert "feynrules" in out.available_bundles

    def test_multiple_bundles_mixed_availability(self):
        """Several bundles with mixed available/dropped states."""
        block = {
            "pdg": {},
            "mg5": {"requires": ["mg5_path"]},
            "eda": {"requires": ["wolframscript_path"]},
            "feynrules": {"requires": ["wolframscript_path", "feynrules_path"]},
        }
        out = evaluate_bundles(
            block,
            {"wolframscript_path": "/usr/bin/ws"},
        )
        assert set(out.available_bundles) == {"pdg", "eda"}
        assert set(out.dropped_bundles) == {"mg5", "feynrules"}
        assert out.dropped_bundles["feynrules"] == ["feynrules_path"]
        assert out.dropped_bundles["mg5"] == ["mg5_path"]

    def test_empty_requires_list_available(self):
        """``requires: []`` is equivalent to ``{}`` — always available."""
        out = evaluate_bundles(
            {"x": {"requires": []}},
            {},
        )
        assert "x" in out.available_bundles

    def test_malformed_requires_passthrough(self):
        """Non-list ``requires:`` is defensively accepted (validation
        catches at publish; resolver should not gate over a shape bug)."""
        out = evaluate_bundles(
            {"x": {"requires": "not-a-list"}},
            {},
        )
        assert "x" in out.available_bundles


# ────────────────────────────────────────────────────────────────────
# is_bundle_available semantics
# ────────────────────────────────────────────────────────────────────


class TestIsBundleAvailable:

    def test_tool_without_bundle_always_available(self):
        out = BundleAvailability(
            available_bundles=[], dropped_bundles={"mg5": ["mg5_path"]},
            has_bundles_block=True,
        )
        # Tools with bundle=None are unaffected by bundles gating.
        assert out.is_bundle_available(None) is True

    def test_no_block_means_no_gating(self):
        out = BundleAvailability(
            available_bundles=[], dropped_bundles={},
            has_bundles_block=False,
        )
        # Backward compat: a tool may carry bundle="foo" with no
        # bundles: block. No gate fires.
        assert out.is_bundle_available("foo") is True

    def test_available_bundle_passes(self):
        out = BundleAvailability(
            available_bundles=["pdg"], dropped_bundles={},
            has_bundles_block=True,
        )
        assert out.is_bundle_available("pdg") is True

    def test_dropped_bundle_blocked(self):
        out = BundleAvailability(
            available_bundles=["pdg"], dropped_bundles={"mg5": ["mg5_path"]},
            has_bundles_block=True,
        )
        assert out.is_bundle_available("mg5") is False

    def test_unknown_bundle_blocked_when_block_present(self):
        """A tool naming a bundle not in the block is blocked when gating
        is active. Validation catches this at publish, but the resolver
        is defensive.
        """
        out = BundleAvailability(
            available_bundles=["pdg"], dropped_bundles={},
            has_bundles_block=True,
        )
        assert out.is_bundle_available("nonexistent") is False


# ────────────────────────────────────────────────────────────────────
# Log line format
# ────────────────────────────────────────────────────────────────────


class TestFormatSkipLogLine:

    def test_format_single_key(self):
        line = format_skip_log_line("heptapod", "mg5", ["mg5_path"])
        assert "[toolbase.serve] bundle_skipped" in line
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

    def test_no_bundles_field_accepted(self):
        """Backward compat: omitting bundles parses fine."""
        from toolbase.validation import ToolkitMetadata
        m = ToolkitMetadata(**self._base_yaml())
        assert m.bundles is None

    def test_empty_bundles_accepted(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {}
        m = ToolkitMetadata(**y)
        assert m.bundles == {}

    def test_bundle_without_requires_accepted(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"pdg": {}}
        m = ToolkitMetadata(**y)
        assert m.bundles == {"pdg": {}}

    def test_requires_key_must_be_in_config_block(self):
        """A ``requires:`` entry that doesn't reference a declared
        config key is rejected at validate time."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"mg5": {"requires": ["mg5_path"]}}
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
        y["bundles"] = {"mg5": {"requires": ["mg5_path"]}}
        m = ToolkitMetadata(**y)
        assert m.bundles["mg5"]["requires"] == ["mg5_path"]

    def test_unknown_key_in_bundle_entry_rejected(self):
        """Typo defense: anything other than ``requires:`` is rejected."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "mg5_path", "type": "path", "required": False},
        ]
        y["bundles"] = {"mg5": {"require": ["mg5_path"]}}  # typo
        with pytest.raises(Exception) as exc:
            ToolkitMetadata(**y)
        assert "require" in str(exc.value).lower()

    def test_requires_must_be_list(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "x", "type": "string", "required": False},
        ]
        y["bundles"] = {"g": {"requires": "x"}}  # string, not list
        with pytest.raises(Exception):
            ToolkitMetadata(**y)

    def test_bundle_field_must_reference_declared_bundle(self):
        """A tool whose ``bundle:`` references an undeclared bundle is
        rejected at validate time."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"declared": {}}
        y["tools"] = [
            {
                "name": "t1",
                "function": "tools.t1.t1",
                "description": "tool one",
                "bundle": "undeclared",
            },
        ]
        with pytest.raises(Exception) as exc:
            ToolkitMetadata(**y)
        assert "undeclared" in str(exc.value)

    def test_bundle_field_referencing_declared_accepted(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"pdg": {}}
        y["tools"] = [
            {
                "name": "t1",
                "function": "tools.t1.t1",
                "description": "tool one",
                "bundle": "pdg",
            },
        ]
        m = ToolkitMetadata(**y)
        assert m.tools[0].bundle == "pdg"

    def test_bundle_without_bundles_block_accepted(self):
        """Per the backward-compat rule: tools may declare ``bundle:``
        without a ``bundles:`` block — no semantic gating applies.
        """
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["tools"][0]["bundle"] = "foo"
        m = ToolkitMetadata(**y)
        assert m.tools[0].bundle == "foo"
        assert m.bundles is None

    def test_bundle_name_format_alphanumeric(self):
        """Bundle names must follow the alphanumeric+_-rule."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "x", "type": "string", "required": False},
        ]
        y["bundles"] = {"bad name!": {"requires": ["x"]}}
        with pytest.raises(Exception):
            ToolkitMetadata(**y)

    def test_bundle_name_format_validator(self):
        """The per-tool ``bundle:`` field uses the same name shape."""
        from toolbase.validation import ToolDefinition
        with pytest.raises(Exception):
            ToolDefinition(
                name="t", function="tools.t.t", description="x",
                bundle="bad name!",
            )

    # ── per-bundle deps ──────────────────────────────────────────────

    def test_bundle_with_deps_accepted(self):
        """``deps: [pip-spec, ...]`` is a recognized bundle-entry key."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {
            "scientific": {"deps": ["numpy>=2.0", "pandas"]},
        }
        m = ToolkitMetadata(**y)
        assert m.bundles["scientific"]["deps"] == ["numpy>=2.0", "pandas"]

    def test_bundle_with_requires_and_deps_together(self):
        """``requires:`` and ``deps:`` coexist on the same bundle."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["config"] = [
            {"name": "cas_path", "type": "string", "required": False},
        ]
        y["bundles"] = {
            "symbolic": {
                "requires": ["cas_path"],
                "deps": ["sympy>=1.14"],
            },
        }
        m = ToolkitMetadata(**y)
        b = m.bundles["symbolic"]
        assert b["requires"] == ["cas_path"]
        assert b["deps"] == ["sympy>=1.14"]

    def test_bundle_deps_must_be_list(self):
        """``deps:`` rejects scalar values (typed as list of pip-specs)."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"scientific": {"deps": "numpy"}}
        with pytest.raises(Exception, match="must be a list"):
            ToolkitMetadata(**y)

    def test_bundle_deps_entries_must_be_non_empty_strings(self):
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"scientific": {"deps": ["numpy", 42]}}
        with pytest.raises(Exception, match="non-empty string"):
            ToolkitMetadata(**y)

        y["bundles"] = {"scientific": {"deps": ["numpy", "   "]}}
        with pytest.raises(Exception, match="non-empty string"):
            ToolkitMetadata(**y)

    def test_bundle_unknown_key_still_rejected(self):
        """Sanity: keys other than 'requires'/'deps' still rejected."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"x": {"depz": ["numpy"]}}
        with pytest.raises(Exception, match="unknown key"):
            ToolkitMetadata(**y)

    def test_bundle_with_empty_deps_list_accepted(self):
        """Empty ``deps: []`` is valid (bundle adds nothing beyond base)."""
        from toolbase.validation import ToolkitMetadata
        y = self._base_yaml()
        y["bundles"] = {"basic": {"deps": []}}
        m = ToolkitMetadata(**y)
        assert m.bundles["basic"]["deps"] == []

    def test_validate_toolkit_surfaces_bundles_error_at_publish(self, tmp_path):
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
bundles:
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
# Two-layer config integration (lightweight; full e2e in run_bundles_e2e.py)
# ────────────────────────────────────────────────────────────────────


class TestTwoLayerConfigIntegration:
    """Verify the resolver honors the user→project two-layer merge.

    We exercise the merge directly via ``resolve_toolkit_config`` and
    feed the result into ``evaluate_bundles`` — same code path the
    orchestrator follows at startup.
    """

    def test_project_layer_unlocks_bundle(self, tmp_path, monkeypatch):
        """User layer is empty; project layer fills in the required
        key → bundle available."""
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
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}}, merged,
        )
        assert "mg5" in out.available_bundles

    def test_project_overrides_user_layer(self, tmp_path, monkeypatch):
        """User has the sentinel, project fills in a real value →
        bundle available (project wins key-by-key)."""
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
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}}, merged,
        )
        assert "mg5" in out.available_bundles

    def test_neither_layer_set_drops_bundle(self, tmp_path, monkeypatch):
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
        out = evaluate_bundles(
            {"mg5": {"requires": ["mg5_path"]}}, merged,
        )
        assert "mg5" in out.dropped_bundles
