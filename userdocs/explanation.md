# Concepts

The handful of ideas the commands are built on.

## Toolkit, bundle, profile, tool

| Term | What it is |
|---|---|
| **toolkit** | The unit you install: one isolated environment, published by an author (`calculator`). |
| **bundle** | An author-defined group of tools inside a toolkit (`calculator`'s `scientific`). |
| **profile** | Your named set of tools the agent sees, assembled across toolkits. |
| **tool** | A single thing the agent calls (`calculator__add`). |

Tools live in bundles; bundles live in toolkits; toolkits are curated into
profiles.

## Install ≠ serve

Three states, three commands:

| State | Command |
|---|---|
| Installed (in the cache) | `tb install` |
| Active (in the profile, served) | `tb activate` |
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
- **User** (`-g`, or `--user` for `config`): applies to you everywhere.

Where they overlap, the project layer wins. (`install` is the exception: its
binaries live in a shared global cache, and `-l` pins the version into the
project.) See [Projects & teams](guides/projects-and-teams.md).

## The active profile

`tb serve` always serves one profile, resolved in order: a `--profile` flag,
then `default.profile` in the project's `serve.yaml`, then your user
`serve.yaml`, then a profile named `default`. If none resolve, serve errors.
There is no "serve everything" fallback.
