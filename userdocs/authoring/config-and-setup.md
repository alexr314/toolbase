# Config & setup

## Declare config

Add a `config:` block to `toolkit.yaml` for values the user supplies:

```yaml
config:
  - name: api_key
    type: secret
    description: Your API key.
    required: true
  - name: precision
    type: integer
    default: 6
  - name: angle_unit
    type: choice
    options: [radians, degrees]
    default: radians
```

Field types: `string`, `secret`, `path`, `integer`, `float`, `boolean`,
`choice` (`choice` needs `options`, ≥2). Optional per field: `required`,
`default`, `description`. Required fields the user hasn't filled cause `serve`
to skip the toolkit with a clear pointer.

Values land in `~/.toolbase/config/<toolkit>.yaml` (user) and the project
layer. From the consumer side: [Configuring toolkits](../guides/configuring-toolkits.md).

### Workspace-aware defaults

For `path` and `string` fields, `default:` can reference two template
variables that the orchestrator expands at serve time:

| Template          | Expands to                                                  |
|-------------------|-------------------------------------------------------------|
| `${CWD}`          | `os.getcwd()` in the orchestrator — the directory the harness launched `tb serve` from. |
| `${PROJECT_ROOT}` | The discovered `.toolbase/` parent (`find_project_root`), or `${CWD}` if there is none. |

```yaml
config:
  - name: workspace_dir          # field name is your choice
    type: path
    required: true
    default: ${CWD}
    description: Working directory for tool I/O.
```

Composition with a suffix works: `${CWD}/scratch`,
`${PROJECT_ROOT}/outputs`. Unknown templates (`${BANANA}`) are rejected
at schema parse time and fail `tb validate`. Allowed types are `path`
and `string` only.

User-stored values override the template; project layer beats user layer.

## Gate a bundle on config

A bundle can require config keys. Its tools stay hidden until they're set:

```yaml
bundles:
  symbolic:
    requires: [cas_path]   # keys must exist in config:
```

Use this for optional, heavyweight capability that needs a prerequisite.
Users without it just don't see those tools.

## Tools in multiple bundles

A tool's `bundle:` field accepts either a single name (`bundle: basic`)
or a list (`bundle: [basic, symbolic]`):

```yaml
tools:
  - name: simplify
    function: tools.symbolic.simplify
    bundle: [basic, symbolic]    # belongs to both bundles
```

A multi-bundle tool is served if **any** of its bundles is available
(config-gating satisfied for at least one), and it counts as in-loadout
if any of its bundles is in the loadout's allowlist. Use this for tools
that genuinely belong in more than one logical grouping — e.g. the
calculator's `simplify` tool above is useful both as a basic operation
and as part of the symbolic workflow.

## Per-bundle dependencies

A bundle can declare pip packages that should be installed when the user
selects this bundle (rather than the whole toolkit). Use this when a
bundle relies on heavy or specialised dependencies the rest of the
toolkit doesn't need:

```yaml
bundles:
  basic: {}                       # base only — no extra deps
  scientific:
    deps: [numpy>=2.0, pandas]    # pip-installed if 'scientific' is selected
  symbolic:
    requires: [cas_path]
    deps: [sympy>=1.14]           # `requires:` and `deps:` can combine
```

The toolkit's `requirements.txt` is always installed (the base). Bundle
`deps:` add on top when the user picks that bundle at install time. A
user installing only `basic` skips numpy and sympy entirely. Use this
to keep small-subset installs lean.

## Heavier setup (`setup.py`)

When config values aren't enough (downloads, derived files, environment
probing), ship a `setup.py` and declare it:

```yaml
setup_script: true
```

```bash
tb init my-toolkit --with-setup   # scaffolds the setup.py
```

Users run `tb setup <toolkit>` (also `--check`, `--reset`). A toolkit that
declares `setup_script` but hasn't had setup run is skipped at serve with a
clear message.

## Skills

A skill is an agent-facing how-to guide: markdown that teaches the model
when and how to use your tools. Ship one in either shape:

```
skills/exact_math.md          # a file — the guide is the whole skill
skills/exact_math/            # a directory, for guides with attachments
    SKILL.md
    references/identities.md
    scripts/check_precision.py
```

Reach for the directory when the guide needs supporting files — reference
tables, example scripts, sample data. They travel with the skill when it's
surfaced, so relative links like `references/identities.md` keep resolving.
A directory without a `SKILL.md` isn't a skill; `tb validate` warns that it
ships unsurfaced.

Either shape carries frontmatter at the top of the guide:

```markdown
---
name: Using calculator for exact math
description: When to reach for these tools, with usage tips.
---

# ...guidance for the agent...
```

`tb connect <harness>` surfaces the activated toolkits' skills in that
harness's own format; `tb disconnect` removes them. Harnesses that take a
skill as a directory (Claude Code, Antigravity) get the attachments too;
those that take a single prompt file (Codex, OpenCode) get the guide alone.

### Scope a skill to a bundle

Add `bundle:` to a skill's frontmatter to tie it to a bundle. The skill is
surfaced only when that bundle is available, the same config gating that
governs the bundle's tools:

```markdown
---
name: Using the symbolic tools
description: How and when to reach for symbolic algebra.
bundle: symbolic
---
```

With `symbolic` gated on `cas_path` (above), this guide appears only once
the user sets that key. A skill with no `bundle:` is toolkit-wide and
always surfaced.

## Next

- [Validate & publish](publish.md)
