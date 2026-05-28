# Create & declare tools

## Scaffold

```bash
tb init my-toolkit            # creates toolkit.yaml, tools/, mcp/, skills/
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

Type hints become the tool's input schema; the docstring is what the agent
sees. Return a JSON string, not a dict.

## Declare tools in `toolkit.yaml`

```yaml
tools:
  - name: add
    function: tools.basic.add      # dotted path to the function
    description: Add two numbers.
    bundle: basic                  # optional
```

Or generate the list from your code and keep it in sync:

```bash
tb ingest            # write/update toolkit.yaml's tools: from tools/
tb ingest --prune    # also drop entries whose source is gone
tb ingest --force    # rebuild from scratch
```

`ingest` preserves your hand-edits (descriptions, `bundle:`) on a re-sync.

## Group tools into bundles

Declare bundles and tag tools with `bundle:`:

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

Tools with no `bundle:` are always served. Gating a bundle on config is in
[Config & setup](config-and-setup.md).

## Next

- [Config & setup](config-and-setup.md) — config schema, gated bundles, setup.py
- [Validate & publish](publish.md)
