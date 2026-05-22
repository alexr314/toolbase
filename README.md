# toolbase

The package manager and runtime for AI agent tools. Publish toolkits to
the [Toolbase registry](https://tool-base.org) and use them in coding
agents (Claude Code, Codex) or any client that speaks the
[Model Context Protocol](https://modelcontextprotocol.io). Toolkits span
any domain, from web and data utilities to scientific categories like
astro, hep, and quantum.

A **toolkit** is the publishable unit; it bundles one or more **tools**
an agent can call. Each toolkit installs into its own isolated Python
environment, so dependency conflicts between toolkits are never a
problem.

---

## Quickstart

```bash
pip install toolbase

# Install a toolkit from the registry (global by default)
tb install arxiv-search

# Or scope an install to the current project's manifest
tb install -l arxiv-search

# See what you have
tb list

# Serve installed toolkits over MCP stdio
tb serve
```

`tb` is a shorter alias for `toolbase`; both ship with the package
and behave identically.

Installs are global by default (`-g`). Use `-l` to pin a toolkit into
the current project's `.toolbase/manifest.yaml` instead — the binary
still lives in the shared global cache, only the pin is project-scoped,
so a collaborator who clones the project and runs `tb install` (no
args) gets the same toolkits at the same versions.

To use the served toolkits in [Claude Code](https://claude.ai/code), add
this to its MCP config:

```json
{
  "mcpServers": {
    "toolbase": {
      "command": "toolbase",
      "args": ["serve"]
    }
  }
}
```

Claude Code will spawn its own `toolbase serve` subprocess and
discover all installed toolkits' tools. To watch tool calls fire in
real time, run `tb logs` in another terminal.

---

## Authoring a toolkit

```bash
tb init my-toolkit              # scaffold from template
# tb init my-toolkit --with-setup   # if your toolkit needs a setup.py
cd my-toolkit
# write your tools in tools/ ; write skills in skills/
tb validate                     # check structure
tb login                        # one-time, browser-flow auth (per-user)
tb publish                      # ship it (auto-registers on first run)
```

`tb publish` registers the toolkit on the registry on its first run —
no separate step. If the name isn't registered yet, it prompts you
(using the metadata in `toolkit.yaml`) and registers it before
uploading. `tb create` is still available if you want to reserve a
name without uploading code yet, but it's no longer required.

`tb login` (no toolkit name) does a browser-flow that gives you a
per-user token good for any toolkit you own or collaborate on. Legacy
per-toolkit tokens are still accepted (`tb login my-toolkit`) but
deprecated.

**Iterating locally.** To develop a toolkit's code without a
publish→install round-trip on every change, install it editable:

```bash
cd my-toolkit
tb install -e .                 # live symlink to this source dir
tb serve my-toolkit             # serve it; edit tools/, restart, edits are live
```

An editable install symlinks your source into the cache and builds the
environment there (your source tree stays clean — no `.venv` written
into it). Edits to your tool source appear on the next `tb serve`. If
you change dependencies, re-run `tb install -e .` to rebuild the env.

For the agent-assisted authoring flow (recommended for first toolkits),
see <https://tool-base.org/docs/scaffold-with-an-agent>.

For the full author guide — toolkit layout, tool conventions, skills,
groups, expected_toolkits, configuration — see
<https://tool-base.org/docs/authoring> and
<https://tool-base.org/docs/configuration>.

---

## What's in toolbase

**Commands:**

- `init`, `create`, `ingest`, `validate`, `login`, `whoami`, `logout`,
  `publish` — author and ship toolkits.
- `search`, `install`, `uninstall`, `list` — manage installed toolkits.
  `install` takes `-g` (global, the default), `-l` (pin into this
  project), or `-e <path>` (editable, live symlink to a local source).
- `serve` — run installed toolkits as an MCP stdio server. Supports
  positional toolkit names, `--group`, `--enable-tool`,
  `--disable-tool`, `--dry-run`, `--call-timeout`.
- `setup <toolkit>` — run a toolkit's `setup.py` (`--reset`, `--check`).
- `config <show|edit|path|set|unset|validate>` — manage per-toolkit
  config files at `~/.toolbase/config/<toolkit>.yaml`.
- `logs` — tail the serve log with Rich coloring.
- `groups` — manage named tool subsets that span toolkits.

**Features:**

- **Editable installs.** `tb install -e <path>` symlinks a local
  toolkit source into the cache so `serve` loads tools live — the
  `pip install -e .` parallel for the toolkit dev loop. The env is
  built and cached; only the source is symlinked, so your source tree
  stays clean.
- **Multi-version installs + per-project pinning.** Different versions
  of a toolkit coexist in the global cache; each project pins which
  version it uses in a git-committed `.toolbase/manifest.yaml`. The
  binary lives once in the shared cache (`-g`/`-l` choose the manifest
  scope, not the file location).
- **Configuration system.** Toolkits declare a `config:` block in
  `toolkit.yaml` (seven types: `string`, `secret`, `path`, `integer`,
  `float`, `boolean`, `choice`); users fill it at install time or by
  editing `~/.toolbase/config/<toolkit>.yaml`. Toolkits with more
  involved setup ship a `setup.py` with full prompts, downloads
  (resumable, SHA256-verified, auto-extracting tar/zip with zip-slip
  defense), and derived-state writes via `ctx.set_config(...)`.
- **Per-user auth.** `toolbase login` does a browser-flow that
  stores a per-user token good for any toolkit you own or
  collaborate on. Legacy per-toolkit tokens still work but are
  deprecated.
- **Multi-tier execution:** same-Python toolkits run in venv,
  different-Python toolkits run under conda (auto-detected). Docker
  mode coming in 3B.
- **Per-tool selection:** enable or disable individual tools per
  serve session or persistently in `~/.toolbase/serve.yaml`.
- **Skills surfacing:** a toolkit's `skills/*.md` files are
  auto-mirrored to `~/.claude/skills/` so Claude Code discovers
  them. Symlinked on POSIX for live edits, copied on Windows.
- **Agent-friendly flags:** every state-modifying command supports
  `--yes`, `--no`, `--no-input`. Non-TTY stdin auto-applies
  non-interactive behavior.
- **Versioning safeguards:** `publish` blocks "version already
  exists" and "version decrease" with helpful suggestions before
  upload.
- **Crash resilience:** per-toolkit subprocess auto-restart with
  exponential backoff. A crashed toolkit doesn't take the
  orchestrator down.
- Python 3.12+ required.

See [CHANGELOG.md](CHANGELOG.md) for the full release history.

---

## Architecture

The package has three pieces:

- **CLI** (this package) — installed locally, manages toolkit
  environments and serves tools.
- **Backend** ([api.scitoolkit.org](https://api.scitoolkit.org)) —
  registry, auth, tarball storage.
- **Website** ([tool-base.org](https://tool-base.org)) — discover and
  manage published toolkits.

Each installed toolkit runs in its own subprocess in its own Python
environment. The `toolbase serve` orchestrator aggregates them and
exposes the union as a single MCP server upstream. Failures in one
toolkit don't affect others.

---

## Contributing

Issues and PRs are welcome at
<https://github.com/alexr314/toolbase>.

---

## License

MIT. See [LICENSE](LICENSE).

## Links

- Website: <https://tool-base.org>
- Backend API: <https://api.scitoolkit.org>
- GitHub: <https://github.com/alexr314/toolbase>
- Issues: <https://github.com/alexr314/toolbase/issues>
