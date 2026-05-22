"""Semver-ish version helpers.

We accept either ``major.minor`` or ``major.minor.patch``. Pre-release /
build-metadata suffixes (``-rc.1``, ``+build.42``) are tolerated but
ignored for ordering — the validator only cares whether a proposed
version is *greater than* what's already on the registry.

Strict PEP 440 / semver compliance is intentionally not enforced here;
authors writing ``1.0`` are common and shouldn't be penalized.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple


_NUMERIC_RE = re.compile(r"^(\d+)$")


def _strip_suffix(v: str) -> str:
    """Remove pre-release / build-metadata suffix.

    Splits on the first ``-`` or ``+`` and returns everything before it.
    ``1.2.3-rc.1`` → ``1.2.3``; ``1.2.3+build.42`` → ``1.2.3``.
    """
    for sep in ("-", "+"):
        idx = v.find(sep)
        if idx >= 0:
            v = v[:idx]
    return v


def parse_version(v: str) -> Optional[Tuple[int, ...]]:
    """Parse a version string into a tuple of ints for comparison.

    Returns None for malformed input. Pads ``major.minor`` to 3 components
    so ``1.2`` and ``1.2.0`` compare equal. Pre-release / build-metadata
    suffixes (``-rc.1``, ``+build.42``) are stripped before parsing.
    """
    if not isinstance(v, str) or not v.strip():
        return None
    core = _strip_suffix(v.strip())
    parts = core.split(".")
    if len(parts) < 2 or len(parts) > 3:
        return None
    out: List[int] = []
    for p in parts:
        m = _NUMERIC_RE.match(p)
        if not m:
            return None
        out.append(int(m.group(1)))
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_strictly_greater(new: str, old: str) -> Optional[bool]:
    """Return True iff ``new > old``. None if either is unparseable."""
    a = parse_version(new)
    b = parse_version(old)
    if a is None or b is None:
        return None
    return a > b


def suggest_next_version(current: str) -> Optional[str]:
    """Suggest the next reasonable version after ``current``.

    Bumps the patch (or minor, if no patch component existed). Returns
    None if the input can't be parsed. Pre-release / build suffixes are
    dropped from the suggestion.
    """
    if not isinstance(current, str):
        return None
    core = _strip_suffix(current.strip())
    parts = core.split(".")
    if len(parts) < 2 or len(parts) > 3:
        return None
    nums: List[int] = []
    for p in parts:
        m = _NUMERIC_RE.match(p)
        if not m:
            return None
        nums.append(int(m.group(1)))
    if len(nums) == 2:
        # major.minor → bump minor
        return f"{nums[0]}.{nums[1] + 1}"
    return f"{nums[0]}.{nums[1]}.{nums[2] + 1}"


def max_version(versions: List[str]) -> Optional[str]:
    """Return the highest parseable version in the list, or None if empty
    or none parse.
    """
    parsed = [(v, parse_version(v)) for v in versions]
    parsed = [(v, t) for v, t in parsed if t is not None]
    if not parsed:
        return None
    return max(parsed, key=lambda pair: pair[1])[0]
