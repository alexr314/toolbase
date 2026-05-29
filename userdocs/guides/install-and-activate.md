# Install & activate

Get a toolkit onto your machine and expose it to the agent.

!!! abstract "Running example"
    The guides use a toy `calculator` toolkit (bundles `basic`, `scientific`,
    `symbolic`) and a companion `units` toolkit, stand-ins for whatever you
    actually install.

## Install

```bash
tb install calculator           # latest
tb install calculator@1.4.0     # a specific version
```

Installing builds the toolkit an isolated environment. It does **not** serve
it. That's `activate`.

## Activate

```bash
tb activate calculator
```

```console
✓ Activated calculator (whole toolkit).
```

`tb install calculator -a` does both at once.

## See what you have

```bash
tb list
```

```console
Active profile: default

✓ calculator   1.4.0   (active)
✗ units        0.9.0   (inactive)
```

`tb list -v` adds a per-tool view (see [Curating tools](curating-tools.md)).

## Update & uninstall

```bash
tb install calculator@1.5.0   # newer version, alongside 1.4.0
tb uninstall calculator       # remove it
```

## Next

- [Curating tools](curating-tools.md): serve only the bundles/tools you want
- [Configuring toolkits](configuring-toolkits.md): API keys, paths
- [Connecting harnesses](connecting-harnesses.md): wire toolbase into your agent
