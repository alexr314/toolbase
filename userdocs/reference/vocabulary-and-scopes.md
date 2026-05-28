# Vocabulary & scopes

Quick lookup. For the narrative, see [Concepts](../explanation.md).

## Vocabulary

| Term | Definition |
|---|---|
| **toolkit** | The installable unit — one isolated environment, published by an author. |
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
| `<name>@<version>` | a specific version | `install` |

## Scopes

| Flag | Scope | Stores in | Applies to |
|---|---|---|---|
| `-g` / `--global` (default) | user | `~/.toolbase/` | you, every project |
| `-l` / `--local` | project | `<repo>/.toolbase/` | this repository (committed) |

`config` uses `--user` / `--project` (and `--layer user\|project`) for the same
distinction. Project layer overrides user layer where they overlap.
