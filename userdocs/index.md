# **<span class="tb-tool">tool</span><span class="tb-base">base</span>**

**toolbase is the package manager for AI agent toolkits.** Install
toolkits into isolated environments, curate which tools your agent sees, and
serve them to your harness over the
[Model Context Protocol](https://modelcontextprotocol.io). This site is the
reference for the toolbase CLI: installing, curating, configuring, and
authoring toolkits.

## Install toolbase

```bash
pip install toolbase     # Python 3.12+
```

## Arm your agent

The loop is **install → activate → connect**. `tb` is a short alias for
`toolbase`.

```bash
tb install calculator        # download into an isolated environment
tb activate calculator       # expose it to the agent
tb connect claude-code       # write toolbase into Claude Code's config
```

Restart your agent session. The tools appear as `calculator__<tool>`.
`tb install calculator -a` installs and activates in one step.

## Inspect

```bash
tb list              # installed toolkits, ✓ active / ✗ inactive
tb list -v           # per-tool view
tb logs              # tool calls, live
```

## Next

- [Guides](guides/install-and-activate.md): install, curate, configure, connect
- [Authoring](authoring/overview.md): write and publish a toolkit
- [Reference](reference/commands.md): commands, files, schemas
