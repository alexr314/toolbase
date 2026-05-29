# Profiles

A **profile** is a named, curated set of tools the agent sees when toolbase
serves. It draws from the toolkits you've installed and narrows them to the
bundles and tools you want for a given task. Because profiles are just named
selections over the same installed toolkits, you can keep several side by side
in one project (say a lean `paper` set and a broader `analysis` set) and switch
which one the agent gets with a single command. `tb activate` and
`tb deactivate` edit the `default` profile. Create named profiles when you want
more than one.

## Manage named profiles

```bash
tb profile list                  # all profiles (user + project), active marked
tb profile create paper          # new profile, scaffolded from default
tb profile create paper --empty  # start blank
tb profile create paper --from research   # copy an existing one
tb profile edit paper            # open in $EDITOR
tb profile show paper            # print it (defaults to the active profile)
tb profile path paper
tb profile delete paper
```

## Switch the active profile

```bash
tb profile set-default paper          # persist: write default.profile to serve.yaml
tb connect claude-code --profile paper   # wire a harness to a specific profile
tb serve --profile paper --dry-run    # one-shot preview of a profile
```

Scope flags apply: `tb profile create paper -l` makes a project profile,
`-g` a user one.

## The profile file

A profile is one YAML file (`<scope>/.toolbase/profiles/<name>.yaml`),
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

Profiles are created two ways. `tb activate` / `tb deactivate` auto-create and
edit the `default` profile at `<project>/.toolbase/profiles/default.yaml`,
materializing `.toolbase/` in your cwd if there's none above. `tb profile create
<name>` makes additional named ones. Both land under
`<scope>/.toolbase/profiles/` (`-l` for project, `-g` for user).

## User vs project profiles

Profiles exist at both scopes. A **project** profile shadows a **user**
profile of the same name. The project file wins whole.

## How the active profile is chosen

`tb serve` resolves it in this order:

1. `--profile <name>` flag
2. `default.profile` in the project's `serve.yaml`
3. `default.profile` in your user `serve.yaml`
4. a profile literally named `default`
5. otherwise: an error (there's no "serve everything" fallback)

Two things to keep straight. The profile **named `default`** (step 4) is an
ordinary profile file (`profiles/default.yaml`, the one `tb activate` fills).
`serve.yaml`'s `default.profile` (steps 2-3) is a separate setting that
overrides which profile is active. A harness runs plain `tb serve` with no
`--profile`, so it resolves from step 2 onward. Use `tb profile set-default`
(or `tb connect --profile`) to serve anything other than `default`.

## Next

- [Curating tools](curating-tools.md): the `activate`/`deactivate` shortcuts
- [Reference → Schemas](../reference/schemas.md): the full profile schema
