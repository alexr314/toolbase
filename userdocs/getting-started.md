# Getting started

This walks you from nothing to a working tool in your agent, and explains
what each step actually does. Five minutes, no prior toolbase knowledge
assumed.

## 1. Install toolbase

toolbase is a Python package (3.12+):

```bash
pip install toolbase
```

This gives you two commands — `toolbase` and the short alias `tb`. They're
identical; this guide uses `tb`.

```bash
tb --help        # sanity check
```

!!! tip "Use a virtual environment"
    Install toolbase into a venv or pipx so its dependencies don't collide
    with other projects. Each *toolkit* you install later gets its **own**
    isolated environment regardless — that isolation is toolbase's whole
    point — but toolbase itself is a normal Python package.

## 2. Install a toolkit

A **toolkit** is the installable unit. Grab one from the registry:

```bash
tb install arxiv-search
```

This downloads the toolkit and builds it an isolated environment (a venv, or
a conda env if it needs a different Python — toolbase auto-detects). The
binary lives in a shared cache at `~/.toolbase/cache/<name>/<version>/`.

!!! warning "Installing does not serve it"
    At this point `arxiv-search` is on your machine but your agent still
    can't see it. Installing fills the *cache*; it doesn't expose anything.
    That's the next step.

Check what you have:

```bash
tb list
```

You'll see `arxiv-search` marked **✗ inactive** — installed, but not served.

## 3. Activate it

`tb activate` adds a toolkit to your **profile** — the set of tools your
agent will see:

```bash
tb activate arxiv-search
```

Run `tb list` again and it flips to **✓ active**.

!!! tip "One-step shortcut"
    `tb install arxiv-search -a` installs *and* activates in one go. The
    two-step form above just makes the model obvious the first time.

## 4. Configure it (if it asks)

Some toolkits need values to work — an API key, a path to a local install.
If a toolkit declares configuration, `tb install` prompts you; you can also
set values any time:

```bash
tb config show arxiv-search          # what does it need? what's set?
tb config set arxiv-search email=you@example.com
```

`arxiv-search` works without config, so you can skip this. See
[Configuring toolkits](guides/configuring-toolkits.md) for the full system
(secrets, paths, per-project values).

## 5. Connect your agent client

`tb connect` writes toolbase into your client's MCP config so you don't have
to hand-edit JSON:

```bash
tb connect claude-code
```

That writes a `toolbase` entry into `~/.claude.json`. Restart your Claude
Code session and the active profile's tools appear (named
`arxiv-search__<tool>`).

!!! note "You don't run `tb serve` yourself"
    Your client spawns `toolbase serve` on its own and discovers the tools.
    `tb serve` is the runtime, not something you invoke by hand. To watch
    tool calls live, run `tb logs` in another terminal.

## 6. Verify

```bash
tb list -v            # per-tool view: what's served, what's hidden, and why
tb serve --dry-run    # preview exactly what the agent will see, without serving
```

Ask your agent to do something that needs the tool. Done.

---

## What just happened — the mental model

Three distinct places a toolkit can be, and the commands that move it
between them:

| State | Where | Put it there with |
|---|---|---|
| **Installed** | the cache (`~/.toolbase/cache/`) | `tb install` |
| **Active** | your profile (what serve exposes) | `tb activate` |
| **Wired** | your client's MCP config | `tb connect` |

A toolkit has to be all three for your agent to use it: installed (so the
code exists), active (so serve includes it), and your client wired (so it
spawns serve). `tb install -a` collapses the first two; `tb connect` is a
one-time-per-client step.

To narrow what's served, you don't uninstall — you `tb deactivate` a whole
toolkit, a single bundle (`tb deactivate heptapod/mg5`), or one tool
(`tb deactivate heptapod__noisy`). Everything you expose lives in your
**profile**; [Curating tools](guides/curating-tools.md) covers that in depth.

---

## Next steps

- [Install & activate](guides/install-and-activate.md) — the everyday loop in detail
- [Curating tools](guides/curating-tools.md) — bundles, per-tool control, `list -v`
- [Connecting clients](guides/connecting-clients.md) — scopes, multiple clients, removal
- [How it works](explanation.md) — why install ≠ serve, and how serve resolves a profile
