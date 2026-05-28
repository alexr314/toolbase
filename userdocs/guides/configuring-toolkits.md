# Configuring toolkits

Some toolkits need values to work — an API key, a path, a default setting.
This is separate from curation: configuration is the data a toolkit needs;
profiles are which tools the agent sees.

## See what a toolkit needs

```bash
tb config show calculator
```

```console
calculator
  user layer:    ~/.toolbase/config/calculator.yaml
  project layer: <project>/.toolbase/config/calculator.yaml

  angle_unit: degrees       # from user
  precision: 6              # from user
  cas_path: <NEEDS VALUE>   # from user
```

`<NEEDS VALUE>` marks a required field that isn't filled in.

## Set values

```bash
tb config set calculator angle_unit degrees   # TOOLKIT KEY VALUE
tb config set calculator precision 10
tb config edit calculator                      # open the file in $EDITOR
```

```bash
tb config path calculator       # print the file location
tb config validate calculator   # check required fields are filled
tb config unset calculator precision
```

The file (`~/.toolbase/config/calculator.yaml`) is canonical — `config set`
just writes it for you.

## Config-gated bundles

A bundle can require a config key. The `calculator`'s `symbolic` bundle needs
`cas_path`, so its tools stay hidden until you set it:

```console
$ tb list -v
    ✗ solve   [bundle: symbolic]  (needs config: cas_path)
```

```bash
tb config set calculator cas_path /usr/local/bin/sympy-cli
```

They appear on the next serve. `tb config validate` and `tb list -v` both name
the key a hidden bundle is waiting on.

## User vs project layers

Config has two layers; **project overrides user**, key by key.

| Layer | File | For |
|---|---|---|
| User | `~/.toolbase/config/<toolkit>.yaml` | values that follow you (secrets, machine paths) |
| Project | `<project>/.toolbase/config/<toolkit>.yaml` | values committed with the project |

```bash
tb config set calculator precision 10 --project   # committed, shared
tb config set calculator cas_path /opt/sympy --user  # private, your machine
```

Keep secrets in the user layer (never committed); pin shared, non-secret
values in the project layer. Secret-typed fields are masked in `config show`.

## Toolkits with a setup step

Some toolkits need more than values (downloads, derived files) and ship a
`setup.py`:

```bash
tb setup calculator           # run its setup
tb setup calculator --check   # verify without re-running
tb setup calculator --reset   # start over
```

## Next

- [Curating tools](curating-tools.md) — the curation half
- [Projects & teams](projects-and-teams.md) — the project layer in context
