# Troubleshooting

## "No active profile"

`tb serve` couldn't resolve a profile. Activate something (creates the default
profile) or set one:

```bash
tb activate <toolkit>            # builds the default profile
tb profile set-default <name>    # or point at an existing one
```

## The agent sees no tools

Installing doesn't serve. Check the toolkit is active and the harness is wired:

```bash
tb list                # is it ✓ active?
tb connect --list      # is toolbase wired into your harness?
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

## Two toolkits expose the same tool name

If two active toolkits each define, say, an `add` tool, `tb serve` prints a
warning at startup and `tb list -v` annotates the rows:

```console
✓ add     [bundle: basic]  (also in: matrix)
```

This is **harmless by default**: tools are served namespaced as
`<toolkit>__<tool>`, so the agent still sees `calculator__add` and
`matrix__add` as distinct. The warning is a heads-up. It only *matters*
under bare serving (`tb serve --bare`): there, a shared name can't be served
bare unambiguously, so those tools fall back to their qualified
`<toolkit>__<tool>` form (both stay callable) with a warning, while the rest
are served bare. To give one a distinct bare name, set a `display_name:` in its
`toolkit.yaml`, or `tb deactivate` the other. `tb install` prints the same
heads-up when installing a toolkit whose tool names overlap an already-active
one.

## "config incomplete" / a toolkit is skipped at serve

A required config field is unset. Find and fill it:

```bash
tb config show <toolkit>       # look for <NEEDS VALUE>
tb config set <toolkit> <key> <value>
tb config validate <toolkit>
```

## Harness launches the wrong toolbase (or none)

The wired command resolves via `PATH`. Check what's wired vs. what's current:

```bash
tb connect --list      # shows the toolbase on your PATH
```

If you switched virtualenvs, re-run `tb connect` (optionally `--abspath` to pin
the exact binary).

## A toolkit works in my shell but fails under toolbase

Usually the tool was quietly relying on your shell's environment. A toolkit
runs in its own isolated environment, and toolbase deliberately does not hand
it the environment you launched from: variables bound to an activated conda
env or virtualenv are stripped, so they can't point the toolkit's own bundled
software at some other install. That's what makes a toolkit behave the same on
your laptop and on a colleague's.

The failure usually looks like a version or "file not found" complaint from an
external program, not from Python. Check the toolkit's log for what it
actually tried to load:

```bash
tb logs                              # tool calls, live
cat ~/.toolbase/logs/<toolkit>.log   # that toolkit's own stderr
```

If the tool needs a path or a data directory, set it as config rather than
exporting it in your shell:

```bash
tb config show <toolkit>              # what the toolkit expects
tb config set <toolkit> <key> <value>
```

If you author the toolkit, see
[Calling external programs](authoring/from-scratch.md#calling-external-programs).

## Project's `.mcp.json` prompts teammates

Claude Code shows a one-time trust prompt per person for a project's
`.mcp.json`. That's expected. Approve once.

## Start over

```bash
tb reset --dry-run     # preview what would be removed
tb reset --all         # remove caches + installs (keeps login + logs)
```
