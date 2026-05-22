"""
ProxyTool — synthesized BaseTool that forwards calls to a child MCPClient.

Each per-toolkit subprocess exposes its tools via FastMCP HTTP. The
orchestrator connects an ``MCPClient`` to that subprocess, asks for the tool
list, and synthesizes one ProxyTool per child tool. The orchestrator then
hands the ProxyTool list to its own ``MCPServer`` (stdio), which is what
Claude Code talks to.

Why a class, not a function: ``orchestral.mcp.MCPServer`` walks a list of
``BaseTool`` instances and calls ``tool.get_tool_spec()`` and
``tool.execute()`` on each one. We need to satisfy that interface without
dragging in any of the per-call validation, state-save/restore plumbing
that ``BaseTool.execute`` does (the *real* tool, in the child subprocess,
already does that work).

So ProxyTool is a thin BaseTool subclass that overrides ``get_tool_spec``,
``get_input_schema``, and ``execute`` to forward to the child. Schema info
is forwarded *as discovered* — the child's FastMCP server already produced
the right MCP-style schema for it, including state-field redaction.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.tool_spec import ToolSpec


def make_proxy_tool(
    upstream_name: str,
    namespaced_name: str,
    description: str,
    input_schema: Dict[str, Any],
    forward: Callable[[str, Dict[str, Any]], str],
) -> BaseTool:
    """
    Build a ProxyTool instance.

    Args:
        upstream_name: the tool's name as exposed by the per-toolkit
            subprocess (e.g. ``analyze_star``). Used when calling the child.
        namespaced_name: the name we surface to Claude Code (e.g.
            ``aster__analyze_star``). Used by ``MCPServer`` for routing.
        description: tool description forwarded from the child.
        input_schema: JSON schema (MCP shape) forwarded from the child.
        forward: callable (`upstream_name`, `kwargs` → `result_string`) the
            proxy invokes when the orchestrator's MCPServer calls
            ``execute()``. Lets the orchestrator wire in retry, timeout,
            and logging without baking it into the proxy.

    Returns:
        A ProxyTool instance, ready to be added to the orchestrator's
        MCPServer tool list.
    """
    # Synthesize a per-call subclass so each proxy has its own metadata.
    # Building one *class* per child tool keeps the BaseTool contract clean.
    cls = type(
        f"Proxy_{namespaced_name}",
        (_ProxyToolBase,),
        {},
    )
    instance = cls()
    instance._stk_upstream_name = upstream_name
    instance._stk_namespaced_name = namespaced_name
    instance._stk_description = description
    instance._stk_input_schema = input_schema
    instance._stk_forward = forward
    return instance


class _ProxyToolBase(BaseTool):
    """Internal base for synthesized proxies. See ``make_proxy_tool``."""

    # Pydantic config: the synthesized subclass has no real fields, but we
    # still want extra fields permitted so the underscore-prefixed
    # forwarding hooks are settable on the instance.
    class Config:
        validate_assignment = False
        extra = "allow"

    # These attributes are set per-instance by ``make_proxy_tool``. Listed
    # here purely so type checkers see them.
    _stk_upstream_name: str
    _stk_namespaced_name: str
    _stk_description: str
    _stk_input_schema: Dict[str, Any]
    _stk_forward: Callable[[str, Dict[str, Any]], str]

    def get_name(self) -> str:  # type: ignore[override]
        return self._stk_namespaced_name

    @classmethod
    def _get_runtime_fields(cls):  # type: ignore[override]
        # MCPServer never reads this on a proxy (it goes through
        # get_tool_spec instead), but other Orchestral plumbing might.
        return []

    @classmethod
    def _get_state_fields(cls):  # type: ignore[override]
        return []

    def get_tool_spec(self) -> ToolSpec:  # type: ignore[override]
        # NOTE: BaseTool.get_tool_spec is a @classmethod that builds from
        # SchemaGenerator. We override at the *instance* level so we can
        # return the cached upstream schema — there's no class-level data
        # to derive it from.
        return ToolSpec(
            name=self._stk_namespaced_name,
            description=self._stk_description,
            input_schema=self._stk_input_schema,
        )

    def get_input_schema(self) -> Dict[str, Any]:  # type: ignore[override]
        return self._stk_input_schema

    def execute(
        self,
        stream_callback: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> str:
        """Forward the call to the child subprocess, return its result."""
        # We deliberately skip BaseTool.execute's validation/state-save
        # plumbing. The real tool — running inside the child subprocess —
        # already runs that machinery on its own instance. Doing it twice
        # would double the failure modes and wouldn't add safety.
        return self._stk_forward(self._stk_upstream_name, kwargs)

    def _run(self):  # type: ignore[override]
        # Defensive: should never be called because we override execute, but
        # ``BaseTool._run`` is abstract and pydantic complains if it's not
        # implemented somewhere in the MRO.
        raise NotImplementedError("ProxyTool routes through execute(), not _run()")
