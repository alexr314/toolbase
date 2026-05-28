# Profiles (power user)

A **profile** is a named set of tools the agent sees. `tb activate` /
`tb deactivate` edit the `default` profile; when you want several you switch
between, use named profiles.

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
tb connect claude-code --profile paper   # wire a client to a specific profile
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

## User vs project profiles

Profiles exist at both scopes. A **project** profile shadows a **user**
profile of the same name — the project file wins whole.

## How the active profile is chosen

`tb serve` resolves it in this order:

1. `--profile <name>` flag
2. `default.profile` in the project's `serve.yaml`
3. `default.profile` in your user `serve.yaml`
4. a profile literally named `default`
5. otherwise: an error (there's no "serve everything" fallback)

## Next

- [Curating tools](curating-tools.md) — the `activate`/`deactivate` shortcuts
- [Reference → Schemas](../reference/schemas.md) — the full profile schema
