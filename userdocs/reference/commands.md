# Command reference

`toolbase` and `tb` are the same command. State-changing commands accept
`--yes` / `--no` / `--no-input` for non-interactive use.

## Installing & serving

| Command | Purpose | Key flags |
|---|---|---|
| `tb install NAME` | Install a toolkit (registry name, `name@version`, or a local path) | `-g`, `-l`, `-e`, `-a/--activate`, `--version`, `--no-skills` |
| `tb install FILE.yaml` | Install every toolkit an import file lists (one command provisions a project's set) | file-level `-g`/`-l` + prompt flags; per-toolkit options go on the entries |
| `tb install FILE.tar.gz` | Install an exported toolkit tarball (registry-free distribution) | same flags as a path install; `-e` rejected |
| `tb export [PATH]` | Package a toolkit dir as `<name>-<version>.tar.gz` (publish's packaging, no upload) | `-o/--output` |
| `tb uninstall NAME` | Remove a toolkit — all versions, or one slot with `NAME@VERSION` (stale pins are cleaned up) | `-y`/`--no`/`--no-input` |
| `tb list` | List installed toolkits, active/inactive | `-v/--verbose`, `--json` |
| `tb activate ITEM` | Expose a toolkit / `toolkit/bundle` / `toolkit__tool` | `-g`, `-l` |
| `tb deactivate ITEM` | Hide a toolkit / bundle / tool | `-g`, `-l` |
| `tb serve` | Serve the active profile over MCP (the harness runs this) | `--profile`, `--dry-run`, `--call-timeout` |
| `tb connect [HARNESS]` | Wire toolbase into a harness: `claude-code`/`codex` config, or scaffold an `orchestral` script | `-g`, `-l`, `--profile`, `--abspath`, `--remove`, `--dry-run`, `--list`, `--harnesses`, `--out`, `--force` |
| `tb disconnect HARNESS` | Remove toolbase from a harness | `-g`, `-l` |
| `tb orchestral` | Run the agent script from `tb connect orchestral` | `--script` |
| `tb logs` | Tail the serve log | `-n`, `-f/-F`, `--all`, `--raw` |

## Configuration

| Command | Purpose | Key flags |
|---|---|---|
| `tb install TOOLKIT[a,b]` or `tb install TOOLKIT --bundle a` | Install only the named bundle(s); additive on re-install (pip-style). | `--bundle`, `--rebuild`, plus all the usual install flags |
| `tb config show TOOLKIT` | Show effective config (merged user+project) | `--user`, `--project`, `--layer` |
| `tb config set TOOLKIT KEY VALUE` | Set one field | `--user`, `--project`, `--layer` |
| `tb config unset TOOLKIT KEY` | Remove one field | layer flags |
| `tb config init TOOLKIT` | Scaffold a commented YAML config file from the toolkit's `config:` schema | layer flags, `-f/--force` |
| `tb config edit TOOLKIT` | Open the config file in `$EDITOR` | layer flags |
| `tb config path TOOLKIT` | Print the config file path | layer flags |
| `tb config validate TOOLKIT` | Check required fields/types | none |
| `tb profile list` | List profiles (user + project), active marked | none |
| `tb profile show [NAME]` | Print a profile (defaults to active) | none |
| `tb profile create NAME` | New profile | `-g`, `-l`, `--from`, `--empty` |
| `tb profile edit [NAME]` | Edit a profile in `$EDITOR` | `-g`, `-l` |
| `tb profile delete NAME` | Delete a profile | `-g`, `-l` |
| `tb profile set-default NAME` | Set the active profile (writes `serve.yaml`) | `-g`, `-l` |
| `tb profile path NAME` | Print a profile's file path | none |
| `tb profile tools [TOOLKIT]` | List available bundles + tools | none |
| `tb setup TOOLKIT` | Run a toolkit's `setup.py` | `--check`, `--reset` |
| `tb project init` | Create `.toolbase/` + empty manifest here | none |

## Authoring & publishing

| Command | Purpose | Key flags |
|---|---|---|
| `tb init NAME` | Scaffold a toolkit from template | `-p/--path`, `--with-docker`, `--with-setup` |
| `tb ingest` | Generate/re-sync `toolkit.yaml` from existing code | `--prune`, `--force` |
| `tb create NAME` | Reserve a name on the registry | `-c/--category` (req), `-d/--description` (req), `--version` |
| `tb validate` | Check toolkit structure | none |
| `tb login` / `tb logout` / `tb whoami` | Registry auth | none |
| `tb publish` | Package + upload to the registry | `--dry-run`, `--allow-version-decrease` |

## Maintenance

| Command | Purpose | Key flags |
|---|---|---|
| `tb reset` | Clean up `~/.toolbase/` state | `--dry-run`, `--all`, `--include-config` |
