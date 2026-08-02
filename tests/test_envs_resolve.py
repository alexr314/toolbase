"""Shared version resolution — ``toolbase.envs.resolve``.

The rule itself (pin > only > highest, dangling pin resolves to nothing)
plus the guarantee the module exists for: serve, ``tb setup`` and
``tb list`` all reach the same answer for the same cache + manifest.
"""

from __future__ import annotations

import pytest

from toolbase.envs.resolve import (
    EDITABLE_SLOT,
    HIGHEST,
    NOT_INSTALLED,
    ONLY,
    PINNED,
    PIN_MISSING,
    resolve_version,
    sort_versions,
)


class TestResolveVersion:
    def test_pin_wins_over_higher_version(self):
        r = resolve_version(["1.0.0", "2.0.0"], pin="1.0.0")
        assert r.version == "1.0.0"
        assert r.reason == PINNED
        assert r.ok

    def test_single_version_no_pin(self):
        r = resolve_version(["1.0.0"])
        assert r.version == "1.0.0"
        assert r.reason == ONLY
        assert not r.is_ambiguous

    def test_multiple_versions_no_pin_picks_highest_and_flags_it(self):
        r = resolve_version(["1.0.0", "2.3.0", "2.10.0"])
        assert r.version == "2.10.0"
        assert r.reason == HIGHEST
        # The caller has to be able to tell that nobody chose this.
        assert r.is_ambiguous

    def test_dangling_pin_resolves_to_nothing(self):
        """A pin naming an absent slot must NOT silently fall through to
        another version — serve refuses instead."""
        r = resolve_version(["2.3.0", "2.4.0"], pin="editable")
        assert r.version is None
        assert not r.ok
        assert r.reason == PIN_MISSING
        assert r.pin == "editable"
        assert "editable" in r.describe()
        assert "2.4.0" in r.describe()

    def test_nothing_installed(self):
        r = resolve_version([])
        assert r.version is None
        assert r.reason == NOT_INSTALLED

    def test_editable_pin_selects_editable_slot(self):
        r = resolve_version(["1.0.0", "editable"], pin="editable")
        assert r.version == "editable"
        assert r.reason == PINNED

    def test_editable_wins_over_numbered_when_unpinned(self):
        """An editable slot is a live checkout someone deliberately
        linked, so it heads the fallback ordering. Before, it sorted to
        the bottom and a developer's own code silently didn't serve."""
        r = resolve_version(["1.0.0", "2.0.0", "editable"])
        assert r.version == "editable"
        assert r.reason == EDITABLE_SLOT
        # The reason names what it beat, so the win isn't silent.
        assert "outranks" in r.describe()
        assert "2.0.0" in r.describe()

    def test_explicit_pin_beats_the_editable_fallback(self):
        """Explicit beats implicit — the ordering only decides the
        fallback, so a pinned version is safe from a stray checkout.
        This is what keeps a pinned loadout reproducible."""
        r = resolve_version(["1.0.0", "2.0.0", "editable"], pin="2.0.0")
        assert r.version == "2.0.0"
        assert r.reason == PINNED

    def test_lone_editable_slot_reports_editable_not_only(self):
        r = resolve_version(["editable"])
        assert r.version == "editable"
        assert r.reason == EDITABLE_SLOT
        assert "outranks" not in r.describe()

    def test_available_is_ordered_highest_first(self):
        r = resolve_version(["1.0.0", "2.10.0", "2.3.0"])
        assert r.available == ["2.10.0", "2.3.0", "1.0.0"]


class TestSortVersions:
    def test_numeric_ordering_is_not_lexicographic(self):
        assert sort_versions(["2.9.0", "2.10.0"]) == ["2.10.0", "2.9.0"]

    def test_two_component_versions_pad(self):
        assert sort_versions(["1.2", "1.10"]) == ["1.10", "1.2"]

    def test_editable_sorts_first(self):
        assert sort_versions(["0.1.0", "editable"]) == ["editable", "0.1.0"]

    def test_other_unparseable_names_still_sort_last(self):
        """Only ``editable`` is privileged; anything else unparseable
        keeps its old bottom placement."""
        assert sort_versions(["nonsense", "0.1.0"]) == ["0.1.0", "nonsense"]


class TestDescribe:
    @pytest.mark.parametrize(
        "available,pin,fragment",
        [
            (["1.0.0", "2.0.0"], "1.0.0", "pinned to 1.0.0"),
            (["1.0.0"], None, "only version installed"),
            (["editable"], None, "editable checkout"),
            (["1.0.0", "2.0.0"], None, "highest installed, no pin"),
            (["1.0.0"], "9.9.9", "not installed"),
        ],
    )
    def test_reason_phrases(self, available, pin, fragment):
        assert fragment in resolve_version(available, pin=pin).describe()
