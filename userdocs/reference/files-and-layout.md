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
├── manifest.yaml             pinned toolkits + versions (committed)
├── manifest.local.yaml       machine-local pins, e.g. editable (gitignored)
├── .gitignore                written by tb install -e; ignores the local layer
├── config/<toolkit>.yaml     per-toolkit config values (project layer, committed)
├── config/<toolkit>.local.yaml  machine paths etc. (project-local layer, gitignored)
├── profiles/<name>.yaml      project profiles
├── serve.yaml                project default.profile + default.disabled
└── orchestral.py             Orchestral launcher (tb connect orchestral)
<repo>/.mcp.json              Claude Code wiring (tb connect claude-code)
<repo>/.codex/config.toml     Codex wiring (tb connect codex)
```

Project files override user files where they overlap, and the two
`.local` files override their committed siblings (`manifest.local.yaml`
name-by-name for pins; `config/<toolkit>.local.yaml` key-by-key for
config) — they're the home for state only true on this machine:
editable pins, absolute tool paths.
Commit `.toolbase/`, `.mcp.json`, and `.codex/config.toml` — the local
manifest layer auto-gitignores itself. Keep secrets in the user-layer
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
