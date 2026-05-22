"""Class-based BaseTool subclass — the second flavor ingest detects."""

from __future__ import annotations

from orchestral.tools import BaseTool


class TwoPointFunction(BaseTool):
    """Two-point function lookup. Synthetic fixture for ingest e2e."""

    name: str = "two_point_function"
    description: str = "Compute a synthetic two-point correlator value."

    def _run(self, x: float = 0.0) -> dict:
        return {"value": float(x) * 2.0}
