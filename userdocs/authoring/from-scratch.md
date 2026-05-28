# Start from scratch

Your first toolkit, from the template.

## Scaffold

```bash
tb init my-toolkit                # creates toolkit.yaml, tools/, mcp/, skills/
tb init my-toolkit --with-setup   # also add a setup.py (heavier setup)
tb init my-toolkit --with-docker  # also add a Dockerfile
```

## Write a tool

Tools are `@define_tool` functions that return a JSON string:

```python
# tools/basic.py
from orchestral import define_tool
import json

@define_tool
def add(a: float, b: float) -> str:
    """Add two numbers."""
    return json.dumps({"sum": a + b})
```

Type hints become the input schema; the docstring is what the agent sees.

## Declare it

List each tool in `toolkit.yaml`:

```yaml
tools:
  - name: add
    function: tools.basic.add      # dotted path to the function
    description: Add two numbers.
    bundle: basic                  # optional
```

Don't want to hand-write the list? `tb ingest` generates it from your code —
see [From existing tools](existing-tools.md).

## Group into bundles

```yaml
bundles:
  basic: {}
  scientific: {}

tools:
  - name: add
    function: tools.basic.add
    bundle: basic
  - name: sqrt
    function: tools.scientific.sqrt
    bundle: scientific
```

Tools with no `bundle:` are always served.

## Next

- [Config & setup](config-and-setup.md) — config schema, gated bundles, setup.py
- [Validate & publish](publish.md)
