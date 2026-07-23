# Start from scratch

Your first toolkit, from the template.

## Scaffold

```bash
tb init my-toolkit                # scaffold a new toolkit
tb init my-toolkit --with-setup   # also add a setup.py (heavier setup)
tb init my-toolkit --with-docker  # also add a Dockerfile
```

This creates `toolkit.yaml`, a `tools/` package, `requirements.txt`, and
`mcp/`, `skills/`, and `README.md`.

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

Type hints become the input schema. The docstring is what the agent sees.

## Export it

Tools are discovered through `tools/__init__.py`. Import and list each one:

```python
# tools/__init__.py
from .basic import add

__all__ = ["add"]
```

A tool that isn't exported here won't be served, even if it appears in
`toolkit.yaml`.

## Declare it

List each tool in `toolkit.yaml`, the manifest the registry and `tb` read
for names, descriptions, and bundle membership:

```yaml
tools:
  - name: add
    function: tools.basic.add      # where the function is defined
    description: Add two numbers.
    bundle: basic                  # optional
```

Don't want to hand-write the list? `tb ingest` generates it from your code
(see [From existing tools](existing-tools.md)).

## Dependencies

Your tools' third-party imports go in `requirements.txt`, alongside the
`orchestral-ai` entry the template ships:

```
sympy>=1.12
```

`tb install` installs them into the toolkit's isolated environment.

## Calling external programs

If a tool shells out to a separate binary, the environment it runs in is the
toolkit's, not the shell you happened to launch the agent from. Two rules
follow, and both are the opposite of what you may expect from testing in an
activated environment:

- **Your toolkit's environment comes first on `PATH`.** A helper script
  starting with `#!/usr/bin/env python3` gets your toolkit's interpreter with
  your declared dependencies, not the system or homebrew one.
- **The calling environment's variables don't reach you.** toolbase is often
  launched from inside an activated conda env or virtualenv, and those export
  variables pointing at their own files (data directories, library roots,
  build flags). Anything bound to the caller's environment is stripped before
  your toolkit runs, so it can't shadow software your toolkit ships. Search
  paths like `PATH` and `LD_LIBRARY_PATH` are kept.

So if your tool needs to know where something lives, declare it — a config
field the user fills, or a `setup.py` that resolves it and records the answer.
Don't rely on a variable your own shell happens to have set: it won't be there
when a harness runs your toolkit. See
[Config & setup](config-and-setup.md).

## Group into bundles

A bundle is a named group of tools. Assign one with `bundle:`:

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

Bundles let users serve a subset of a toolkit, and let you gate a group of
tools (and their skills) on config (see [Config & setup](config-and-setup.md)).

## Next

- [Config & setup](config-and-setup.md): config schema, gated bundles, skills, setup.py
- [Validate & publish](publish.md)
