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
└── serve.yaml                project default.profile + default.disabled
<repo>/.mcp.json              client wiring (written by tb connect -l)
```

Project files override user files where they overlap. Commit `.toolbase/`
and `.mcp.json`; keep secrets in the user-layer `config/<toolkit>.yaml`.

## Client config

| Scope | File |
|---|---|
| User (`tb connect`) | `~/.claude.json` |
| Project (`tb connect -l`) | `<repo>/.mcp.json` |

See [Schemas](schemas.md) for the contents of `serve.yaml`, profiles, and the
author's `toolkit.yaml`.
