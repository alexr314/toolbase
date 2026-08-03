# Concepts

The handful of ideas the commands are built on.

## Toolkit, bundle, loadout, tool

| Term | What it is |
|---|---|
| **toolkit** | The unit you install: one isolated environment, published by an author (`calculator`). |
| **bundle** | An author-defined group of tools inside a toolkit (`calculator`'s `scientific`). |
| **loadout** | Your named set of tools the agent sees, assembled across toolkits. |
| **tool** | A single thing the agent calls (`calculator__add`). |

Tools live in bundles; bundles live in toolkits; toolkits are curated into
loadouts.

## Install ≠ serve

Three states, three commands:

| State | Command |
|---|---|
| Installed (in the cache) | `tb install` |
| Active (in the loadout, served) | `tb activate` |
| Wired (into your harness) | `tb connect` |

Installing never serves anything on its own. You activate what you want
exposed. This keeps the agent's tool set explicit: installing a new toolkit
doesn't silently change what the agent sees.

## Scopes: project and user

Most state-changing commands write to one of two layers, and the **project**
layer is the default:

- **Project** (default): applies to one repository, stored in its `.toolbase/`
  and committed so collaborators share it. Outside a repo, the command creates
  `.toolbase/` in the current directory.
- **User** (`-u`, or `--user` for `config`): applies to you everywhere.

Where they overlap, the project layer wins. (`install` is the exception: it
takes no scope at all. Binaries go to a shared user-level cache and no
manifest is written — `tb use` is what pins a version.) See
[Projects & teams](guides/projects-and-teams.md).

## The active loadout

`tb serve` always serves one loadout, resolved in order: a `--loadout` flag,
then `default.loadout` in the project's `serve.yaml`, then your user
`serve.yaml`, then a loadout named `default`. If none resolve, serve errors.
There is no "serve everything" fallback.
