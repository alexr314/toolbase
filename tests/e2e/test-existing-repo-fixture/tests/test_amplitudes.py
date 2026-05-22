"""Test file — should be skipped by ingest's walker.

If ingest mistakenly picks tools up from a tests/ directory, the e2e
asserts will fail. This file is a regression guard.
"""

from __future__ import annotations

from orchestral import define_tool


@define_tool
def fake_tool_should_not_appear():
    """If ingest picks this up, the test fixture is broken."""
    return "should not happen"
