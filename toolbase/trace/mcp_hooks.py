"""MCP plumbing for the trace: send a call's trace context in the request
``_meta``, read it in the toolkit host, read the client's ``initialize``
info in ``toolbase serve``. Used only when tracing is on."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from .core import META_KEY, _CURRENT


def call_with_meta(client: Any, name: str, arguments: Dict[str, Any],
                   meta: Dict[str, Any]) -> Tuple[str, Optional[bool]]:
    """``client.call_tool`` with ``meta`` attached to the request.

    Orchestral's ``MCPClient.call_tool`` takes no ``_meta``, so this replays
    its persistent-session path (same loop, same timeout, same
    subprocess-death conversion) with ``meta=``. Returns ``(text, is_error)``
    where ``text`` is exactly what ``call_tool`` would have returned. Falls
    back to plain ``call_tool`` (``is_error=None``) when the client's
    internals are not the expected shape.
    """
    session = getattr(client, "_session", None)
    loop = getattr(client, "_event_loop", None)
    if (session is None or loop is None or getattr(client, "_subprocess_died", False)
            or not hasattr(client, "_is_subprocess_death")):
        return client.call_tool(name, arguments), None
    coro = None
    try:
        from orchestral.mcp.client import (
            MCPSubprocessDiedError, _extract_text_from_result,
        )
        coro = session.call_tool(name, arguments, meta=meta)
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception:
        # Nothing was sent: unexpected client shape. Plain call instead.
        if asyncio.iscoroutine(coro):
            coro.close()
        return client.call_tool(name, arguments), None
    try:
        result = future.result(timeout=client.timeout)
    except Exception as e:
        if (getattr(client, "_transport_type", "stdio") == "stdio"
                and client._is_subprocess_death(e)):
            client._subprocess_died = True
            client._connected = False
            raise MCPSubprocessDiedError(
                "MCP subprocess exited unexpectedly during call_tool. "
                "Reconnect to continue."
            ) from e
        raise
    is_error = bool(getattr(result, "isError", False)
                    or getattr(result, "is_error", False))
    return _extract_text_from_result(result), is_error


def _meta_of(ctx: Any, params: Any) -> Optional[Dict[str, Any]]:
    for src in (getattr(params, "meta", None), getattr(ctx, "meta", None)):
        if isinstance(src, dict):
            info = src.get(META_KEY)
            if isinstance(info, dict) and info.get("call_id"):
                return info
    return None


def host_server_class(base: type) -> type:
    """Subclass of orchestral's ``MCPServer`` whose tools see the request's
    trace context through ``toolbase.trace.current()``. Returns ``base``
    unchanged if it has no ``_on_call_tool`` hook (older orchestral)."""
    if not hasattr(base, "_on_call_tool"):
        return base

    class TracedHostServer(base):  # type: ignore[misc, valid-type]
        async def _on_call_tool(self, ctx, params):
            try:
                info = _meta_of(ctx, params)
            except Exception:
                info = None
            if info is None:
                return await super()._on_call_tool(ctx, params)
            token = _CURRENT.set(dict(info))
            try:
                return await super()._on_call_tool(ctx, params)
            finally:
                _CURRENT.reset(token)

    return TracedHostServer


def client_info_of(ctx: Any) -> Optional[Dict[str, Any]]:
    """``{"name", "version"}`` from the MCP client's ``initialize``, if any."""
    info = None
    try:
        params = ctx.session.client_params
        info = getattr(params, "client_info", None) or getattr(params, "clientInfo", None)
    except Exception:
        info = None
    if info is None:
        meta = getattr(ctx, "meta", None)
        if isinstance(meta, dict):
            info = meta.get("io.modelcontextprotocol/clientInfo")
    if info is None:
        return None
    if isinstance(info, dict):
        return {"name": info.get("name"), "version": info.get("version")}
    return {"name": getattr(info, "name", None),
            "version": getattr(info, "version", None)}


def serve_server_class(base: type,
                       on_first_call: Callable[[Optional[Dict[str, Any]]], None]) -> type:
    """Subclass of ``MCPServer`` that calls ``on_first_call(client_info)``
    once, before the first tool call runs. Errors are swallowed."""
    if not hasattr(base, "_on_call_tool"):
        return base
    done = threading.Event()
    lock = threading.Lock()

    class TracedServeServer(base):  # type: ignore[misc, valid-type]
        async def _on_call_tool(self, ctx, params):
            if not done.is_set():
                with lock:
                    if not done.is_set():
                        try:
                            on_first_call(client_info_of(ctx))
                        except Exception:
                            pass
                        done.set()
            return await super()._on_call_tool(ctx, params)

    return TracedServeServer
