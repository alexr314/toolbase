# Connecting harnesses

`tb connect` wires toolbase into your agent harness so you don't edit its config
by hand. Claude Code and Codex connect as MCP clients; Orchestral is a library
you launch yourself.

```bash
tb connect claude-code
```

Restart your session. The active profile's tools appear as `<toolkit>__<tool>`.
You don't run `tb serve`. The harness launches it.

## Scopes

```bash
tb connect claude-code        # project scope: ./.mcp.json (committed, team-shared)
tb connect claude-code -g     # user scope: ~/.claude.json (all your projects)
```

The default is project-local, so the wiring lives next to your code and travels
with the repo. Pass `-g/--global` to wire it into every session instead.

## The trust prompt

The default write, `<repo>/.mcp.json`, is the file you commit so collaborators
get toolbase wired on clone. Claude Code shows a one-time approval prompt the
first time anyone opens a project with a `.mcp.json`. That's Claude's security
model, and each person approves once.

## Codex

Same model as Claude Code, in Codex's TOML config:

```bash
tb connect codex        # project scope: ./.codex/config.toml
tb connect codex -g     # user scope: ~/.codex/config.toml
```

Codex loads a project's `.codex/config.toml` only after you trust the project,
so run `codex` there and approve it once.

## Orchestral

Orchestral is a library, not an MCP client, so there's no config to write. `tb
connect orchestral` scaffolds a launcher script instead:

```bash
tb connect orchestral   # writes ./.toolbase/orchestral.py
tb orchestral           # run it
```

The script loads your active profile's tools and hands them to an Orchestral
`Agent`; you supply the LLM and its API key. Tools load in-process, so there's
no `tb serve`.

## Activate a profile while connecting

```bash
tb connect claude-code --profile lab
```

Wires the harness and sets `lab` as the active profile in one step. For
Orchestral, `--profile lab` bakes the profile into the script.

## Inspect, choose the binary, remove

```bash
tb connect --list        # where toolbase is wired + the toolbase on your PATH
tb connect --harnesses   # which harnesses are supported
tb connect claude-code --abspath   # write the absolute binary path, not "toolbase"
tb connect claude-code --dry-run   # show the intended write, change nothing
tb disconnect claude-code          # remove (also: tb connect claude-code --remove)
```

Use `--abspath` if you have several toolbase installs and want to pin one. Keep
the bare `toolbase` command in a committed `.mcp.json` so each teammate's
`PATH` resolves their own.

## Next

- [Projects & teams](projects-and-teams.md): committed project setup, reproducible on clone
- [Profiles](profiles-power-user.md): named profiles and `--profile`
