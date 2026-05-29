# Files & layout

Where toolbase keeps things. All files are human-editable; the CLI is a
convenience over them.

## User scope: `~/.toolbase/`

```
~/.toolbase/
├── cache/<name>/<version>/   installed toolkit binaries (one slot per version)
├── config/<toolkit>.yaml     per-toolkit config values (user layer)
├── profiles/<name>.yaml      your profiles (curated tool sets)
├── serve.yaml                default.profile + default.disabled
├── logs/serve.log            tool-call log (tb logs)
└── default-project/          fallback project used outside any repo
```

## Project scope: `<repo>/.toolbase/`

```
<repo>/.toolbase/
├── manifest.yaml             pinned toolkits + versions
├── config/<toolkit>.yaml     per-toolkit config values (project layer)
├── profiles/<name>.yaml      project profiles
├── serve.yaml                project default.profile + default.disabled
└── orchestral.py             Orchestral launcher (tb connect orchestral)
<repo>/.mcp.json              Claude Code wiring (tb connect claude-code)
<repo>/.codex/config.toml     Codex wiring (tb connect codex)
```

Project files override user files where they overlap. Commit `.toolbase/`,
`.mcp.json`, and `.codex/config.toml`. Keep secrets in the user-layer
`config/<toolkit>.yaml`.

## Harness wiring

| Harness | Project (`tb connect`) | User (`tb connect -g`) |
|---|---|---|
| Claude Code | `<repo>/.mcp.json` | `~/.claude.json` |
| Codex | `<repo>/.codex/config.toml` | `~/.codex/config.toml` |
| Orchestral | `<repo>/.toolbase/orchestral.py` | none |

Claude Code and Codex are MCP clients (each spawns `tb serve`). Orchestral is a
library: `tb connect orchestral` scaffolds the script, `tb orchestral` runs it.

See [Schemas](schemas.md) for the contents of `serve.yaml`, profiles, and the
author's `toolkit.yaml`.
