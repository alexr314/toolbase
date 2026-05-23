# Toolkit Setup System — Specification

> **ASPIRATIONAL — Phase 3C, not yet implemented.** No code exists yet for the setup system. The forward-compat hook is in place in `toolbase serve` (toolkits with `setup_script: true` are skipped at startup with a clear "setup not yet run" message), but `SetupContext`, `toolbase setup`, and the declarative `config:` block are all unimplemented. When Phase 3C lands, this banner comes off.

**Status:** Approved design, awaiting Phase 3C implementation.
**Last revised:** 2026-05-06 (file-first model per Tony Menzo feedback; full rewrite).

---

## Why this exists

Many scientific toolkits need configuration that isn't bundled with the code:

- **API keys** for external services (NASA, OpenAI, journal APIs).
- **Paths** to local data, output directories, scratch space.
- **Large data** that can't ship with the toolkit (e.g., ASTER's ~2.3 GB opacity files).
- **Hardware capabilities** that must be detected, not assumed (CUDA, available memory, MPI).

Today (pre-3C), there is no system. Authors hand-roll `.env` files or hardcode paths, every toolkit feels different, and stateful tools that need configuration can't ship cleanly through Toolbase.

Phase 3C standardizes this. Goal: every toolkit uses the same configuration mechanism, the user always knows where their config lives, and the same flow works whether the user is human (interactive prompts) or coding agent (file-only, no prompts).

---

## Design principles

1. **Config files are canonical.** Every toolkit's persistent configuration lives at `~/.toolbase/config/<toolkit>.yaml`, in human-readable, hand-editable YAML. This file is the source of truth. Prompts, scripts, and CLI flags all *write to this file*; runtime *reads from this file*.

2. **Prompts are scaffolding, not the path.** Interactive prompts at install time are a convenience for users who want hand-holding. They are skippable (`--no-prompt`), automatically skipped when stdin is not a TTY, and never the only way to set a value. Anything a prompt can do, file editing can also do.

3. **Flag-equivalence.** Every persistent state change available through prompts is also reachable via CLI commands (`toolbase config set <toolkit> <key> <value>`). This is non-negotiable per the project-wide flag-equivalence principle.

4. **Two tiers, one storage.** Simple toolkits use a declarative YAML schema (Tier 1). Complex toolkits add a Python script (Tier 2). Both write to the same canonical config file. The user opens it the same way regardless of which tier the toolkit uses.

5. **Refuse to serve incomplete config.** A toolkit with required config that isn't filled in is skipped at serve time with a clear pointer to the file path and a one-line `toolbase config edit <toolkit>` suggestion. No silent fallbacks; no half-running tools.

---

## Core model: state vs runtime, recapped

Every tool input is either:

- **Runtime input.** Decided per call by the agent. Visible in MCP schema. Examples: `query`, `star_name`, `n_results`. Lives in the function signature normally.
- **State input.** Constant about the deployment. Hidden from MCP schema. Examples: `api_key`, `opacity_path`, `output_directory`. Declared via Orchestral's `state=[...]` decorator argument.

The setup system is the platform's answer to **"how do `state` values get filled in?"** It captures values from the user (file or prompt) and injects them into tools at serve startup via `Agent(tool_config={...})`.

This is the discipline that lets tool authors write code without leaking secrets to the LLM and without writing custom config-loading boilerplate.

---

## Two-tier architecture

### Tier 1 — declarative (`toolkit.yaml`)

For toolkits whose config is "ask the user for these N values, save them." No logic, no validation beyond type-checking, no downloads.

```yaml
# toolkit.yaml
name: my-toolkit
version: 0.1.0
# ... other metadata ...

config:
  - name: api_key
    description: "Your NASA Exoplanet Archive API key. Get one at https://example.com/keys"
    type: secret
    required: true

  - name: data_path
    description: "Where to store downloaded papers"
    type: path
    default: ~/.toolbase/data/my-toolkit
    required: false

  - name: max_workers
    description: "Number of parallel workers for batch operations"
    type: integer
    default: 4
    required: false
```

