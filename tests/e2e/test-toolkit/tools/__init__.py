"""Tools for the synthetic test toolkit."""

from orchestral import define_tool


@define_tool
def hello(name: str = "world") -> str:
    """Return a greeting."""
    import json as _json
    return _json.dumps({"greeting": f"hello, {name}"})


@define_tool
def add(a: float, b: float) -> str:
    """Return the sum of two numbers as JSON."""
    import json as _json
    return _json.dumps({"sum": a + b})


TOOLS = [hello, add]
