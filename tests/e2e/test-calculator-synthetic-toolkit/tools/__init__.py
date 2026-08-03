"""Synthetic calculator tools — return a manifest derived from injected state.

The harness reads the result back as JSON and asserts:

- `api_key` (Tier-1 declared) is injected.
- `workspace` (Tier-1 declared) is injected.
- `constants_path` (Tier-2 derived via ctx.set_config) is injected.
- `max_workers` (Tier-1 declared, default-applied) is injected.

This proves the full state-injection pipeline — both schema-declared
and ctx.set_config-derived values — reaches a real running tool.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from orchestral import define_tool


@define_tool(state=["api_key", "workspace", "constants_path", "max_workers"])
def calculate(
    expression: str,
    *,
    api_key: str,
    workspace: str,
    constants_path: str,
    max_workers: int,
) -> str:
    """Mocked expression evaluator.

    A real toolkit would evaluate `expression` against the constants
    table at `constants_path`, write results into `workspace`, and
    parallelize over `max_workers`. Here we just confirm the injection
    wiring works.
    """
    payload = {
        "expression": expression,
        "api_key_set": bool(api_key),
        "workspace": workspace,
        "constants_path": constants_path,
        "manifest_present": (Path(constants_path) / "manifest.txt").exists(),
        "max_workers": max_workers,
    }
    return _json.dumps(payload)


TOOLS = [calculate]
