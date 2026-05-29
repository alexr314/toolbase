# Curating tools

What the agent sees is controlled per-bundle and per-tool, not just per-toolkit.
`tb activate` / `tb deactivate` take one argument:

| Form | Scope | Example |
|---|---|---|
| `<toolkit>` | whole toolkit | `tb activate calculator` |
| `<toolkit>/<bundle>` | one bundle | `tb activate calculator/scientific` |
| `<toolkit>__<tool>` | one tool | `tb deactivate calculator__log` |

A **bundle** is a self-contained capability an author carves out of a
toolkit: a coherent group of tools and the skills that go with them, meant to
stand on its own. `calculator` ships three:

```
calculator
├── basic        add, subtract, multiply, divide
├── scientific   power, sqrt, log, sin, cos
└── symbolic     solve, simplify, differentiate   (needs config)
```

## See what's available

```bash
tb profile tools calculator
```

## Narrow it down

```bash
tb activate calculator/basic         # serve only the basic bundle
tb activate calculator/scientific    # add another bundle
tb deactivate calculator__log        # drop one tool
tb deactivate calculator/scientific  # drop a bundle
tb deactivate calculator             # drop the whole toolkit
```

Activating a bundle on a whole-toolkit entry narrows it to that bundle.
`deactivate` only ever removes.

## Check the result

```bash
tb list -v           # every tool, served or hidden, with the reason
tb serve --dry-run   # the set the agent will see
```

```console
✓ calculator  (active)
  - 1.4.0   (used 2 minutes ago, 40 MB)
    ✓ add     [bundle: basic]
    ✗ power   [bundle: scientific]
    ✗ solve   [bundle: symbolic]  (needs config: cas_path)
```

A tool is hidden if its bundle isn't active, you deactivated it, or its bundle
needs config you haven't set ([Configuring toolkits](configuring-toolkits.md)).

## Next

- [Configuring toolkits](configuring-toolkits.md): unlock config-gated bundles
- [Profiles](profiles-power-user.md): save and switch named tool sets
