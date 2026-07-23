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
tb config init calculator       # scaffold a commented YAML from the schema
tb config path calculator       # print the file location
tb config validate calculator   # check required fields are filled
tb config unset calculator precision
```

The file (`~/.toolbase/config/calculator.yaml`) is canonical. `config set`
just writes it for you.

## Which layer?

Three layers merge, later wins key-by-key:

| Layer | File | Use for |
|---|---|---|
| `--user` | `~/.toolbase/config/<kit>.yaml` | your defaults and secrets, every project on this machine |
| `--project` | `<repo>/.toolbase/config/<kit>.yaml` | **committed**, shareable values the whole team should get |
| `--local` | `<repo>/.toolbase/config/<kit>.local.yaml` | project-scoped **machine truth** — absolute tool paths, local builds. Gitignored automatically. |

Inside a project, `config set` defaults to the project layer. The rule of
thumb: if the value contains a path that only exists on your machine, it's
`--local`; if it's a secret, it's `--user`; otherwise `--project` and commit
it. Writing `--local` drops a `.toolbase/.gitignore` (if absent) so the file
can't reach git by accident.

## Scaffold a fresh config file

`tb config init <toolkit>` writes a commented YAML stub from the toolkit's
declared `config:` schema. Useful when you want to see the full set of
available keys (including the optional ones you might not have known about):

```bash
tb config init calculator                # project layer (default)
tb config init calculator --user         # user layer
tb config init calculator --force        # overwrite an existing file
```

Required fields land as `<NEEDS VALUE>`. Optional fields with defaults get
the default value. Optional fields without defaults are commented out so you
can see what's available without committing to a value.

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
$ tb config show calculator
calculator
  user layer:    ~/.toolbase/config/calculator.yaml
  project layer: <project>/.toolbase/config/calculator.yaml

  output_dir: ${CWD}  → /Users/you/work/report   # from schema default
  precision: 10                                  # from user
```

Pin a specific path in any layer to override:

```bash
tb config set calculator output_dir ~/calculator-scratch --user
```

An explicit value beats the template, and the layer order above still
applies: local beats project beats user.

Keep secrets in the user layer (never committed). Secret-typed fields are
masked in `config show`.

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
