"""Canonical tool-naming rules for toolbase.

One home for the rule that decides how a tool is named on the MCP wire, so the
serve host (``_toolkit_host``), the orchestrator, and external integrators
(e.g. toolbench's ``python:`` bridge, which reproduces a served toolkit's names
in-process) all agree instead of each re-deriving it. Re-deriving is how names
silently drift: a naive "strip ``Tool`` and lowercase" gets ``SortByPtTool``
wrong (the served name is ``SortByPT``, from its display name), so the rule
must live in exactly one place.

Two forms:
  - ``mcp_tool_name``       -> the bare name (``CalculateInvariantMass``)
  - ``namespaced_tool_name`` -> the qualified name (``heptapod__CalculateInvariantMass``)

The qualified form is what ``tb serve`` advertises today; the bare form is the
default proposed in issue #29 (with the qualified form behind ``--qualified``).
Keeping both here means that switch is a one-line change at the call sites, not
a rule rewrite. See also issue #28 (display-name + PascalCase default).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "strip_tool_suffix",
    "mcp_tool_name",
    "namespaced_tool_name",
    "find_name_collisions",
]


def strip_tool_suffix(class_name: str) -> str:
    """A tool class's default wire name: the class name with a trailing
    ``Tool`` removed, PascalCase preserved (``InspireSearchTool`` ->
    ``InspireSearch``). No lowercasing — that was the pre-#28 behavior and it
    lost word boundaries."""
    return class_name.removesuffix("Tool")


def mcp_tool_name(tool_or_class: Any, display_name: Optional[str] = None) -> str:
    """The bare (un-namespaced) name toolbase advertises a tool under.

    Precedence: an explicit ``display_name`` (a ``toolkit.yaml`` ``display_name:``
    or ``@define_tool(display_name=...)``) wins; otherwise the class name with a
    trailing ``Tool`` stripped.

    ``tool_or_class`` may be a class-name ``str`` or a live tool instance. For an
    instance, a display name already resolved onto it as ``_mcp_display_name``
    (as the host does) is honored — so an in-process consumer that holds only the
    instance (not its ``toolkit.yaml``) reproduces the served name without
    re-deriving the rule. The one case a bare instance can't see is a
    ``toolkit.yaml``-level ``display_name:`` that was never applied to it.
    """
    if isinstance(tool_or_class, str):
        return display_name or strip_tool_suffix(tool_or_class)
    resolved = display_name or getattr(tool_or_class, "_mcp_display_name", None)
    if resolved:
        return resolved
    return strip_tool_suffix(type(tool_or_class).__name__)


def namespaced_tool_name(toolkit: str, tool_or_class: Any,
                         display_name: Optional[str] = None) -> str:
    """The qualified wire name, ``<toolkit>__<bare>``. This is the default
    form ``tb serve`` advertises; a bare (un-namespaced) mode is opt-in."""
    return f"{toolkit}__{mcp_tool_name(tool_or_class, display_name)}"


def find_name_collisions(
    tools_by_toolkit: Dict[str, Iterable[str]],
) -> Dict[str, List[str]]:
    """Bare tool names exposed by more than one toolkit.

    Given ``{toolkit: iterable-of-bare-tool-names}``, return
    ``{bare_name: sorted[toolkits]}`` for every bare name provided by two or
    more toolkits. Under the default ``<toolkit>__<tool>`` serving these are
    harmless — the toolkit prefix keeps them distinct — but they are the exact
    names that would clash if tools were ever served un-namespaced, and often
    signal overlapping toolkits worth curating. Callers surface them (serve
    startup, ``tb list -v``, ``tb install``) so the overlap is never silent.
    """
    owners: Dict[str, set] = defaultdict(set)
    for toolkit, names in tools_by_toolkit.items():
        for name in names:
            owners[name].add(toolkit)
    return {name: sorted(tks) for name, tks in owners.items() if len(tks) > 1}
