# End-to-end test harnesses

Thirteen driver scripts that exercise whole toolbase workflows against
synthetic toolkits: real venvs, real subprocesses, real MCP round-trips,
with the registry mocked so none of it needs the network or a published
toolkit.

They are **not** pytest tests — each is a standalone script that prints
its steps and exits non-zero on failure. That is why they run as a
separate CI job from the unit suite.

## Why they exist

They cover the seam unit tests can't reach. A unit test can prove the
install pipeline computes the right paths; only these can prove a venv
actually builds, a host subprocess actually starts, and a tool actually
answers over the wire. Several bugs have been caught here first — most
recently `tb uninstall` leaving a version recorded for a slot it had
just deleted.

The corresponding cost is that they rot quietly when nothing runs them.
Seven were failing for months against changes nobody updated them for.
CI runs all thirteen on pull requests now, and fails if any fails.

## Running

From the repo root, with the dev venv installed (`pip install -e '.[dev]'`):

```bash
# one harness
python tests/e2e/run_install_e2e.py

# all of them
for f in tests/e2e/run_*.py; do python "$f" || echo "FAIL $f"; done
```

Each builds its own tree under `$TMPDIR/tb-*-e2e/` and points `HOME`
there, so they don't touch your real `~/.toolbase/`. They also `chdir`
into that tree: project discovery walks up from the working directory,
and since `.toolbase/` is the project marker, a harness left in the
checkout would resolve the repo as its project and read state left by
its own previous run. CI fails the job if a run dirties the working
tree.

They are independent and can run in any order, with one exception:
`run_serve_e2e.py` consumes the install `run_install_e2e.py` produces.

## The harnesses

| Script | Covers |
| --- | --- |
| `run_install_e2e.py` | `install` against a mocked registry: download, extract, env detection, venv build, metadata write |
| `run_serve_e2e.py` | `serve` end to end — discovery, host spawn, handshake, proxy tools, tool calls over MCP stdio |
| `run_editable_e2e.py` | `install -e`, and that an editable slot loses the unpinned fallback |
| `run_multi_version_e2e.py` | the multi-version cache layout — several versions of one toolkit side by side |
| `run_envs_e2e.py` | the environments/scoping substrate: cache slots, per-slot metadata, project vs user layers |
| `run_two_layer_config_e2e.py` | user→project config merge across the real CLI surface |
| `run_setup_e2e.py` | the declarative setup system: config schema, validation, injection into a running tool |
| `run_setup_script_e2e.py` | `setup.py` runner — Tier-2 derived state reaching a tool body |
| `run_calculator_synthetic_e2e.py` | setup with a large download: SHA256-verified extract, `set_config` writeback, negative path |
| `run_bundles_e2e.py` | `bundles.requires:` — bundles dropped when their config keys are unset |
| `run_restart_e2e.py` | the orchestrator's auto-restart machinery after a host crash |
| `run_ingest_e2e.py` | `ingest` — turning an existing repo into a toolkit |
| `run_legacy_layout_e2e.py` | reading a pre-cutover on-disk layout without migrating it |

## Fixtures

`test-toolkit/` is the general-purpose synthetic toolkit; the others are
shaped for one harness each (`test-calculator-synthetic-toolkit/`,
`test-config-toolkit/`, `test-restart-toolkit/`,
`test-setup-script-toolkit/`, `test-existing-repo-fixture/`). None is
published anywhere — the registry is mocked.

## Not covered

- A real registry round-trip (publish → install). These mock it.
- Conda-mode toolkits. The fixtures are venv mode only; conda mode is
  implemented but unexercised here.
- Windows. The harnesses assume a POSIX layout, including symlinks.

`manual_arxiv_postship_check.md` is a hand-run checklist against the
live registry, for the parts the above deliberately don't cover.
