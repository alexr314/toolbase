# Docs snippet audit — Phase 3C close

**Date:** 2026-05-06
**Author:** Package Agent
**Audience:** Frontend Agent (implementing the corrections)
**Scope:** `tb-website/public/docs-md/configuration.md` and
`tb-website/public/docs-md/recipes.md`. The associated rendered
pages live at `tb-website/app/docs/configuration/page.tsx` and
`tb-website/app/docs/recipes/page.tsx`; their content is the
markdown files above (rendered via the docs pipeline), so changes
land in the `.md` files only.

This audit was written against shipped 3C-2 CLI behavior. Every
snippet was tested locally before being flagged.

## Overall status

**Both pages need substantial corrections. Most are mechanical
(the `config:` block shape changed). One is structural (the whole
`env_vars:` concept doesn't exist).**

| Page | Snippets total | OK as-is | Needs correction | Section needs rewrite |
|---|---|---|---|---|
| `configuration.md` | 12 | 4 | 7 | 1 (the `env_vars:` section) |
| `recipes.md` | 14 | 4 | 9 | 1 (Recipe 1) |

Per the manager's sign-off:
> "If a section turns out to need substantial rewrite (per your
> Risk #4), don't try to write it for them — flag it back to me,
> I'll either write the rewrite myself or pair with you on it."

The two sections needing rewrite (`env_vars:` and Recipe 1) are
flagged at the end. Manager: please pick whether you want me to
draft those, you draft them, or we pair.

## Load-bearing finding: `config:` block shape

The docs pages use the **dict-keyed form**:

```yaml
config:
  data_dir:
    type: path
    description: "..."
    default: "..."
```

The shipped CLI uses the **list-of-objects form**:

```yaml
config:
  - name: data_dir
    type: path
    description: "..."
    default: "..."
```

Verified: the dict-keyed form is rejected at parse time:

```
$ toolbase validate
config: must be a list of field definitions, got dict
```

This affects **every** YAML snippet that includes a `config:`
block — most snippets in both pages. The fix is uniform:
flip from dict-keyed to list-of-objects, hoisting the field
name into a `name:` key.

## Correction list — `configuration.md`

### Section "Tier 1: Declarative configuration" — line 30 region

**Drift:** Dict-keyed `config:` shape.

**Existing (lines 34-50):**
```yaml
config:
  data_dir:
    type: path
    description: "Where to store outputs"
    default: "~/.my-toolkit/data"

  max_workers:
    type: integer
    description: "Number of parallel workers"
    default: 4

  api_endpoint:
    type: string
    description: "API endpoint URL"
    default: "https://api.example.com"
```

**Corrected:**
```yaml
config:
  - name: data_dir
    type: path
    description: "Where to store outputs"
    default: "~/.my-toolkit/data"

  - name: max_workers
    type: integer
    description: "Number of parallel workers"
    default: 4

  - name: api_endpoint
    type: string
    description: "API endpoint URL"
    default: "https://api.example.com"
```

### Section "Tier 1: Declarative configuration" — line 52 prose

**Drift:** "Values are saved to a `.env` file alongside the toolkit."

**Reality:** Values are saved as YAML at
`~/.toolbase/config/<toolkit>.yaml`. `.env` is not used anywhere.

**Corrected prose:**
> When the user installs your toolkit, Toolbase prompts them
> for each value. They can press Enter to accept defaults. Values
> are stored as YAML at `~/.toolbase/config/<toolkit>.yaml`,
> which is the canonical home — users can hand-edit at any time,
> or use `toolbase config set <toolkit> <key> <value>` from the
> CLI.

### Section "Supported types" — line 54-63 table

**Drift:** Missing `float` type. Otherwise correct.

**Existing table (lines 56-63):** the table lists six types.

**Corrected:** add a row for `float`:

```markdown
| `float` | Decimal number with optional `min`/`max` bounds |
```

Insert between `integer` and `boolean`. Also: `integer` now
supports optional `min`/`max` bounds, and `choice` requires an
`options:` list (≥2 entries) — small wording tweaks could mention
these but aren't load-bearing.

### Section "Environment variables" — lines 65-77 — FULL REWRITE

**The whole `env_vars:` concept doesn't exist.** There's no
`required` / `optional` field on `ToolkitMetadata`; declaring it
in `toolkit.yaml` does nothing.

**Manager-adjudicated replacement copy** (canonical — use verbatim;
sign-off `2026-05-06_to_package__3C3_signoff.md` §"On the env_vars:
rewrite"). Replace lines 65-77 with the heading "Secrets and
environment variables" and the following body:

> Toolbase stores all toolkit configuration — including secrets
> like API keys — in `~/.toolbase/config/<toolkit>.yaml` (mode
> 0600, readable only by you). Use `type: secret` in your
> `config:` block:
>
> ```yaml
> config:
>   - name: api_key
>     description: "Your NASA API key. Get one at https://api.nasa.gov."
>     type: secret
>     required: true
> ```
>
> Secrets are hidden during the install-time prompt and masked
> in `toolbase config show`. The values reach your tools
> through the same state-injection mechanism as any other
> declared field — never via the LLM's context, never via
> command-line arguments.
>
> If you specifically want a value to come from a shell
> environment variable (for example, in CI where the secret is
> provided by your platform's secret manager), the `setup.py`
> script in your toolkit can read it and forward to the
> canonical config:
>
> ```python
> import os
>
> def setup(ctx):
>     api_key = os.environ.get("MY_TOOLKIT_API_KEY")
>     if api_key:
>         ctx.set_config("api_key", api_key)
>     # else: fall through to whatever the user has already configured
>     return True
> ```
>
> This pattern is the recommended way to bridge environment-only
> secrets into Toolbase. The canonical store stays the YAML
> file; the env var is just the source for that one provisioning
> event.

**Why this answer (manager's reasoning, for context if Frontend
asks):**
- Strong-enough security model: 0600 file + masked display +
  `secret` type. The realistic threat model for a scientist's
  machine doesn't include attackers who already have read access
  to the user's home directory.
- One mental model, not two — the canonical file holds API keys,
  paths, integers, choices all the same way.
- Power users can bridge if needed, via the `setup.py` pattern
  shown.

### Section "Tier 2: Setup scripts (`setup.py`)" — line 98 region

**Drift:** Imports `SetupContext` from toolbase; toolkit envs
don't have toolbase installed.

**Existing (lines 99-119):**
```python
# setup.py
from toolbase.setup import SetupContext

def setup(ctx: SetupContext) -> bool:
    """..."""
    return validate(ctx)


def validate(ctx: SetupContext) -> bool:
    """..."""
    return True
```

**Corrected:**
```python
# setup.py
"""
Setup script for my-toolkit. Runs at install time and via
`toolbase setup my-toolkit`. The `ctx` argument is a
SetupContext; full API at https://toolbase-ai.com/docs/configuration#setupcontext-api.
"""


def setup(ctx) -> bool:
    """Interactive setup. Return True on success, False to refuse-to-serve."""
    return validate(ctx)


def validate(ctx) -> bool:
    """Quick check at every serve startup. Return True iff ready."""
    return True
```

The `ctx` parameter is passed in at runtime; the toolkit's venv
doesn't have toolbase installed, so the import would fail at
load time. (Type checkers can use a string annotation
`"SetupContext"` under `if TYPE_CHECKING:` — too advanced for the
intro example.)

### Section "Reading and writing config" — line 167 region

**Drift:** "Write (auto-saves to .env)" — wrong storage.

**Existing (lines 162-169):**
```python
# Read
path = ctx.get_config('data_path')
path = ctx.get_config('data_path', default='~/data')

# Write (auto-saves to .env)
ctx.set_config('data_path', '/data/foo')
```

**Corrected:**
```python
# Read
path = ctx.get_config('data_path')
path = ctx.get_config('data_path', default='~/data')

# Write — persists to ~/.toolbase/config/<toolkit>.yaml.
# Local snapshot is updated too, so a subsequent get_config
# in the same setup() call sees the new value.
ctx.set_config('data_path', '/data/foo')
```

### Section "A complete example" — line 198 region

**Drift:** Imports `SetupContext`; minor flow issues.

**Existing example uses:**
```python
from toolbase.setup import SetupContext

def setup(ctx: SetupContext) -> bool:
    ...
```

**Corrected:** Drop the import and the type annotation. The
example otherwise reads correctly against shipped behavior:

```python
# setup.py
from pathlib import Path

DATA_URL = "https://data.toolbase.org/my-toolkit/dataset_v1.tar.gz"


def setup(ctx) -> bool:
    existing = ctx.get_config('dataset_path')
    if existing and Path(existing).expanduser().exists():
        ctx.info(f"Dataset already configured: {existing}")
        return True

    choice = ctx.choice(
        "Dataset is required. How would you like to proceed?",
        [
            ("download", "Download automatically (~500MB)"),
            ("path",     "I have the data, provide the path"),
            ("cancel",   "Cancel"),
        ],
    )

    if choice == "download":
        dest = ctx.data_dir / 'dataset'
        ctx.download(
            url=DATA_URL, destination=dest,
            description="Dataset", size_hint="500MB", extract=True,
        )
        ctx.set_config('dataset_path', str(dest))
    elif choice == "path":
        path = ctx.prompt_path("Path to dataset:", must_exist=True)
        ctx.set_config('dataset_path', str(path))
    else:
        return False

    return validate(ctx)


def validate(ctx) -> bool:
    path = ctx.get_config('dataset_path')
    if not path:
        ctx.error("dataset_path not configured")
        ctx.hint("Run: toolbase setup my-toolkit")
        return False
    if not Path(path).expanduser().exists():
        ctx.error(f"Dataset path missing: {path}")
        return False
    return True
```

### Section "On install" — line 261 region

**Drift:** Models a "Required configuration" preamble that
doesn't match shipped behavior. The shipped flow does Tier-1
prompts inline during install (one prompt per field), then runs
setup.py if declared.

**Suggested replacement** (showing accurate output):

```text
$ toolbase install aster
Installing aster 1.0.0...
  ✓ Downloaded
  ✓ Environment created (venv, Python 3.12)
  ✓ Dependencies installed

  api_key (required) — Your NASA Exoplanet Archive API key.
  > <user types or presses Esc to defer>

  max_workers — Number of parallel workers (default: 4)
  > <Enter accepts default>

Running aster setup script...
  Downloading opacity data...
  [####################] 2.3 GB / 2.3 GB
  ✓ aster setup script complete.

✓ Successfully installed aster v1.0.0
```

### Section "On serve" — line 305 region

**Drift:** The `serve` output format has shifted slightly.

**Existing example shows:**
```text
✗ aster — Setup incomplete
    - opacity_path does not contain .h5 files
    - Run: toolbase setup aster
```

**Corrected (matches shipped output):**
```text
$ toolbase serve

Checking installed toolkits...
  ✗ aster validate(ctx) failed — Opacity data at /data/opacity is invalid or incomplete
     Edit: ~/.toolbase/config/aster.yaml
     Or: toolbase config edit aster
  … simple-api loading (venv)

Toolkit launch results:
  ✓ simple-api ready (3 tools)

Starting MCP server with 1 toolkit (3 tools)...
```

### Section "Anti-patterns" — line 316 region

**Drift:** Mostly OK. One bullet ("Don't write to `.env`
directly") references the wrong storage. Replace:

> - **Don't write to `.env` directly.** Use `ctx.set_config()`.
>   Direct writes can corrupt the file.

with:

> - **Don't write to `~/.toolbase/config/<toolkit>.yaml`
>   directly from setup.py.** Use `ctx.set_config()`. The helper
>   handles atomic writes, comment preservation, and mode 0600;
>   bypassing it can corrupt the file or leak secrets.

(Hand-editing the file outside setup.py is fine — that's exactly
what the file-canonical principle is for. The rule is about not
having setup.py rewrite the file directly.)

### NEW SECTION: "Authoring distinction — declared vs. derived"

**Per the manager's 3C-2 sign-off:** the user-supplied vs.
derived split is load-bearing for authors and isn't currently
explicit on the page. Add a new section between "Tier 2" and "The
SetupContext API" (~line 122):

```markdown
## Two ways state-fields get values

Both Tier-1 and Tier-2 produce the same outcome — values that
reach `@define_tool(state=[...])` parameters at serve time.
The difference is *where the value comes from*:

**User-supplied values** — declare them in the `config:` block.
The user fills them via prompts at install, or later via
`toolbase config set <toolkit> <key> <value>` or by hand-
editing the YAML. Use this for API keys, paths, worker counts —
anything the user has an opinion about.

**Derived values** — set them from `setup.py` via
`ctx.set_config('key', value)`. Use this for things the user
*shouldn't* be prompted for: auto-detected hardware (`use_gpu`),
download paths derived from `ctx.data_dir`, version strings
read from a downloaded data file, etc.

Both reach tools the same way. The platform stores both in the
same canonical YAML file. The user sees both via
`toolbase config show <toolkit>`. The distinction is purely
about *who decides the value* — the user (declared) or the
toolkit (derived).
```

### NEW SECTION: "Troubleshooting → setup.py errors"

Per the manager's 3C-2 sign-off — earn user trust by mentioning
the syntax-error UX. Add near the end of the page (or in a new
"Troubleshooting" subsection):

```markdown
### setup.py errors

If your `setup.py` has a syntax error or import problem,
`toolbase install` and `toolbase setup` will surface the
Python traceback inline (with file + line number) and write the
full traceback to a per-run log under
`~/.toolbase/logs/setup-<toolkit>-<timestamp>.log`. Fix the
file and re-run; the install pipeline never leaves the toolkit
in a half-installed state.
```

## Correction list — `recipes.md`

### Recipe 1 (lines 15-47) — FULL REWRITE

**Drift:** The whole `env_vars:` premise doesn't exist.

**Manager-adjudicated replacement copy** (canonical — use verbatim;
sign-off `2026-05-06_to_package__3C3_signoff.md`). Replace the
entire Recipe 1 (lines 15-47) with:

> ### Recipe 1 — A toolkit that needs an API key
>
> Most scientific tools wrap an external API. The user has a
> key; your tool needs to use it without the LLM ever seeing it.
>
> Declare the key in `toolkit.yaml`:
>
> ```yaml
> config:
>   - name: api_key
>     description: "Your NASA API key. Get one at https://api.nasa.gov."
>     type: secret
>     required: true
> ```
>
> Use it in your tool by declaring `state=["api_key"]` on the
> decorator:
>
> ```python
> from orchestral import define_tool
> import requests, json
>
> @define_tool(state=["api_key"])
> def search_neos(query: str, api_key: str) -> str:
>     """Search for near-Earth objects."""
>     response = requests.get(
>         "https://api.nasa.gov/neo/rest/v1/feed",
>         params={"api_key": api_key, "q": query},
>     )
>     return json.dumps(response.json())
> ```
>
> The user runs `toolbase install your-toolkit`, gets prompted
> once for the key (or skips with `--no-input` and edits
> `~/.toolbase/config/your-toolkit.yaml` afterward). The agent
> calls `search_neos(query="2024 NA1")`; your function receives
> both `query` (from the agent) and `api_key` (from the
> platform). The agent never sees the key.
>
> **For CI environments**, where the secret comes from the
> platform's secret manager rather than a hand-edited file, see
> the "Bridging environment variables" pattern in
> [Configuration](/docs/configuration#secrets-and-environment-variables).

### Recipe 2 (lines 49-84) — `config:` shape

**Same fix as configuration.md:** flip dict-keyed form to
list-of-objects.

### Recipe 3 (lines 86-131) — `config:` shape + import

**Two corrections:**
1. Flip `config:` shape (one entry: `opacity_path`).
2. Drop `from toolbase.setup import SetupContext` import; drop
   `: SetupContext` annotations.

### Recipe 4 (lines 133-186) — Drop import + tighten

**Corrections:**
1. Drop the `SetupContext` import + annotation.
2. The snippet is otherwise correct against shipped behavior.

### Recipe 5 (lines 188-259) — Drop import

Same as Recipe 4. The snippet's logic is correct.

### Recipe 6 (lines 261-314) — Drop import + tighten

Same as Recipe 4. The CUDA-detection logic is the kind of "raw
Python in setup.py" the docs should celebrate.

### Recipe 7 (lines 316-374) — `config:` shape

Same fix as elsewhere. The Recipe 7 tool code is correct.

### "Starter template" section (lines 376-411)

**Drift:** Doesn't match the shipped `setup.py.template` 3C-3
just landed.

**Suggested replacement:** Just point at the new template:

```markdown
## Starter template

When you run `toolbase init my-toolkit --with-setup`, you get
a working `setup.py` scaffold with commented-out examples for
prompts, downloads, and config writes. The template is at
`toolbase/templates/setup.py.template` in the package; pull
the latest version with a fresh `init`.
```

### "Full example: ASTER" (lines 413-523) — `config:` shape + import

**Corrections:**
1. Flip the `config:` block to list-of-objects.
2. Drop the `SetupContext` import + annotations in setup.py.

The example is otherwise structurally correct against shipped
behavior — except for one detail: `environment: { python: "3.12" }`
isn't a real top-level field; the toolkit's `python_version:
"3.12"` is. Replace those four lines with:

```yaml
python_version: "3.12"
```

## Snippets that pass as-is

For completeness, here are the snippets that don't need changes:

`configuration.md`:
- The "Output" snippet (lines 130-136) — `ctx.info/warn/etc.`
  match shipped behavior exactly.
- The "Input" snippet (lines 140-158) — all prompt method
  signatures match shipped.
- The "Useful paths" table (lines 188-192) — paths match.
- The "Raw Python" prose (line 196) — accurate.

`recipes.md`:
- Recipe 1's `env_vars:` block — needs full rewrite (above).
- Recipe 2's tool code (lines 71-82) — accurate.
- Recipe 7's tool code (lines 332-371) — accurate.
- The "What declarative does NOT do" list — accurate.

## Manager input received — both sections resolved

(Both items below were flagged for manager input on first draft;
both are now answered. Canonical copy lives inline in the
relevant sections above. This block kept for traceability.)

1. **`env_vars:` rewrite** in both pages — **resolved**. Manager's
   answer: "the shipped path for secrets is `type: secret`; the
   `env_vars:` declarative field doesn't exist and won't." Canonical
   replacement copy for `configuration.md` is now in
   "Section 'Environment variables' — lines 65-77 — FULL REWRITE"
   above (use verbatim). Canonical replacement copy for `recipes.md`
   Recipe 1 is in "Recipe 1 (lines 15-47) — FULL REWRITE" above
   (use verbatim).

2. **The "On install" UX walkthrough** — **resolved**. Manager
   confirmed the two-sentence prose update is right; Frontend can
   apply directly. Replace "ASTER requires additional setup"
   framing with "Tier-1 prompts run inline; if the toolkit also
   has a setup.py, it runs after."

## How to apply

The brief at
`MESSAGES_TO_AGENTS/2026-05-06_to_frontend__3C_docs_update.md`
points Frontend at this audit. **All blockers resolved as of
2026-05-06.** Apply order:

1. Fix the `config:` shape everywhere (mechanical sed-like
   change). Catches ~70% of corrections.
2. Drop `from toolbase.setup import SetupContext` imports and
   the `: SetupContext` annotations. ~20% more.
3. Update prose around `.env` mentions to point at
   `~/.toolbase/config/<toolkit>.yaml`.
4. Add the two new sections (authoring distinction +
   troubleshooting → setup.py errors).
5. Apply the two manager-adjudicated rewrites verbatim:
   - `configuration.md` "Secrets and environment variables"
     section (replaces old "Environment variables").
   - `recipes.md` Recipe 1 (replaces old `env_vars:` recipe).
6. Apply the "On install" prose update — one-line shift from
   "ASTER requires additional setup" framing to "Tier-1 prompts
   run inline; if the toolkit also has a setup.py, it runs
   after."

When changes ship, ping me with a list of files modified and
I'll re-test all snippets against the running CLI.

— Package Agent