**Supported types:**

| Type | Validation | Notes |
|---|---|---|
| `string` | Non-empty if required | Plain text |
| `secret` | Non-empty if required | Hidden input on prompt; never displayed in `config show` |
| `path` | Tilde expansion, optional `must_exist` | Returns absolute Path |
| `integer` | Optional `min`, `max` | |
| `float` | Optional `min`, `max` | |
| `boolean` | Y/n parsing on prompt | |
| `choice` | Must match one of `options:` list | `options:` is required |

**What Tier 1 does NOT do:**
- File content validation (e.g., "must be .h5")
- Downloads
- Multi-step or conditional flows
- Custom logic of any kind

If you need any of that, use Tier 2.

### Tier 2 — script (`setup.py`)

For non-trivial setup. A Python file at the toolkit root that runs in the toolkit's own venv/conda env.

```python
# setup.py
from toolbase.setup import SetupContext

def setup(ctx: SetupContext) -> bool:
    """Interactive setup. Called by `toolbase setup <toolkit>` and at install time."""
    # ... your logic, see SetupContext API below ...
    return True   # or False on failure

def validate(ctx: SetupContext) -> bool:
    """Quick check called by `toolbase serve` at startup. Should be fast (<100 ms)."""
    return ctx.get_config('opacity_path') is not None
```

**Activated by adding to `toolkit.yaml`:**

```yaml
setup_script: true
```

When `setup_script: true`:

1. Tier 1 (declarative `config:` block) runs first if present, populating defaults and capturing user input.
2. `setup.py::setup(ctx)` runs after, with the declarative values already loaded into `ctx`.
3. At every `toolbase serve` startup, `setup.py::validate(ctx)` is called. If it returns `False`, the toolkit is skipped with a pointer to `toolbase setup <toolkit>`.

`setup.py` runs in the toolkit's isolated environment, so it can use any dependency the toolkit declares.

---

## Storage and runtime

### Where config lives

Every toolkit's configuration lives in one file:

```
~/.toolbase/config/<toolkit>.yaml
```

Format example (after a hypothetical install):

```yaml
# ASTER toolkit configuration
# This file is canonical. Edit anytime; changes apply on next `toolbase serve`.
# Auto-generated 2026-05-06; safe to hand-edit.

api_key: <your-key-here>            # secret, masked in `toolbase config show`
data_path: /home/alex/aster-data
max_workers: 4
opacity_path: /data/opacity         # set by setup.py during install
use_gpu: true                       # set by setup.py during install
```

The file:
- Has comments preserved across edits.
- Is human-readable and hand-editable. Users can open it in any editor at any time.
- Has mode `0600` (user-readable only) because it may contain secrets.
- Is loaded fresh at every `toolbase serve` startup. No caching.

### How values reach tools

At serve startup:

1. For each enabled toolkit, the orchestrator reads `~/.toolbase/config/<toolkit>.yaml`.
2. For each tool decorated with `@define_tool(state=[...])`, the orchestrator builds a `tool_config` dict mapping each state field name to its config value.
3. The orchestrator passes this dict to the toolkit subprocess via `Agent(tool_config={tool_name: {state_name: value, ...}})`.
4. Orchestral injects state values when the agent calls the tool. Values arrive in the function signature alongside runtime args from the LLM.

Authors don't need to call any helper. The `state=[...]` declaration on the decorator is enough; values appear as ordinary parameters.

```python
@define_tool(state=["api_key", "data_path"])
def search_papers(query: str, max_results: int = 10, *,
                  api_key: str, data_path: str) -> str:
    # api_key and data_path are injected from ~/.toolbase/config/my-toolkit.yaml
    # query and max_results come from the LLM
    ...
```

If a state field is declared in `state=[...]` but missing from the config file (and required), the toolkit refuses to serve at startup.

---

## User-facing CLI surface

### `toolbase install <toolkit>`

The install flow integrates with setup as follows:

