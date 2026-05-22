"""Synthetic ASTER tools — return a manifest derived from injected state.

The harness reads the result back as JSON and asserts:

- `api_key` (Tier-1 declared) is injected.
- `workspace` (Tier-1 declared) is injected.
- `opacity_path` (Tier-2 derived via ctx.set_config) is injected.
- `max_workers` (Tier-1 declared, default-applied) is injected.

This proves the full state-injection pipeline — both schema-declared
and ctx.set_config-derived values — reaches a real running tool.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from orchestral import define_tool


@define_tool(state=["api_key", "workspace", "opacity_path", "max_workers"])
def get_observation(
    star_name: str,
    *,
    api_key: str,
    workspace: str,
    opacity_path: str,
    max_workers: int,
) -> str:
    """Mocked observation packager.

    Real ASTER would query the Exoplanet Archive with `star_name`
    and write outputs into `workspace`, parallelized over
    `max_workers`, using opacity data from `opacity_path`. Here we
    just confirm the injection wiring works.
    """
    payload = {
        "star_name": star_name,
        "api_key_set": bool(api_key),
        "workspace": workspace,
        "opacity_path": opacity_path,
        "manifest_present": (Path(opacity_path) / "manifest.txt").exists(),
        "max_workers": max_workers,
    }
    return _json.dumps(payload)


TOOLS = [get_observation]
