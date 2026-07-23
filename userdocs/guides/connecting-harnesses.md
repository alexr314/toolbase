# Connecting harnesses

`tb connect` wires toolbase into your agent harness so you don't edit its config
by hand. There are two modalities: **MCP harnesses** (Claude Code, Codex), which
read a config file that launches toolbase, and **Orchestral**, a library you
launch yourself.

## MCP harnesses (Claude Code, Codex)

Both connect the same way. `tb connect` writes a config entry that launches
`toolbase serve` as a stdio MCP server, and the harness talks to it over MCP:

```bash
tb connect claude-code        # or: tb connect codex
```

Then launch the harness. `claude` or `codex` starts a session with toolbase's
tools wired in (an already-running session needs a restart to pick them up). The
active profile's tools appear as `<toolkit>__<tool>`.

**Scopes.** The default is project-local (committed, team-shared). `-g/--global`
wires it into every session instead:

```bash
tb connect claude-code        # project: ./.mcp.json (default)
tb connect claude-code -g     # user: ~/.claude.json (every session)
```

The first time a harness opens a project with a committed config, it shows a
one-time approval prompt. That's the harness's own security model, not
toolbase's.

### Claude Code

`tb connect claude-code` writes `.mcp.json` (in your project by default) with a
stdio MCP server entry:

```json
{
  "mcpServers": {
    "toolbase": {
      "type": "stdio",
      "command": "toolbase",
      "args": ["serve"]
    }
  }
}
```

### Codex

`tb connect codex` writes the same entry to Codex's TOML config
(`./.codex/config.toml`, or `~/.codex/config.toml` with `-g`):

```toml
[mcp_servers.toolbase]
command = "toolbase"
args = ["serve"]
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

The generated script (safe to edit) is roughly:

```python
"""Launch an orchestral agent wired with your toolbase tools."""

from toolbase.connect.orchestral import toolbase_tools
from orchestral import Agent
from orchestral.llm import Claude   # swap for GPT, Gemini, ...

def main():
    with toolbase_tools() as tools:        # one subprocess per served toolkit
        agent = Agent(llm=Claude(), tools=tools)
        from orchestral.ui import run_interactive_session
        run_interactive_session(agent, streaming=True)

if __name__ == "__main__":
    main()
```

It loads your active profile's tools and hands them to an Orchestral `Agent`.
You supply the LLM and its API key. Tools load in-process, so there's no
`tb serve`. The scaffold also ships commented-out headless and web-GUI launch
modes.

`toolbase_tools()` takes keyword-only arguments, all optional:

| Argument | Default | Effect |
|---|---|---|
| `profile` | active profile | Serve a named profile, like `tb serve --profile` |
| `project_root` | discovered from the cwd | Project whose `.toolbase/` config applies; `str` or `Path` |
| `call_timeout_s` | `60` | Per-call upper bound |
| `quiet` | `False` | Suppress the startup banner (it prints to stderr) |
| `config_overrides` | none | Config keys merged over every served toolkit |

Pass `project_root` when the script runs from somewhere other than the project
directory — otherwise resolution follows the same chain `tb serve` uses.

## Common operations

Set the active profile while connecting:

```bash
tb connect claude-code --profile analysis
```

Wires the harness and sets `analysis` as the active profile in one step. For
Orchestral, `--profile analysis` bakes the profile into the script.

Inspect, pin the binary, or remove:

```bash
tb connect --list        # where toolbase is wired (user + project) + the toolbase on your PATH
tb connect --harnesses   # which harnesses are supported
tb connect claude-code --abspath   # write the absolute binary path, not "toolbase"
tb connect claude-code --dry-run   # show the intended write, change nothing
```

`tb connect --list` reports every scope it's wired into — the user config and
the project config for each harness — so you can see exactly where an entry
lives before changing it.

**Removing.** `tb disconnect` mirrors connect's scopes: it removes from this
project by default, `-g` from the user config, and `--all` from both at once.

```bash
tb disconnect claude-code          # this project's .mcp.json
tb disconnect claude-code -g       # user ~/.claude.json (every session)
tb disconnect claude-code --all    # both at once
```

(`tb connect claude-code --remove` is the equivalent of the project form.)

**Pinning the binary.** Use `--abspath` when the `toolbase` you want isn't on
the `PATH` your harness inherits — e.g. it lives in a conda env or venv, or the
harness is launched from a GUI (Dock/Spotlight) that doesn't see your shell
`PATH`. It writes the absolute binary path so the harness finds it regardless of
how it's launched. Keep the bare `toolbase` command in a *committed* config so
each teammate's `PATH` resolves their own install.

## How it fits together

For the curious, here's what happens at runtime:

1. **You wire it once.** `tb activate` writes the curated set to the project's
   `.toolbase/profiles/<name>.yaml` (created in your cwd if there's none above),
   and `tb connect` writes the harness config.
2. **The harness starts** and reads that config, launching `toolbase serve` as
   a stdio MCP server.
3. **`serve` loads the active profile** and exposes its tools, spawning one
   subprocess per toolkit. By default that's the `default` profile `tb activate`
   filled.
4. **The harness sees the tools** as `<toolkit>__<tool>`.

### Overriding the default with serve.yaml

`serve.yaml` is the easy way to override which profile `serve` runs, and to
blocklist tools across every profile, without editing a profile file. A command
writes it for you when you:

- **serve a profile other than `default`.** The harness launches plain
  `tb serve` (no `--profile`), so to make it serve a named profile you record
  the choice: `tb profile set-default analysis` (or
  `tb connect --profile analysis`) writes `default.profile: analysis`.
- **blocklist a tool everywhere.** `tb serve disable-tool calculator__log`
  hides it no matter which profile is active.
- **commit a team default.** Being a project file, it carries the
  active-profile choice and blocklist to collaborators on clone.

The file is small and human-editable:

```yaml
# <repo>/.toolbase/serve.yaml
default:
  profile: analysis        # which profile serve exposes
  disabled:
    tools:
      - calculator__log     # hidden everywhere, even if the profile includes it
```

See [Profiles](profiles-power-user.md) for the full active-profile resolution
order.

## Next

- [Projects & teams](projects-and-teams.md): committed project setup, reproducible on clone
- [Profiles](profiles-power-user.md): named profiles and `--profile`
