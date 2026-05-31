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
when and how to use your tools. Each is a `.md` file in `skills/` with
frontmatter:

```markdown
---
name: Using calculator for exact math
description: When to reach for these tools, with usage tips.
---

# ...guidance for the agent...
```

On `tb install`, each skill is surfaced to
`~/.claude/skills/<toolkit>__<skill>/SKILL.md`, where Claude Code reads it.
`tb uninstall` removes it.

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
