# Loadouts

A **loadout** is a named, curated set of tools the agent sees when toolbase
serves. It draws from the toolkits you've installed and narrows them to the
bundles and tools you want for a given task. Because loadouts are just named
selections over the same installed toolkits, you can keep several side by side
in one project (say a lean `paper` set and a broader `analysis` set) and switch
which one the agent gets with a single command. `tb activate` and
`tb deactivate` edit the `default` loadout. Create named loadouts when you want
more than one.

## Manage named loadouts

```bash
tb loadout list                  # all loadouts (user + project), active marked
tb loadout create paper          # new loadout, scaffolded from default
tb loadout create paper --empty  # start blank
tb loadout create paper --from research   # copy an existing one
tb loadout edit paper            # open in $EDITOR
tb loadout show paper            # print it (defaults to the active loadout)
tb loadout path paper
tb loadout delete paper
```

## Switch the active loadout

```bash
tb loadout set-default paper          # persist: write default.loadout to serve.yaml
tb connect claude-code --loadout paper   # wire a harness to a specific loadout
tb serve --loadout paper --dry-run    # one-shot preview of a loadout
```

Scope flags apply: `tb loadout create paper -p` makes a project loadout,
`-u` a user one.

## The loadout file

A loadout is one YAML file (`<scope>/.toolbase/loadouts/<name>.yaml`),
partitioned per toolkit:

```yaml
toolkits:
  calculator:
    bundles: [basic, scientific]   # only these bundles
    tools:
      enabled: [factorial]         # plus this specific tool
      disabled: [log]              # minus this one
  units: {}                        # whole toolkit
```

- A toolkit with no `bundles`/`tools.enabled` (`{}`) serves the whole toolkit.
- Set `bundles` and/or `tools.enabled` to switch to an allowlist (the union of
  the two), then `tools.disabled` subtracts.

Loadouts are created two ways. `tb activate` / `tb deactivate` auto-create and
edit the `default` loadout at `<project>/.toolbase/loadouts/default.yaml`,
materializing `.toolbase/` in your cwd if there's none above. `tb loadout create
<name>` makes additional named ones. Both land under
`<scope>/.toolbase/loadouts/` (`-p` for project, `-u` for user).

## User vs project loadouts

Loadouts exist at both scopes. A **project** loadout shadows a **user**
loadout of the same name. The project file wins whole.

## How the active loadout is chosen

`tb serve` resolves it in this order:

1. `--loadout <name>` flag
2. `default.loadout` in the project's `serve.yaml`
3. `default.loadout` in your user `serve.yaml`
4. a loadout literally named `default`
5. otherwise: an error (there's no "serve everything" fallback)

Two things to keep straight. The loadout **named `default`** (step 4) is an
ordinary loadout file (`loadouts/default.yaml`, the one `tb activate` fills).
`serve.yaml`'s `default.loadout` (steps 2-3) is a separate setting that
overrides which loadout is active. A harness runs plain `tb serve` with no
`--loadout`, so it resolves from step 2 onward. Use `tb loadout set-default`
(or `tb connect --loadout`) to serve anything other than `default`.

## Next

- [Curating tools](curating-tools.md): the `activate`/`deactivate` shortcuts
- [Reference → Schemas](../reference/schemas.md): the full loadout schema
