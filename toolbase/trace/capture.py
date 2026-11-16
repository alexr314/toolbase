"""Orchestrator-side capture of one call: the id, the request ``_meta``, the
outcome and the record."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .core import (
    META_KEY, Trace, build_call_record, collect_files, new_call_id, now_iso,
)


class CallCapture:
    """One traced call. ``call`` sends it with the trace context; ``finish``
    writes the record. ``ok`` is true only when the toolkit answered without
    an error (no exception, no MCP ``isError``, and no orchestral
    ``Error: ...`` text); a call that never reached it (toolkit restarting,
    crashed) records ``ok: false`` with the message the agent got."""

    def __init__(self, trace: Trace, *, toolkit: str, tool: str,
                 version: Optional[str], base: Optional[str]):
        self.trace = trace
        self.toolkit, self.tool, self.version = toolkit, tool, version
        self.base = base or (str(trace.base) if trace.base else None)
        self.id = new_call_id()
        self.ts_start = now_iso()
        self.reached = False
        self.is_error: Optional[bool] = None
        self.error: Optional[str] = None

    def meta(self) -> Dict[str, Any]:
        return {META_KEY: {
            "call_id": self.id, "dir": str(self.trace.dir),
            "agent": self.trace.agent, "toolkit": self.toolkit,
            "version": self.version, "base": self.base,
        }}

    def call(self, rt: Any, name: str, kwargs: Dict[str, Any]) -> str:
        from .mcp_hooks import call_with_meta
        text, self.is_error = call_with_meta(rt.mcp_client, name, kwargs, self.meta())
        self.reached = True
        return text

    def finish(self, kwargs: Dict[str, Any], result: Any) -> None:
        ts_end = now_iso()
        text = result if isinstance(result, str) else ("" if result is None else str(result))
        # Orchestral reports a failed tool as text: ``BaseTool.format_error``
        # starts with "Error: ", and an MCP isError result reaches us as
        # "[MCP Error] ...". Both are what the agent sees as a failure.
        failed_text = text.startswith(("Error: ", "[MCP Error]"))
        ok = (self.reached and not self.is_error and self.error is None
              and not failed_text)
        error = self.error
        if not ok and error is None:
            error = text[:2048]
        files = collect_files(kwargs, text, base=self.base,
                              hash_limit=self.trace.hash_limit)
        self.trace.write_call(build_call_record(
            id=self.id, agent=self.trace.agent, toolkit=self.toolkit,
            version=self.version, tool=self.tool, args=kwargs, ok=ok,
            error=error, result=text, ts_start=self.ts_start, ts_end=ts_end,
            files=files, parent=None,
        ))
