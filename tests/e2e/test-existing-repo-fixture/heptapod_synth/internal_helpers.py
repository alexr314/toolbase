"""Module with no tools — should NOT appear in the ingest output.

Some real-world repos have helper modules that look superficially like
tool modules (they import orchestral) but don't actually decorate
anything. Ingest should skip them silently.
"""

from __future__ import annotations

# Even importing define_tool without using it shouldn't yield a tool.
from orchestral import define_tool  # noqa: F401


def helper_function(x):
    """Internal helper, not exposed as a tool."""
    return x * 3
