# toolbase

**toolbase is the package manager and runtime for AI agent tools.** Install
toolkits into isolated environments, curate exactly which tools your agent
sees, and wire them into your client with one command.

This site is the setup, installation, and configuration guide. If you just
want to get a tool in front of your agent in two minutes, start with
[Getting started](getting-started.md).

---

## The 60-second tour

```bash
pip install toolbase
tb install arxiv-search        # download into an isolated env (serves nothing yet)
tb activate arxiv-search       # expose it to the agent
tb connect claude-code         # write the MCP entry into Claude Code's config
# restart your agent session — the tools are there
```

`tb` is a short alias for `toolbase`; both ship with the package and behave
identically.

---

## Three nouns, and they don't overlap

Almost everything in this guide is built from three concepts. Learn these and
the rest follows.

<div class="grid cards" markdown>

- :material-package-variant: **toolkit**

    The unit you install. One repo, one isolated Python environment,
    published by an author. `heptapod` is a toolkit; `arxiv-search` is a
    toolkit.

- :material-puzzle: **bundle**

    A coherent group of tools *inside* a toolkit, declared by its author —
    e.g. heptapod's `pythia` bundle. You serve a bundle without taking the
    whole toolkit.

- :material-tune: **profile**

    *Your* curated set of tools, assembled across installed toolkits. The
    active profile is what `tb serve` exposes. Most people only ever use the
    default profile, edited via `tb activate` / `tb deactivate`.

</div>

A fourth word you'll see is **tool** — a single callable the agent invokes
(`arxiv_search__search`). Tools live in bundles; bundles live in toolkits;
toolkits are curated into profiles.

---

## The one rule that surprises people

**Installing a toolkit does not serve it.** Installing places the toolkit in
your cache; nothing reaches the agent until you `tb activate` it. This is the
same split conda draws between *installing* a package and *activating* an
environment — and it exists for the same reasons: no surprise changes to what
your agent sees, and reproducible, explicit curation.

If that trips you up, [How it works → Nothing-active](explanation.md) explains
the model in full. The short version: `install` → `activate` → `connect`.

---

## Where to go next

<div class="grid cards" markdown>

- **New here?** → [Getting started](getting-started.md)

    The golden path, end to end, with the mental model.

- **Day-to-day tasks** → [Guides](guides/install-and-activate.md)

    Find, install, activate, curate, configure, connect.

- **Teams & reproducibility** → [Projects & teams](guides/projects-and-teams.md)

    Pin toolkits and curation into a repo your collaborators can clone.

- **Power user** → [Profiles](guides/profiles-power-user.md)

    Named profiles, switching, hand-editing, layering.

- **Writing a toolkit** → [Authoring](authoring/overview.md)

    Create, declare tools and bundles, configure, validate, publish.

- **Look something up** → [Reference](reference/commands.md)

    Commands, files, vocabulary, scopes, schemas.

</div>

---

## How toolbase fits with your agent

toolbase doesn't replace your agent client — it feeds it. You keep using
Claude Code (or any MCP client); toolbase manages the tools and exposes the
active profile over the [Model Context Protocol](https://modelcontextprotocol.io).
Your client spawns `toolbase serve` and discovers the tools; you never run
`tb serve` by hand. See [Connecting clients](guides/connecting-clients.md).
