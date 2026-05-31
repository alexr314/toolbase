# Configuring toolkits

Some toolkits need values to work: an API key, a path, a default setting.
This is separate from curation. Configuration is the data a toolkit needs,
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

The file (`~/.toolbase/config/calculator.yaml`) is canonical. `config set`
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

## Pointing tools at the agent's workspace

Toolkits can declare a field that resolves at serve time to a directory
in the harness's environment:

- `${CWD}` — the directory the harness (Claude Code, Codex, Orchestral, …)
  launched `tb serve` from. Use for "where the agent is working right now."
- `${PROJECT_ROOT}` — the discovered `.toolbase/` parent, or `${CWD}` if
  there is none. Use for "the project this work is committed to."

`tb config show` renders the template alongside its current expansion:

```console
$ tb config show heptapod
heptapod
  user layer:    ~/.toolbase/config/heptapod.yaml
  project layer: <project>/.toolbase/config/heptapod.yaml

  base_directory: ${CWD}  → /Users/you/papers/zprime  # from schema default
  cache_enabled: false                                # from user
```

Pin a specific path in either layer to override:

```bash
tb config set heptapod base_directory ~/heptapod-sandbox --user
```

User values beat the template; project layer beats user layer.

## User vs project layers

Config has two layers, and **project overrides user** key by key. `config`
writes the project layer by default. Pass `--user` for the user layer.

| Layer | File | For |
|---|---|---|
| User | `~/.toolbase/config/<toolkit>.yaml` | values that follow you (secrets, machine paths) |
| Project | `<project>/.toolbase/config/<toolkit>.yaml` | values committed with the project |

```bash
tb config set calculator precision 10                # project layer (default, committed)
tb config set calculator cas_path /opt/sympy --user  # user layer (private, your machine)
```

Keep secrets in the user layer (never committed). Pin shared, non-secret
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

- [Curating tools](curating-tools.md): the curation half
- [Projects & teams](projects-and-teams.md): the project layer in context
