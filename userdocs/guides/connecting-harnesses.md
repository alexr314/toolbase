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
active loadout's tools appear as `<toolkit>__<tool>`.

**Scopes.** The default is project-local (committed, team-shared). `-u/--user`
wires it into every session instead:

```bash
tb connect claude-code        # project: ./.mcp.json (default)
tb connect claude-code -u     # user: ~/.claude.json (every session)
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
(`./.codex/config.toml`, or `~/.codex/config.toml` with `-u`):

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
tb connect orchestral   # writes ./.toolbase/agent.py
tb orchestral           # run it
```

The generated script (safe to edit) is roughly:

```python
"""Launch an orchestral agent wired with your toolbase tools."""

from pathlib import Path

from orchestral import Agent
from orchestral.llm import Claude   # swap for GPT, Gemini, ...
from orchestral.tools import ReadFileTool, WriteFileTool, RunPythonTool

from toolbase.connect.orchestral import toolbase_tools

SANDBOX = Path(__file__).resolve().parent.parent / "sandbox"

def main():
    SANDBOX.mkdir(parents=True, exist_ok=True)
    # One subprocess per served toolkit, all scoped to SANDBOX.
    with toolbase_tools(
        config_overrides={"base_directory": str(SANDBOX)},
    ) as served:
        agent = Agent(llm=Claude(), tools=[
            ReadFileTool(base_directory=str(SANDBOX)),
            WriteFileTool(base_directory=str(SANDBOX)),
            RunPythonTool(base_directory=str(SANDBOX)),
            *served,
        ])
        from orchestral.ui import run_interactive_session
        run_interactive_session(agent, streaming=True)

if __name__ == "__main__":
    main()
```

It loads your active loadout's tools and hands them to an Orchestral `Agent`.
You supply the LLM and its API key. Tools load in-process, so there's no
`tb serve`. The scaffold also ships commented-out headless and web-GUI launch
modes.

Two details the scaffold gets right, worth keeping if you rewrite it:

- **One sandbox.** `config_overrides` points every served toolkit at
  `SANDBOX`, and the file tools take the same root. Otherwise the toolkits
  write wherever `~/.toolbase/config/<toolkit>.yaml` says and the agent can't
  read back its own output.
- **File tools.** Served toolkits are domain tools; without read/write/run
  tools the agent has no way to open the files they produce.

> Don't name this script `orchestral.py`. Python puts its directory at the head
> of `sys.path`, so that name shadows the `orchestral` package it imports and
> the run dies on `from orchestral import Agent`. Scaffolds from toolbase 0.8.1
> and earlier used that name; re-run `tb connect orchestral` to migrate.

`toolbase_tools()` takes keyword-only arguments, all optional:

| Argument | Default | Effect |
|---|---|---|
| `loadout` | active loadout | Serve a named loadout, like `tb serve --loadout` |
| `project_root` | discovered from the cwd | Project whose `.toolbase/` config applies; `str` or `Path` |
| `call_timeout_s` | `60` | Per-call upper bound |
| `quiet` | `False` | Suppress the startup banner (it prints to stderr) |
| `config_overrides` | none | Config keys merged over every served toolkit |

Pass `project_root` when the script runs from somewhere other than the project
directory — otherwise resolution follows the same chain `tb serve` uses.

Keys in `config_overrides` behave exactly as if they were in the toolkit's
config file: they satisfy required fields and unlock bundles whose `requires:`
names them. Keys a toolkit doesn't declare are ignored, so one
`{"base_directory": ...}` can scope every served toolkit at once.

## Common operations

Set the active loadout while connecting:

```bash
tb connect claude-code --loadout analysis
```

Wires the harness and sets `analysis` as the active loadout in one step. For
Orchestral, `--loadout analysis` bakes the loadout into the script.

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
project by default, `-u` from the user config, and `--all` from both at once.

```bash
tb disconnect claude-code          # this project's .mcp.json
tb disconnect claude-code -u       # user ~/.claude.json (every session)
tb disconnect claude-code --all    # both at once
```

(`tb connect claude-code --remove` is the equivalent of the project form.)

**Pinning the binary.** The wired command has to launch from whatever `PATH`
your harness inherits — and `PATH` is per-shell state, so activating an env,
sourcing an rc file, or opening a new tab can change it between connecting and
launching. `tb connect` therefore writes the **absolute** binary path by
default, in every scope and for every installation. That path launches
regardless of which shell, or GUI app, starts the harness.

Use `--portable` to force the bare command — right for a *committed* config,
where each teammate's `PATH` should resolve their own install. `--abspath`
forces the absolute path. This choice is independent of scope: pinning the
binary doesn't change which sessions get the tools.

## How it fits together

For the curious, here's what happens at runtime:

1. **You wire it once.** `tb activate` writes the curated set to the project's
   `.toolbase/loadouts/<name>.yaml` (created in your cwd if there's none above),
   and `tb connect` writes the harness config.
2. **The harness starts** and reads that config, launching `toolbase serve` as
   a stdio MCP server.
3. **`serve` loads the active loadout** and exposes its tools, spawning one
   subprocess per toolkit. By default that's the `default` loadout `tb activate`
   filled.
4. **The harness sees the tools** as `<toolkit>__<tool>`.

### Overriding the default with serve.yaml

`serve.yaml` is the easy way to override which loadout `serve` runs, and to
blocklist tools across every loadout, without editing a loadout file. A command
writes it for you when you:

- **serve a loadout other than `default`.** The harness launches plain
  `tb serve` (no `--loadout`), so to make it serve a named loadout you record
  the choice: `tb loadout set-default analysis` (or
  `tb connect --loadout analysis`) writes `default.loadout: analysis`.
- **blocklist a tool everywhere.** `tb serve disable-tool calculator__log`
  hides it no matter which loadout is active.
- **commit a team default.** Being a project file, it carries the
  active-loadout choice and blocklist to collaborators on clone.

The file is small and human-editable:

```yaml
# <repo>/.toolbase/serve.yaml
default:
  loadout: analysis        # which loadout serve exposes
  disabled:
    tools:
      - calculator__log     # hidden everywhere, even if the loadout includes it
```

See [Loadouts](loadouts-power-user.md) for the full active-loadout resolution
order.

## Next

- [Projects & teams](projects-and-teams.md): committed project setup, reproducible on clone
- [Loadouts](loadouts-power-user.md): named loadouts and `--loadout`
