"""Test toolkit tools — return injected state for verification."""

from __future__ import annotations

import json as _json

from orchestral import define_tool


@define_tool(state=["api_key", "worker_count", "use_gpu", "data_mode"])
def get_state(*, api_key: str, worker_count: int, use_gpu: bool, data_mode: str) -> str:
    """Return the injected state as JSON. The e2e harness reads this back."""
    return _json.dumps({
        "api_key": api_key,
        "worker_count": worker_count,
        "use_gpu": use_gpu,
        "data_mode": data_mode,
    })


TOOLS = [get_state]
