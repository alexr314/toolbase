# Connecting clients

`tb connect` writes toolbase into your agent client's MCP config so you don't
hand-edit JSON.

```bash
tb connect claude-code
```

Restart your session; the active profile's tools appear as `<toolkit>__<tool>`.
You don't run `tb serve`. The client launches it.

## Scopes

```bash
tb connect claude-code        # user scope: ~/.claude.json (all your projects)
tb connect claude-code -l     # project scope: ./.mcp.json (committed, team-shared)
```

## Project scope and the trust prompt

`-l` writes `<repo>/.mcp.json`, which you commit so collaborators get toolbase
wired on clone. Claude Code shows a one-time approval prompt the first time
anyone opens a project with a `.mcp.json`. That's Claude's security model,
and each person approves once.

## Activate a profile while connecting

```bash
tb connect claude-code -l --profile lab
```

Wires the client and sets `lab` as the active profile in one step.

## Inspect, choose the binary, remove

```bash
tb connect --list       # where toolbase is wired + the toolbase on your PATH
tb connect --clients    # which clients are supported
tb connect claude-code --abspath   # write the absolute binary path, not "toolbase"
tb connect claude-code --dry-run   # show the intended write, change nothing
tb disconnect claude-code          # remove (also: tb connect claude-code --remove)
```

Use `--abspath` if you have several toolbase installs and want to pin one; keep
the bare `toolbase` command for project scope so each teammate's `PATH`
resolves their own.

## Next

- [Projects & teams](projects-and-teams.md): `-l` wiring + reproducible setup
- [Profiles](profiles-power-user.md): named profiles and `--profile`
