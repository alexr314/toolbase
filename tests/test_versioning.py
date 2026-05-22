"""Tests for ``toolbase/versioning.py``.

The publish command's pre-flight version check uses these helpers.
``parse_version`` accepts ``major.minor`` and ``major.minor.patch``,
tolerates pre-release suffixes for parsing, ignores them for ordering,
and returns None for anything else.
"""

from __future__ import annotations

from toolbase import versioning


def test_parse_basic_three_part():
    assert versioning.parse_version("1.2.3") == (1, 2, 3)


def test_parse_two_part_pads_with_zero():
    assert versioning.parse_version("1.2") == (1, 2, 0)


def test_parse_with_prerelease_suffix_ignored_for_compare():
    assert versioning.parse_version("1.2.3-rc.1") == (1, 2, 3)


def test_parse_with_build_suffix_ignored_for_compare():
    assert versioning.parse_version("1.2.3+build.42") == (1, 2, 3)


def test_parse_rejects_one_part():
    assert versioning.parse_version("5") is None


def test_parse_rejects_four_parts():
    assert versioning.parse_version("1.2.3.4") is None


def test_parse_rejects_garbage():
    for bad in ["", " ", "abc", "1.x.0", None, 1.0]:
        assert versioning.parse_version(bad) is None


def test_is_strictly_greater_basic():
    assert versioning.is_strictly_greater("1.0.1", "1.0.0") is True
    assert versioning.is_strictly_greater("1.0.0", "1.0.1") is False
    assert versioning.is_strictly_greater("1.0.0", "1.0.0") is False


def test_is_strictly_greater_two_vs_three_part_equivalent():
    """1.2 == 1.2.0 → not strictly greater either way."""
    assert versioning.is_strictly_greater("1.2", "1.2.0") is False
    assert versioning.is_strictly_greater("1.2.0", "1.2") is False


def test_is_strictly_greater_returns_none_for_unparseable():
    assert versioning.is_strictly_greater("garbage", "1.0.0") is None
    assert versioning.is_strictly_greater("1.0.0", "garbage") is None


def test_suggest_next_version_bumps_patch():
    assert versioning.suggest_next_version("1.2.3") == "1.2.4"


def test_suggest_next_version_bumps_minor_when_no_patch():
    assert versioning.suggest_next_version("1.2") == "1.3"


def test_suggest_next_version_returns_none_for_unparseable():
    assert versioning.suggest_next_version("garbage") is None
    assert versioning.suggest_next_version("") is None


def test_max_version_picks_highest():
    assert versioning.max_version(["1.0.0", "1.2.3", "1.1.5"]) == "1.2.3"


def test_max_version_skips_unparseable():
    assert versioning.max_version(["1.0.0", "garbage", "1.2.0"]) == "1.2.0"


def test_max_version_empty_list():
    assert versioning.max_version([]) is None


def test_max_version_all_unparseable():
    assert versioning.max_version(["garbage", "more-garbage"]) is None
