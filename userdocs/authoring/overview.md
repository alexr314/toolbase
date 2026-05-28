# Authoring overview

Writing a toolkit, end to end:

```bash
tb init my-toolkit          # scaffold from template
cd my-toolkit
# write tools in tools/, declare them in toolkit.yaml (or run `tb ingest`)
tb validate                 # check structure
tb login                    # one-time registry auth
tb publish                  # package + upload
```

The pieces:

1. [Create & declare tools](create-and-declare.md) — scaffold, write tools, list them (and group into bundles).
2. [Config & setup](config-and-setup.md) — declare config the user fills in, gate bundles on it, and handle heavier setup.
3. [Validate & publish](publish.md) — validate, authenticate, ship.

Develop against a live install with `tb install -e . -a` — see
[Multi-version & editable](../guides/multi-version-and-editable.md).
