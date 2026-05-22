"""Tool that echoes its injected state — used by run_setup_e2e.py."""

from __future__ import annotations

import json as _json

from orchestral import define_tool


@define_tool(state=["api_key", "max_workers"])
def get_config(*, api_key: str, max_workers: int) -> str:
    """Return the injected state values as JSON.

    The test reads this back and asserts the values match what it set
    via ``toolbase config set ...`` before serve started.
    """
    return _json.dumps({"api_key": api_key, "max_workers": max_workers})


TOOLS = [get_config]
