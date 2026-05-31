"""Unit tests for ``toolbase/envs/bundle_deps.py``.

Covers ``deps_for_bundles`` (union of pip-specs across a selected
subset of bundles, dedup, defensive against malformed yaml) and
``declared_bundle_names`` (the simple list-of-names accessor).
"""

from __future__ import annotations

from toolbase.envs.bundle_deps import deps_for_bundles, declared_bundle_names


# ── deps_for_bundles ──────────────────────────────────────────────────


def test_empty_yaml_returns_empty():
    assert deps_for_bundles(None, ["alpha"]) == []
    assert deps_for_bundles({}, ["alpha"]) == []


def test_empty_selection_returns_empty():
    y = {"bundles": {"alpha": {"deps": ["numpy"]}}}
    assert deps_for_bundles(y, []) == []


def test_no_bundles_block_returns_empty():
    y = {"name": "x", "tools": []}
    assert deps_for_bundles(y, ["alpha"]) == []


def test_single_bundle_returns_its_deps():
    y = {"bundles": {"alpha": {"deps": ["requests", "click"]}}}
    assert deps_for_bundles(y, ["alpha"]) == ["requests", "click"]


def test_multiple_bundles_unioned_in_order():
    y = {
        "bundles": {
            "alpha": {"deps": ["requests"]},
            "beta": {"deps": ["numpy>=2.0"]},
            "gamma": {"deps": ["pandas"]},
        }
    }
    assert deps_for_bundles(y, ["alpha", "beta"]) == ["requests", "numpy>=2.0"]
    # gamma not selected, so it's omitted
    assert deps_for_bundles(y, ["alpha", "gamma"]) == ["requests", "pandas"]


def test_dedupe_across_bundles():
    """A pip-spec shared by two selected bundles appears once."""
    y = {
        "bundles": {
            "alpha": {"deps": ["requests", "shared-thing"]},
            "beta": {"deps": ["shared-thing", "numpy"]},
        }
    }
    assert deps_for_bundles(y, ["alpha", "beta"]) == [
        "requests", "shared-thing", "numpy",
    ]


def test_selection_order_preserved():
    """Iteration order of ``selected_bundles`` drives insertion order."""
    y = {
        "bundles": {
            "alpha": {"deps": ["a"]},
            "beta": {"deps": ["b"]},
        }
    }
    assert deps_for_bundles(y, ["beta", "alpha"]) == ["b", "a"]


def test_unknown_bundle_in_selection_contributes_nothing():
    """Selecting a bundle that doesn't exist in the yaml is a noop."""
    y = {"bundles": {"alpha": {"deps": ["numpy"]}}}
    assert deps_for_bundles(y, ["alpha", "ghost"]) == ["numpy"]


def test_bundle_without_deps_contributes_nothing():
    """A bundle entry with no ``deps:`` (or absent) is fine — just empty."""
    y = {
        "bundles": {
            "alpha": {"requires": ["some_key"]},
            "beta": {},
            "gamma": {"deps": ["actual-dep"]},
        }
    }
    assert deps_for_bundles(y, ["alpha", "beta", "gamma"]) == ["actual-dep"]


def test_malformed_entries_skipped_silently():
    """Defensive: non-dict bundle entries, non-list deps, non-string
    items are skipped without raising (validator is the gate; this
    helper trusts incoming yaml or shrugs)."""
    y = {
        "bundles": {
            "alpha": "this-should-be-a-dict",     # bad entry
            "beta": {"deps": "this-should-be-list"},  # bad deps
            "gamma": {"deps": ["ok-1", 42, "", "   ", "ok-2"]},  # mixed
        }
    }
    assert deps_for_bundles(y, ["alpha", "beta", "gamma"]) == ["ok-1", "ok-2"]


# ── declared_bundle_names ─────────────────────────────────────────────


def test_declared_names_returns_bundle_keys():
    y = {
        "bundles": {
            "alpha": {},
            "beta": {"deps": ["x"]},
            "gamma": {"requires": ["y"]},
        }
    }
    assert sorted(declared_bundle_names(y)) == ["alpha", "beta", "gamma"]


def test_declared_names_empty_when_no_block():
    assert declared_bundle_names(None) == []
    assert declared_bundle_names({}) == []
    assert declared_bundle_names({"name": "x"}) == []


def test_declared_names_skips_non_string_keys():
    """Defensive: a yaml with a non-string bundle name is silently filtered."""
    y = {"bundles": {"alpha": {}, 42: {}, "beta": {}}}
    assert sorted(declared_bundle_names(y)) == ["alpha", "beta"]