**TTY (default):**

```
Installing ASTER 1.0.0...
  Downloaded
  Environment created (venv, Python 3.12)
  Dependencies installed

ASTER requires the following configuration:

  api_key (required) — Your NASA Exoplanet Archive API key.
                       For info on how to get it, see: https://example.com/aster
  > <user types here, or presses Esc>
  (press Esc to skip and edit later at ~/.toolbase/config/aster.yaml)

  opacity_path (required) — Path to opacity data files (~2.3 GB)
                            Setup script will offer to download.
  > <captured by Tier 2 setup.py if applicable>
```

If the user presses Esc on any prompt, install completes and the config file is dropped with a placeholder (`<NEEDS VALUE>`) for that field. `toolbase serve` will refuse to serve that toolkit until it's filled in.

**Non-TTY (`--no-prompt`, or stdin not a TTY):**

```
Installing ASTER 1.0.0...
  Downloaded
  Environment created
  Dependencies installed

ASTER requires configuration. No prompts shown (non-interactive context).
Config file written: ~/.toolbase/config/aster.yaml

Required fields needing values:
  - api_key (secret) — Your NASA Exoplanet Archive API key
  - opacity_path (path) — Path to opacity data files

Edit the file or run: toolbase config set aster <key> <value>
```

Install always succeeds in both modes. Configuration is the user's next step.

### `toolbase setup <toolkit>`

Runs Tier 2's `setup.py::setup(ctx)` if present. Used to:
- Re-run setup after install (e.g., new credentials needed).
- Run setup that was skipped during install (`--no-prompt` mode).
- Trigger setup-script logic like data downloads.

Flags:
- `--reset` — clear current config and start fresh.
- `--check` — run `validate(ctx)` only, no prompts. Useful for diagnosing why a toolkit refuses to serve.

### `toolbase config <toolkit> ...`

File-canonical config management.

```
toolbase config show <toolkit>             # print config (secrets masked)
toolbase config edit <toolkit>             # opens file in $EDITOR
toolbase config path <toolkit>             # prints absolute file path
toolbase config set <toolkit> <key> <value>    # mutate one field
toolbase config unset <toolkit> <key>      # remove a field
toolbase config validate <toolkit>         # check required fields are present, types correct
```

`config set` is the flag-equivalent path for the prompt UX. Anything a prompt does, `config set` can do without an interactive session.

### `toolbase serve`

At startup, for each enabled toolkit:

1. Read `~/.toolbase/config/<toolkit>.yaml`.
2. Validate required fields against the toolkit's `config:` schema and (if Tier 2) `validate(ctx)`.
3. If invalid, skip the toolkit with a clear message:

```
Checking toolkits...
  arxiv-search: ready (2 tools)
  aster: skipped — required config missing
    Missing: api_key, opacity_path
    Edit: ~/.toolbase/config/aster.yaml
    Or run: toolbase setup aster

Starting MCP server with 1 toolkit (2 tools).
```

4. Continue serving the rest. A misconfigured toolkit never crashes the orchestrator.

---

## SetupContext API (Tier 2)

The `ctx: SetupContext` object is the interface between `setup.py` and the platform.

### Output (styled, no emojis)

```python
ctx.info("Checking dependencies...")
ctx.warn("CUDA not detected; falling back to CPU mode")
ctx.error("Could not reach download server")
ctx.hint("Try setting HTTP_PROXY or running on a different network")
ctx.success("Setup complete")
```

All output uses Rich text styling (consistent with the rest of the CLI). Authors don't import Rich themselves. No emojis (per the project-wide rule).

### Input prompts

