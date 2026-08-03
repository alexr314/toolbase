# Command reference

`toolbase` and `tb` are the same command. State-changing commands accept
`--yes` / `--no` / `--no-input` for non-interactive use.

## Installing & serving

| Command | Purpose | Key flags |
|---|---|---|
| `tb install NAME` | Install a toolkit into the cache (registry name, `name@version`, or a local path). Writes no manifest and takes no scope | `-e`, `-a/--activate`, `--version`, `--bundle`, `--rebuild`, `--no-skills` |
| `tb install FILE.yaml` | Install every toolkit an import file lists (one command provisions a project's set) | prompt flags; per-toolkit options go on the entries |
| `tb install FILE.tar.gz` | Install an exported toolkit tarball (registry-free distribution) | same flags as a path install; `-e` rejected |
| `tb export [PATH]` | Package a toolkit dir as `<name>-<version>.tar.gz` (publish's packaging, no upload) | `-o/--output` |
| `tb uninstall NAME` | Remove a toolkit — all versions, or one slot with `NAME@VERSION` (stale pins are cleaned up) | `-y`/`--no`/`--no-input` |
| `tb use NAME@VERSION` | Choose which installed version serves — writes the pin only, no rebuild. Bare `NAME` clears the pin | `-u`, `-p`, `--private` |
| `tb status` | What applies here: project, loadout, what would serve, and anything broken | none |
| `tb list` | List installed toolkits, active/inactive, and which version serves (`-v` groups tools by bundle) | `-v/--verbose`, `--json` |
| `tb activate ITEM` | Expose a toolkit / `toolkit/bundle` / `toolkit__tool` | `-u`, `-p` |
| `tb deactivate ITEM` | Hide a toolkit / bundle / tool | `-u`, `-p` |
| `tb serve` | Serve the active loadout over MCP (the harness runs this) | `--loadout`, `--dry-run`, `--call-timeout`, `--bare`/`--qualified` |
| `tb connect [HARNESS]` | Wire toolbase into a harness: `claude-code`/`codex` config, or scaffold an `orchestral` script | `-u`, `-p`, `--loadout`, `--abspath`, `--remove`, `--dry-run`, `--list`, `--harnesses`, `--out`, `--force` |
| `tb disconnect HARNESS` | Remove toolbase from a harness | `-u`, `-p` |
| `tb orchestral` | Run the agent script from `tb connect orchestral` | `--script` |
| `tb logs` | Tail the serve log | `-n`, `-f/-F`, `--all`, `--raw` |

## Configuration

| Command | Purpose | Key flags |
|---|---|---|
| `tb install TOOLKIT[a,b]` or `tb install TOOLKIT --bundle a` | Install only the named bundle(s); additive on re-install (pip-style). | `--bundle`, `--rebuild`, plus all the usual install flags |
| `tb config show TOOLKIT` | Show effective config (merged user+project) | `--user`, `--project`, `--layer` |
| `tb config set TOOLKIT KEY VALUE` | Set one field | `--user`, `--project`, `--private` (gitignored machine paths), `--layer` |
| `tb config unset TOOLKIT KEY` | Remove one field | layer flags |
| `tb config init TOOLKIT` | Scaffold a commented YAML config file from the toolkit's `config:` schema | layer flags, `-f/--force` |
| `tb config edit TOOLKIT` | Open the config file in `$EDITOR` | layer flags |
| `tb config path TOOLKIT` | Print the config file path | layer flags |
| `tb config validate TOOLKIT` | Check required fields/types | none |
| `tb loadout list` | List loadouts (user + project), active marked | none |
| `tb loadout show [NAME]` | Print a loadout (defaults to active) | none |
| `tb loadout create NAME` | New loadout | `-u`, `-p`, `--from`, `--empty` |
| `tb loadout edit [NAME]` | Edit a loadout in `$EDITOR` | `-u`, `-p` |
| `tb loadout delete NAME` | Delete a loadout | `-u`, `-p` |
| `tb loadout set-default NAME` | Set the active loadout (writes `serve.yaml`) | `-u`, `-p` |
| `tb loadout path NAME` | Print a loadout's file path | none |
| `tb loadout tools [TOOLKIT]` | List available bundles + tools | none |
| `tb setup TOOLKIT` | Run a toolkit's `setup.py` | `--check`, `--reset` |
| `tb project init` | Create `.toolbase/` here, marking it a project | none |

## Authoring & publishing

| Command | Purpose | Key flags |
|---|---|---|
| `tb init NAME` | Scaffold a toolkit from template | `--path`, `--with-docker`, `--with-setup` |
| `tb ingest` | Generate/re-sync `toolkit.yaml` from existing code | `--prune`, `--force` |
| `tb create NAME` | Reserve a name on the registry | `-c/--category` (req), `-d/--description` (req), `--version` |
| `tb validate` | Check toolkit structure | none |
| `tb login` / `tb logout` / `tb whoami` | Registry auth | none |
| `tb publish` | Package + upload to the registry | `--dry-run`, `--allow-version-decrease` |

## Maintenance

| Command | Purpose | Key flags |
|---|---|---|
| `tb reset` | Clean up `~/.toolbase/` state | `--dry-run`, `--all`, `--include-config` |
