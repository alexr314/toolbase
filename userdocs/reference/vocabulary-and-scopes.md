# Vocabulary & scopes

Quick lookup. For the narrative, see [Concepts](../explanation.md).

## Vocabulary

| Term | Definition |
|---|---|
| **toolkit** | The installable unit: one isolated environment, published by an author. |
| **bundle** | An author-defined group of tools within a toolkit. |
| **tool** | A single callable the agent invokes; namespaced `<toolkit>__<tool>`. |
| **loadout** | A user-defined set of tools the agent sees, across toolkits. |
| **active loadout** | The loadout `tb serve` currently exposes. |

## Reference forms

| Form | Means | Used by |
|---|---|---|
| `<toolkit>` | a whole toolkit | `activate`, `deactivate` |
| `<toolkit>/<bundle>` | one bundle | `activate`, `deactivate` |
| `<toolkit>__<tool>` | one tool | `activate`, `deactivate` |
| `<name>@<version>` | a specific version | `install`, `use`, `uninstall` |

## Scopes

Three keys, the same three everywhere:

| Flag | Stores in | Applies to |
|---|---|---|
| `-u` / `--user` | `~/.toolbase/` | you, every project |
| `-p` / `--project` | `<repo>/.toolbase/` (created in the cwd if none) | this repository, committed |
| `--private` | `<repo>/.toolbase/*.local.yaml` | this repository, **gitignored** |

`--private` is for machine truth that would be wrong on a teammate's clone:
an absolute tool path, or a pin to a local checkout. It's written to a
`.local.yaml` sibling of the committed file, wins over it, and drops a
`.toolbase/.gitignore` so it never reaches git. Not every command takes it —
harness configs have no gitignored variant.

Resolution order where layers overlap: user, then project, then private,
each overriding the last key by key.

**`install` takes no scope keys at all.** It puts a toolkit in the shared
user-level cache and writes no manifest, so there is never a question of
which file an install touched. `tb use` is the only command that pins a
version.

Every scoped command defaults to **this project**: `use`, `activate`,
`deactivate`, `loadout *`, `config *`, `connect`. One rule — act on the
project you're standing in — with `-u` to opt out.

A directory is a project when it has a `.toolbase/`; commands create one in
the current directory if there is none above. (Your own `~/.toolbase/` is
config, not a project, so your home directory is never one.)

Inside a project, a `-u` pin does not apply there — `tb use` says so when
that happens.

`config` additionally accepts `--layer user|project|private` as a scriptable
spelling of the same three.