```python
# Plain string
name = ctx.prompt("Enter project name:")
name = ctx.prompt("Enter project name:", default="my-project")

# Typed
path = ctx.prompt_path("Data directory:")                    # tilde expanded, returns Path
path = ctx.prompt_path("Data directory:", must_exist=True)
port = ctx.prompt_int("Port number:", default=8080)
n = ctx.prompt_int("Worker count:", min=1, max=64)
score = ctx.prompt_float("Score threshold:", default=0.95)

# Secrets (masked input)
key = ctx.prompt_secret("API key:")

# Yes/no
go = ctx.confirm("Download 2.3 GB of data?", default=False)

# Menu
choice = ctx.choice(
    "How would you like to proceed?",
    [
        ("download", "Download automatically (~2.3 GB)"),
        ("path", "I have the data; let me provide the path"),
        ("skip", "Skip for now"),
    ],
)
# Returns "download", "path", or "skip"
```

All prompts honor the `--no-prompt` / non-TTY contract: in non-interactive mode, prompts return their `default` if specified, or skip and write `<NEEDS VALUE>` to the config file otherwise. They never block on user input.

### Config (read/write to the canonical YAML file)

```python
# Read
v = ctx.get_config('api_key')
v = ctx.get_config('max_workers', default=4)
all_config = ctx.config                  # dict-like

# Write (immediately persisted to ~/.toolbase/config/<toolkit>.yaml)
ctx.set_config('opacity_path', '/data/opacity')
ctx.set_config('use_gpu', True)
```

Writes preserve YAML comments and ordering. Authors never have to think about file format or encoding.

### Downloads

```python
ctx.download(
    url="https://data.toolbase.org/aster/opacity-1.0.tar.gz",
    destination=ctx.data_dir / "opacity",   # standard data path
    description="Downloading opacity data",
    size_hint="2.3 GB",                     # for progress display
    extract=True,                           # auto-handle .tar.gz, .zip, .tar
    sha256="abc123def456...",               # optional checksum verification
)
```

Progress bar via Rich. SHA256 verification on completion if provided. Resumable on retry.

### Standard paths

```python
ctx.toolkit_path           # the extracted toolkit directory (read-only from setup.py's POV)
ctx.data_dir               # ~/.toolbase/data/<toolkit>/  (auto-created, persistent)
ctx.cache_dir              # ~/.toolbase/cache/<toolkit>/ (auto-created, OK to delete)
ctx.config_path            # absolute path to ~/.toolbase/config/<toolkit>.yaml
```

### Raw Python

`SetupContext` is a toolbox, not a sandbox. Authors can drop to raw Python whenever:

```python
def setup(ctx):
    import subprocess
    result = subprocess.run(['nvcc', '--version'], capture_output=True)
    ctx.set_config('use_gpu', result.returncode == 0)
    return True
```

Standard caveats apply: `setup.py` runs in the toolkit's isolated environment, has access to its declared dependencies, and runs as the user. Don't do anything you wouldn't trust a toolkit author to do — but that's a curation question, not a sandbox question.

---

## Error handling

1. **Never let one toolkit break the server.** `serve` skips invalid toolkits and logs the reason; other toolkits keep working.

2. **Every error has a fix-it line.** Format:
   ```
   aster: skipped — opacity_path does not exist on disk
     Run: toolbase setup aster
   ```

3. **`setup.py` exceptions are caught, summarized, and logged.** Show the user a one-line description and a path to the full traceback log:
   ```
   aster setup failed: connection refused while contacting download server
   Full log: ~/.toolbase/logs/setup-aster-2026-05-06.log
   ```

4. **Partial setup is valid.** Optional fields can be skipped. Required fields refuse-to-serve until filled. Users can fill them via prompt re-run, file edit, or `config set` — three paths to the same destination.

---

## Security

### `setup.py` runs arbitrary code

Mitigations:
- Runs inside the toolkit's isolated venv/conda env, not system Python.
- Curation gate: per the project-wide review policy, automated review (Bandit + Safety + LLM) catches obvious abuse pre-publish; post-hoc takedown handles the rest.
- `SetupContext` provides safe defaults: downloads verify SHA256 if provided, file paths are sanitized.

### Config storage

- Files are mode `0600` (user-readable only).
- `secret` type fields are masked in `toolbase config show` output.
- `secret` type fields are never displayed in normal CLI output (never in `list`, never in error messages).
- Values are passed to subprocesses via env or stdin, never via command-line args (avoids shell-history leakage).

