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

## Gate a bundle on config

A bundle can require config keys; its tools stay hidden until they're set:

```yaml
bundles:
  symbolic:
    requires: [cas_path]   # keys must exist in config:
```

Use this for optional, heavyweight capability that needs a prerequisite —
users without it just don't see those tools.

## Heavier setup (`setup.py`)

When config values aren't enough — downloads, derived files, environment
probing — ship a `setup.py` and declare it:

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

Markdown files in `skills/` are surfaced to the agent (Claude Code reads
`~/.claude/skills/`) when the toolkit is installed. Drop guidance there to
teach the agent how to use your tools well.

## Next

- [Validate & publish](publish.md)
