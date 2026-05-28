# toolbase

**toolbase is the package manager and runtime for AI agent tools.** Install
toolkits into isolated environments, curate which tools your agent sees, and
serve them to your client over the
[Model Context Protocol](https://modelcontextprotocol.io).

This site is the setup, installation, and configuration reference. `tb` is a
short alias for `toolbase`.

## Install toolbase

```bash
pip install toolbase     # Python 3.12+
```

## Get a tool in front of your agent

The loop is **install → activate → connect**.

```bash
tb install arxiv-search      # download into an isolated environment
tb activate arxiv-search     # expose it to the agent
tb connect claude-code       # write toolbase into Claude Code's config
```

Restart your agent session; the tools appear as `arxiv-search__<tool>`.
`tb install arxiv-search -a` installs and activates in one step.

## Install ≠ serve

Installing puts a toolkit in the cache; it serves nothing until you activate
it. A toolkit is in one of three states:

| State | Put it there with |
|---|---|
| Installed (in the cache) | `tb install` |
| Active (served to the agent) | `tb activate` |
| Wired (in your client's config) | `tb connect` |

It must be all three for the agent to use it. You don't run `tb serve`
yourself — the client spawns it.

## Inspect

```bash
tb list              # installed toolkits, ✓ active / ✗ inactive
tb list -v           # per-tool view
tb serve --dry-run   # exactly what the agent will see
tb logs              # tool calls, live
```

## Next

- [Guides](guides/install-and-activate.md) — install, curate, configure, connect
- [Authoring](authoring/overview.md) — write and publish a toolkit
- [Reference](reference/commands.md) — commands, files, schemas
