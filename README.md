# toolbase

The package manager for AI agent toolkits. Install toolkits into
isolated environments, curate which tools your agent sees, and serve
them to your harness over the [Model Context Protocol](https://modelcontextprotocol.io). Toolkits
span any domain, from web and data utilities to scientific categories
like astro, hep, and quantum.

A **toolkit** is the publishable unit. It bundles **tools** an agent can
call and/or **skills** — short markdown how-to guides surfaced into your
harness. Each toolkit installs into its own isolated Python environment,
so dependency conflicts between toolkits are never a problem. A toolkit
can even ship *only* skills (a **skill pack**).

Full CLI reference: <https://toolbase-ai.com/docs>.

---

## Install toolbase

```bash
pip install toolbase     # Python 3.12+
```

## Arm your agent

The loop is **install → activate → connect**. `tb` is a short alias for
`toolbase`; both ship with the package and behave identically.

```bash
tb install calculator             # download into an isolated environment
tb activate calculator            # expose it to the agent
tb connect claude-code            # write toolbase into Claude Code's MCP config
```

Now launch your harness (e.g. `claude` for Claude Code) — or, in an
already-running session, reconnect the toolbase MCP server. The tools
appear as `calculator__add`, `calculator__multiply`, etc.
`tb install calculator -a` installs and activates in one step.

**Install ≠ activate.** Installing places a toolkit in the global cache
but serves nothing — activation is what exposes it to the agent. The
binary always lives in the shared cache (`~/.toolbase/cache/`); only
the activation is scoped: `tb activate` writes to the current
directory's `.toolbase/` by default, `-g` writes to the user-wide
profile instead.

**`tb connect` writes the MCP config for you.** Claude Code, Codex,
OpenCode, Antigravity, and Orchestral are all supported (`tb connect
--harnesses` lists them); the first four are MCP clients (`tb connect`
edits their config file), while Orchestral gets a runnable agent
script you launch yourself. It also surfaces the activated toolkits'
**skills** into the harness — see
[Skills](#skills-guides-that-travel-with-the-toolkit).

## Inspect

```bash
tb list              # installed toolkits, ✓ active / ✗ inactive
tb list -v           # per-tool view with bundle + config-gating annotations
tb logs              # tool calls, live (best diagnostic for "did it fire?")
```

## Curate what the agent sees

`tb activate` / `deactivate` work at four granularities:

```bash
tb activate calculator                # the whole toolkit
tb activate calculator/scientific     # one bundle (group of related tools)
tb activate calculator__add           # one specific tool
tb deactivate calculator__quickstart  # one specific skill (see Skills below)
```

A `<toolkit>__<name>` item is a **skill** when it matches a surfaced
skill and not a tool; otherwise it's a tool (a name that is both resolves
to the tool). A **bundle** is a self-contained capability an author
carves out of a toolkit, with its own deps and skills.
`tb profile tools calculator` lists what's available. Power users can
keep several named profiles (`tb profile create paper`,
`tb connect claude-code --profile paper`) and switch between them; most
users only ever touch the default profile.

Tools are served namespaced as `<toolkit>__<tool>` by default, so two active
toolkits that both define, say, an `add` tool stay distinct (`calculator__add`
vs `matrix__add`). When names do overlap, `tb serve`, `tb list -v`, and
`tb install` flag it so it's never a surprise. Prefer bare names? `tb serve
--bare` (or `default.bare: true` in `serve.yaml`) advertises the plain `<tool>`;
a name shared by two toolkits stays qualified (both remain callable) with a
warning, and the rest are served bare.

## Skills: guides that travel with the toolkit

A toolkit can ship **skills** — markdown how-to guides in `skills/` —
alongside its tools or on their own. Each is either a file
(`skills/exact_math.md`) or a directory (`skills/exact_math/SKILL.md`) that
carries reference files and scripts beside the guide. `tb connect` surfaces
the activated toolkits' skills into the harness you connect, in that
harness's native format:

- **Claude Code** → `~/.claude/skills/<toolkit>__<skill>/` — auto-surfaced
  to the model *and* available as a `/<toolkit>__<skill>` slash command.
- **Codex** → `~/.codex/prompts/<toolkit>__<skill>.md` — a
  `/<toolkit>__<skill>` slash-command prompt.
- **OpenCode** → `~/.config/opencode/command/<toolkit>__<skill>.md` — a
  `/<toolkit>__<skill>` slash-command prompt (its `description` shows in
  the TUI).
- **Antigravity** → `~/.gemini/config/skills/<toolkit>__<skill>/` — a
  native skill in the global customization root, loaded on demand by the
  `agy` CLI and the IDE alike.

Skills follow the same curation as tools: a guide is surfaced only when
its toolkit is active, and you toggle a single one with the activation
grammar.

```bash
tb deactivate calculator__advanced_guide   # stop surfacing this one guide
tb activate   calculator__advanced_guide   # bring it back
```

`tb connect --no-skills` wires the MCP server without surfacing any
skills; `tb disconnect` removes the surfaced skills too. Surfacing is a
`connect`-time step, so `tb install` alone reports a toolkit's skills but
doesn't surface them — connect (and the set you've activated) decides
what lands in the harness.

A toolkit that ships *only* skills is a **skill pack**: it declares no
tools and serves nothing over MCP, but its guides surface through
`tb connect` exactly like any other toolkit's.

## Share a project without sharing your machine

Toolkits that need configuration (an API key, a path to an external
binary) read it from three layers, later winning key-by-key:

| Layer | File | For |
|---|---|---|
| user | `~/.toolbase/config/<kit>.yaml` | your defaults and secrets, every project |
| project | `<repo>/.toolbase/config/<kit>.yaml` | committed, shared with the team |
| local | `<repo>/.toolbase/config/<kit>.local.yaml` | this project on *this* machine; gitignored |

```bash
tb config set calculator precision 10                  # committed
tb config set calculator solver_path /opt/bin --local  # yours alone
```

Toolkit versions split the same way: `manifest.yaml` is committed so a
collaborator who clones the project and runs `tb install` gets the same
toolkits at the same versions, while `manifest.local.yaml` holds machine-local
pins like editable installs.

---

## Authoring a toolkit

```bash
tb init my-toolkit             # scaffold from template
cd my-toolkit
# write tools in tools/ and skills in skills/
# (a toolkit may ship only skills — a skill pack — and omit tools entirely)
tb validate                    # check structure
tb login                       # one-time browser-flow auth
tb publish                     # ship it (auto-registers on first run)
```

**Iterating locally.** Develop a toolkit's code without a
publish→install round-trip by installing it editable:

```bash
cd my-toolkit
tb install -e . -a             # live symlink to this source dir, and activate
```

Edits to your tool source appear on the next serve; rerun
`tb install -e .` to rebuild the env when dependencies change.

For the full author guide — tool conventions, skills, bundles,
configuration, `setup.py` — see <https://toolbase-ai.com/docs/authoring>.
For the agent-assisted authoring flow (recommended for first toolkits),
see <https://toolbase-ai.com/docs/scaffold-with-an-agent>.

---

## Commands

Full reference with all flags: <https://toolbase-ai.com/docs/reference/commands>.

| Command | Purpose |
|---|---|
| `tb install NAME` | Install a toolkit (`-a` to also activate, `-e <path>` for editable, `NAME[a,b]` for selected bundles) |
| `tb uninstall NAME` | Remove a toolkit |
| `tb list` | Installed toolkits (`-v` for a per-tool view) |
| `tb activate ITEM` | Expose a toolkit / `toolkit/bundle` / `toolkit__tool` / `toolkit__skill` (project-local; `-g` for user-wide) |
| `tb deactivate ITEM` | Hide a toolkit / bundle / tool / skill |
| `tb connect HARNESS` | Wire toolbase into Claude Code, Codex, OpenCode, Antigravity, or scaffold an Orchestral agent script (also surfaces skills; `--no-skills` to skip) |
| `tb disconnect HARNESS` | Remove toolbase from a harness (and its surfaced skills) |
| `tb logs` | Tail the serve log, live |
| `tb profile …` | Manage named profiles: `list \| show \| create \| edit \| delete \| set-default \| path \| tools` |
| `tb config …` | Manage per-toolkit config: `show \| init \| set \| unset \| edit \| path \| validate` (`--user` / `--project` / `--local` pick the layer) |
| `tb setup TOOLKIT` | Run a toolkit's `setup.py` (`--reset`, `--check`) |
| `tb project init` | Create `.toolbase/` here |
| `tb init NAME` | Scaffold a toolkit from template |
| `tb validate` / `tb ingest` | Check toolkit structure / regenerate `toolkit.yaml` from code |
| `tb login` / `tb whoami` / `tb logout` | Registry auth |
| `tb publish` | Package and upload to the registry |

See [CHANGELOG.md](CHANGELOG.md) for the full release history.

---

## Architecture

Three pieces:

- **CLI** (this package) — installed locally, manages toolkit
  environments and serves tools.
- **Backend** ([api.scitoolkit.org](https://api.scitoolkit.org)) —
  registry, auth, tarball storage.
- **Website** ([toolbase-ai.com](https://toolbase-ai.com)) — discover
  and manage published toolkits.

Each installed toolkit runs in its own subprocess in its own Python
environment. `toolbase serve` aggregates them and exposes the union as
a single MCP server upstream; failures in one toolkit don't affect
others.

---

## Contributing

Issues and PRs are welcome at
<https://github.com/alexr314/toolbase>.

## License

MIT. See [LICENSE](LICENSE).

## Links

- Website: <https://toolbase-ai.com>
- Docs: <https://toolbase-ai.com/docs>
- Backend API: <https://api.scitoolkit.org>
- GitHub: <https://github.com/alexr314/toolbase>
- Issues: <https://github.com/alexr314/toolbase/issues>
