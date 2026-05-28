# Troubleshooting

## "No active profile"

`tb serve` couldn't resolve a profile. Activate something (creates the default
profile) or set one:

```bash
tb activate <toolkit>            # builds the default profile
tb profile set-default <name>    # or point at an existing one
```

## The agent sees no tools

Installing doesn't serve — check the toolkit is active and the client is wired:

```bash
tb list                # is it ✓ active?
tb connect --list      # is toolbase wired into your client?
tb serve --dry-run     # what would be served
```

Restart the agent session after `connect` or any profile change.

## A tool I expected is missing

```bash
tb list -v             # shows each tool and why it's hidden
```

A tool is hidden if its bundle isn't active, you deactivated it, or its bundle
needs config:

```console
✗ solve   [bundle: symbolic]  (needs config: cas_path)
```

Set the key (`tb config set <toolkit> cas_path <value>`) and re-serve.

## "config incomplete" / a toolkit is skipped at serve

A required config field is unset. Find and fill it:

```bash
tb config show <toolkit>       # look for <NEEDS VALUE>
tb config set <toolkit> <key> <value>
tb config validate <toolkit>
```

## Client launches the wrong toolbase (or none)

The wired command resolves via `PATH`. Check what's wired vs. what's current:

```bash
tb connect --list      # shows the toolbase on your PATH
```

If you switched virtualenvs, re-run `tb connect` (optionally `--abspath` to pin
the exact binary).

## Project's `.mcp.json` prompts teammates

Claude Code shows a one-time trust prompt per person for a project's
`.mcp.json`. That's expected — approve once.

## Start over

```bash
tb reset --dry-run     # preview what would be removed
tb reset --all         # remove caches + installs (keeps login + logs)
```
