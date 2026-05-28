# toolbase

The package manager and runtime for AI agent tools. Publish toolkits to
the [Toolbase registry](https://toolbase-ai.com) and use them in coding
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

# Expose it to the agent (installing alone serves nothing)
tb activate arxiv-search

# Wire toolbase into Claude Code (writes its MCP config for you)
tb connect claude-code

# See what's installed and what's active
tb list
```

`tb` is a shorter alias for `toolbase`; both ship with the package
and behave identically.

**Install, then activate.** Installing places a toolkit in the cache but
does not serve it — nothing reaches the agent until you `tb activate` it
(conda-style: install ≠ activate). `tb install -a arxiv-search` does both
in one step. Use `tb deactivate` to hide a toolkit, bundle, or tool again.

Installs are global by default (`-g`). Use `-l` to pin a toolkit into
the current project's `.toolbase/manifest.yaml` instead — the binary
still lives in the shared global cache, only the pin is project-scoped,
so a collaborator who clones the project and runs `tb install` (no
args) gets the same toolkits at the same versions.

**`tb connect` replaces hand-editing MCP config.** It writes the
`toolbase` server entry into [Claude Code](https://claude.ai/code)'s
config (`~/.claude.json` for user scope, `.mcp.json` for `-l` project
scope). Claude Code then spawns its own `toolbase serve` subprocess and
discovers the active profile's tools. To watch tool calls fire in real
time, run `tb logs` in another terminal. (If you'd rather wire it by
hand, the entry is `{"mcpServers": {"toolbase": {"command": "toolbase",
"args": ["serve"]}}}`.)

### Curating what the agent sees

`tb activate` / `tb deactivate` edit the active **profile** — a named
curated set of tools. Three granularities:

```bash
tb activate heptapod                 # the whole toolkit
tb activate heptapod/pythia          # one bundle (a coherent group of tools)
tb activate heptapod__run_pythia     # one specific tool
```

Most users only ever touch the default profile via these commands. Power
users can keep several named profiles (`tb profile create paper`,
`tb connect claude-code --profile paper`) and switch between them. Run
`tb profile tools` to see the bundles and tools a toolkit offers.

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
tb install -e . -a              # live symlink to this source dir, and activate it
# edit tools/, restart your agent session — edits are live
```

An editable install symlinks your source into the cache and builds the
environment there (your source tree stays clean — no `.venv` written
into it). Edits to your tool source appear on the next serve. If you
change dependencies, re-run `tb install -e .` to rebuild the env.

For the agent-assisted authoring flow (recommended for first toolkits),
see <https://toolbase-ai.com/docs/scaffold-with-an-agent>.

For the full author guide — toolkit layout, tool conventions, skills,
bundles, expected_toolkits, configuration — see
<https://toolbase-ai.com/docs/authoring> and
<https://toolbase-ai.com/docs/configuration>.

---

## What's in toolbase

**Commands:**

- `init`, `create`, `ingest`, `validate`, `login`, `whoami`, `logout`,
  `publish` — author and ship toolkits.
- `search`, `install`, `uninstall`, `list` — manage installed toolkits.
  `install` takes `-g` (global, the default), `-l` (pin into this
  project), `-e <path>` (editable, live symlink to a local source), or
  `-a` (also activate). `list` takes `-v` for a per-tool served/hidden
  view.
- `activate` / `deactivate` — expose or hide a toolkit, bundle
  (`toolkit/bundle`), or tool (`toolkit__tool`) in the active profile.
- `connect <client>` / `disconnect <client>` — write (or remove)
  toolbase in an agent client's MCP config (`claude-code` in v1).
  `--list` shows where it's wired; `--clients` lists supported targets.
- `serve` — run the active profile's tools as an MCP stdio server
  (`--profile`, `--dry-run`, `--call-timeout`). Normally spawned by the
  client, not run by hand.
- `profile <list|show|create|edit|delete|set-default|path|tools>` —
  manage named profiles (curated tool sets).
- `setup <toolkit>` — run a toolkit's `setup.py` (`--reset`, `--check`).
- `config <show|edit|path|set|unset|validate>` — manage per-toolkit
  config files at `~/.toolbase/config/<toolkit>.yaml`.
- `logs` — tail the serve log with Rich coloring.

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
- **Profiles:** curated tool sets assembled across toolkits at
  toolkit / bundle / tool granularity, stored one-file-per-profile under
  `.toolbase/profiles/`. Activate with `tb activate`; the active profile
  is chosen by `default.profile` in `~/.toolbase/serve.yaml` (or a
  project-level override).
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
- **Website** ([toolbase-ai.com](https://toolbase-ai.com)) — discover and
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

- Website: <https://toolbase-ai.com>
- Backend API: <https://api.scitoolkit.org>
- GitHub: <https://github.com/alexr314/toolbase>
- Issues: <https://github.com/alexr314/toolbase/issues>
