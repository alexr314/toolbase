# End-to-end test harnesses

Two scripts that exercise the full Toolbase workflows end-to-end against
a synthetic toolkit. Originally built ad-hoc during the May 2026 MVP push;
preserved here so the work isn't lost across `/tmp` cleanup.

These are not pytest tests — they're driver scripts. They print to stdout
and exit non-zero on failure. Wrap with `pytest` if you want — the synthetic
toolkit and the harness logic stay the same.

## Layout

```
tests/e2e/
├── run_install_e2e.py       # exercises `toolbase install` against a mocked registry
├── run_serve_e2e.py         # exercises `toolbase serve` end-to-end via MCP stdio
└── test-toolkit/            # synthetic toolkit (2 tools: hello, add; 2 skill files)
    ├── toolkit.yaml
    ├── requirements.txt
    ├── tools/__init__.py
    └── skills/*.md
```

The synthetic toolkit is published as `tb-e2e-test` (in the mocked
registry — it does not exist on the real registry).

## Prereqs

- Python 3.12 (matches the package's `requires-python = ">=3.12"`).
- A venv with this package installed editable: `pip install -e .[dev]`.
  This pulls in pytest, toolbase itself, orchestral-ai, mcp.
- The dev venv must be activated (or its `bin/` on `PATH`) when running
  `run_serve_e2e.py` — that script uses `shutil.which("toolbase")` to
  find the binary.

## Running

From the repo root, with the dev venv active:

```bash
# 1. Install the synthetic toolkit. Network-free (mocked registry).
#    Creates $TMPDIR/tb-e2e/install-root/tb-e2e-test/ with a real venv.
python tb-package/tests/e2e/run_install_e2e.py

# 2. Serve it through the orchestrator. Requires step 1 first.
#    Connects an MCP client over stdio, calls each tool, asserts results.
python tb-package/tests/e2e/run_serve_e2e.py
```

Both scripts exit 0 on success. `run_serve_e2e.py` prints the last 30 lines
of `serve.log` so you can see the orchestrator's event stream.

## What each script proves

### `run_install_e2e.py`

- The install command's full pipeline runs against a network-mocked
  registry. Specifically: download (mocked), tarball extract, environment
  detection, venv creation, dependency install (this one is real — it
  hits PyPI to install orchestral-ai + mcp), metadata write.
- `.stk_meta.json` ends up with the right shape (env type, python_path,
  tool_count, skills_count, etc.).
- Useful when you change install logic and want a smoke test that doesn't
  depend on whatever's on the live registry today.

### `run_serve_e2e.py`

- The orchestrator discovers the synthetic toolkit, spawns its host
  subprocess in the toolkit's venv, reads the handshake, connects an
  MCPClient over HTTP loopback, builds proxy tools, and exposes them via
  the upstream MCPServer stdio interface.
- Tool calls round-trip through the full stack and return correct results.
- The serve.log tail is printed so you can spot regressions in the event
  stream (subprocess_spawned, mcp_client_connected, toolkit_loaded, etc.).
- Useful when you change serve internals — orchestrator, host, proxy tool —
  and want to verify the loop still closes.

## What these scripts do NOT cover

- Real registry roundtrip (publish → install). For that, see the
  `arxiv-search` toolkit on the live registry — install it from there.
- Conda-mode toolkits. The synthetic toolkit is venv mode only. Conda mode
  is implemented but not currently exercised by these harnesses.
- Crash/restart behavior. The serve test does the happy path only. There's
  no automatic restart logic in the orchestrator yet (deferred), so there's
  nothing to test on that side.
- TUI mode. `--no-tui` is the only mode shipped.

## Cleanup

Both scripts use `$TMPDIR/tb-e2e/` as their work root and clean up before
each run. Remove the directory manually if you want to start completely
fresh:

```bash
rm -rf "$TMPDIR/tb-e2e"
```