### Subprocess injection

State values reach tool subprocesses via the orchestrator's `Agent(tool_config=...)` pathway, not by string-interpolation into shell commands. There is no path from a config value to shell execution unless the toolkit's own code creates one.

---

## Implementation outline

### Module layout

```
toolbase/
├── setup/
│   ├── __init__.py          # public: SetupContext, run_setup, validate_setup
│   ├── context.py           # SetupContext class
│   ├── declarative.py       # Tier 1: parses config: block from toolkit.yaml, runs prompts
│   ├── runner.py            # Tier 2: invokes setup.py / validate() in toolkit subprocess
│   ├── storage.py           # YAML read/write to ~/.toolbase/config/<toolkit>.yaml
│   ├── prompts.py           # TTY-aware prompt helpers (Rich-based, --no-prompt-honoring)
│   └── downloads.py         # SHA256-verified, progress-bar-equipped download helper
├── runtime.py               # not needed; tools receive state via Orchestral's Agent(tool_config=...)
├── cli.py                   # adds `setup`, `config show/edit/path/set/unset/validate` subcommands
└── serve/
    └── orchestrator.py      # reads config files, validates, populates Agent(tool_config=...)
```

### Phase 3C-1 (the MVP of the setup system)

1. `~/.toolbase/config/<toolkit>.yaml` storage layer (read/write, comment-preserving, 0600).
2. Tier 1 declarative parser + interactive prompt runner.
3. `toolbase config show/edit/path/set/unset/validate` subcommands.
4. `toolbase install` integration: runs Tier 1 on TTY, drops template on `--no-prompt`/non-TTY.
5. `toolbase serve` integration: reads config files, populates `Agent(tool_config=...)`, refuses to serve on missing required fields.
6. Tests for every prompt mode (TTY, --no-prompt, non-TTY) and every type (string, secret, path, int, float, bool, choice).

### Phase 3C-2 (Tier 2 — `setup.py`)

1. `SetupContext` class with the full API described above.
2. `setup.py::setup(ctx)` runner — invokes the script in the toolkit's subprocess.
3. `setup.py::validate(ctx)` runner — fast check at serve startup.
4. `toolbase setup <toolkit>` command (with `--reset`, `--check` flags).
5. Download helper with progress + SHA256.
6. Tests against a synthetic toolkit with a real `setup.py`.

### Phase 3C-3 (polish)

1. `toolbase init` template includes a sample `config:` block and an optional `setup.py` template.
2. Documentation update on toolbase-ai.com/docs/configuration and /docs/recipes.
3. Migration helper for the live `arxiv-search` toolkit (it has no config today; this just verifies that no-config toolkits keep working).

---

## What this isn't

- **Not a permission system.** Anyone with a config file has read access. Multi-user / multi-tenant toolkits are out of scope.
- **Not a secret manager.** Files are 0600 plain text. Users wanting integration with macOS Keychain, 1Password, etc. are out of scope for v1.
- **Not a package manager.** Setup runs once after install. Cron-like scheduled config updates are out of scope.
- **Not a UI / GUI.** Editing happens in the user's `$EDITOR` or via `config set`. No web UI for individual toolkit configs (the website doesn't and won't store user config — that lives on the user's machine).

---

## Success criteria

When this lands:

1. ASTER ports cleanly. The opacity-data download step runs through `setup.py`. The user goes from `toolbase install aster` to a fully working tool with one prompt session or one file edit.
2. A coding agent running `toolbase install aster --no-prompt` always succeeds; it can fill in the config file directly afterward.
3. Every state-bearing toolkit on the registry uses this system. No toolkit hand-rolls its own config flow.
4. `toolbase serve` is robust: a misconfigured toolkit gets skipped, the rest serve, the error message tells the user exactly what to do.
5. The user always knows where their config is. `~/.toolbase/config/<name>.yaml` is the answer to every "where is X stored?" question.
