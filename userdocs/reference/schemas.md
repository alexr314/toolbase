# Schemas

The YAML files toolbase reads and writes. User files (`serve.yaml`, profiles)
are usually managed by the CLI; the author file (`toolkit.yaml`) you write by
hand.

## `serve.yaml`

User: `~/.toolbase/serve.yaml`. Project: `<repo>/.toolbase/serve.yaml`.

```yaml
default:
  profile: paper            # the active profile
  disabled:                 # absolute blocklist, on top of any profile
    toolkits: [legacy]      # never serve these toolkits
    tools: [calc__noisy]    # never serve these tools (qualified)
```

## Profile

One file per profile: `<scope>/.toolbase/profiles/<name>.yaml`.

```yaml
toolkits:
  calculator:
    bundles: [basic, scientific]   # allowlist by bundle
    tools:
      enabled: [factorial]         # additive per-tool allowlist
      disabled: [log]              # subtracted last
  units: {}                        # whole toolkit (no curation)
```

Rules: a toolkit with neither `bundles` nor `tools.enabled` serves whole; set
either to switch to an allowlist (their union); `tools.disabled` always
subtracts.

## `toolkit.yaml` (authors)

```yaml
name: calculator
version: 1.4.0
description: A small calculator toolkit.
author: Ada Lovelace
# optional: email, license (default MIT), homepage, category, keywords,
# python_version (default 3.11), expected_toolkits

config:                      # optional — values users fill in
  - name: cas_path
    type: path               # string | secret | path | integer | float | boolean | choice
    description: Path to a computer-algebra backend.
    required: true
    # default: ...           # optional
    # options: [...]         # choice only (>= 2)

bundles:                     # optional — named groups; requires gates a bundle
  basic: {}
  scientific: {}
  symbolic:
    requires: [cas_path]     # hidden until this config key is set

tools:
  - name: add
    function: tools.basic.add        # dotted path to the @define_tool function
    description: Add two numbers.
    bundle: basic
  - name: solve
    function: tools.symbolic.solve
    bundle: symbolic

setup_script: true           # optional — set when shipping a setup.py
```

A tool's `bundle` must name a declared bundle; a bundle's `requires` keys must
exist in `config`. `tb ingest` generates this `tools:` list from your code.
See [Authoring](../authoring/overview.md).
