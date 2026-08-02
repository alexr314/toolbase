# Vocabulary & scopes

Quick lookup. For the narrative, see [Concepts](../explanation.md).

## Vocabulary

| Term | Definition |
|---|---|
| **toolkit** | The installable unit: one isolated environment, published by an author. |
| **bundle** | An author-defined group of tools within a toolkit. |
| **tool** | A single callable the agent invokes; namespaced `<toolkit>__<tool>`. |
| **profile** | A user-defined set of tools the agent sees, across toolkits. |
| **active profile** | The profile `tb serve` currently exposes. |

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
profiles and harness configs have no gitignored variant.

Resolution order where layers overlap: user, then project, then private,
each overriding the last key by key.

**`install` takes no scope keys at all.** It puts a toolkit in the shared
user-level cache and writes no manifest, so there is never a question of
which file an install touched. `tb use` is the only command that pins a
version.

Defaults for the commands that *are* scoped:

| Command | Default scope |
|---|---|
| `use` | `--user` |
| `activate`, `deactivate`, `profile *`, `config *`, `connect` | `--project` |

Inside a repo with its own `.toolbase/`, a user-scope pin does not apply —
`tb use` says so when that happens.

`config` additionally accepts `--layer user|project|private` as a scriptable
spelling of the same three.
