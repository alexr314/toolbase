"""Synthetic scattering-amplitude tools for the ingest e2e fixture.

Mimics the kind of code Tony's HEPTAPOD repo would have at ingest time:
real-world Python module with decorated tools, plus helpers that aren't
tools.
"""

from __future__ import annotations

from orchestral import define_tool


def _validate_kinematics(s, t):
    """Helper, not a tool."""
    if s + t < 0:
        raise ValueError("invalid kinematics")
    return True


@define_tool
def compute_amplitude(s: float, t: float) -> dict:
    """Compute a synthetic scattering amplitude for given kinematics."""
    _validate_kinematics(s, t)
    return {"amplitude": float(s * t), "kinematics": [s, t]}


@define_tool
def cross_section(amplitude_squared: float) -> dict:
    """Convert |M|^2 to a cross section (synthetic)."""
    return {"sigma": float(amplitude_squared) * 0.5}
