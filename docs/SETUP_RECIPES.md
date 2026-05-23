# Toolkit Setup Recipes

> **ASPIRATIONAL — Phase 3C, not yet implemented.** These recipes use `SetupContext`, `setup.py`, and the `config:` block in toolkit.yaml — none of which exist yet. They show the *intended* author experience. Toolkits that use these patterns today will install but be skipped at serve time with a "setup not yet run" message. Once Phase 3C ships, this banner comes off.

**Copy-paste solutions for common setup patterns.**

This guide shows you how to handle typical configuration needs in your toolkit. Start with the simplest pattern that works - you can always add complexity later.

---

## Decision Tree

**What does your toolkit need?**

```
Just API keys or env vars?
  → Recipe 1 (declarative only)

Simple paths or numbers?
  → Recipe 2 (declarative only)

Need to validate that a file exists?
  → Recipe 3 (setup.py validate only)

Need to download something?
  → Recipe 4 (setup.py with download)

Multiple setup options (download vs provide path)?
  → Recipe 5 (setup.py with choice)

Complex detection (CUDA, system libs)?
  → Recipe 6 (setup.py with custom logic)

Want to accept configuration from tools via get_config()?
  → Recipe 7 (runtime config access)
```

---

## Recipe 1: API Keys Only

**Your toolkit just needs an environment variable set.**

### toolkit.yaml

```yaml
name: my-toolkit
version: 1.0.0

env_vars:
  required:
    - OPENAI_API_KEY
  optional:
    - OPENAI_ORG_ID
```

### No setup.py needed

Toolbase will:
1. On `install`: warn that `OPENAI_API_KEY` is required
2. On `serve`: check that the env var exists in the user's environment
3. If missing: skip the toolkit with a clear message

### Tool code

```python
import os
from orchestral import define_tool
import json

@define_tool
def ask_gpt(prompt: str) -> str:
    """Ask GPT a question."""
    api_key = os.environ['OPENAI_API_KEY']  # Already validated
    # ... call OpenAI API ...
    return json.dumps(result)
```

---

## Recipe 2: Simple Paths and Values

**You need a path and maybe a numeric config.**

### toolkit.yaml

```yaml
name: my-toolkit
version: 1.0.0

config:
  data_dir:
    type: path
    description: "Where to store outputs"
    default: "~/.my-toolkit/data"

  max_workers:
    type: integer
    description: "Number of parallel workers"
    default: 4
```

### No setup.py needed

Toolbase will prompt for these values on install. User can skip to use defaults.

### Tool code

```python
from orchestral import define_tool
from toolbase.runtime import get_config
import json

@define_tool
def process_data(input_file: str) -> str:
    """Process input file."""
    data_dir = get_config('data_dir')      # From config
    workers = get_config('max_workers')    # From config

    # Use them
    result = process(input_file, output_dir=data_dir, n_workers=workers)
    return json.dumps(result)
```

---

## Recipe 3: Validate a File Exists

**You collect a path declaratively, but need to check it's actually usable.**

### toolkit.yaml

```yaml
name: my-toolkit
version: 1.0.0

config:
  opacity_path:
    type: path
    description: "Path to opacity data files"
    required: true

setup_script: true  # Enable setup.py
```

### setup.py

```python
from pathlib import Path
from toolbase.setup import SetupContext

def setup(ctx: SetupContext) -> bool:
    """Setup hook - runs after declarative prompts."""
    # The declarative layer already collected opacity_path
    # We just validate here
    return validate(ctx)


def validate(ctx: SetupContext) -> bool:
    """Called by `toolbase serve` to check readiness."""
    path_str = ctx.get_config('opacity_path')

    if not path_str:
        ctx.error("opacity_path not configured")
        ctx.hint("Run: toolbase setup my-toolkit")
        return False

    path = Path(path_str).expanduser()

    if not path.exists():
        ctx.error(f"Path does not exist: {path}")
        return False

    # Check for expected files
    h5_files = list(path.glob("*.h5"))
    if len(h5_files) < 10:
        ctx.error(f"Expected 10+ .h5 files in {path}, found {len(h5_files)}")
        return False

    return True
```

---

## Recipe 4: Download a Data File

**Your toolkit ships with a script to download the data it needs.**

### toolkit.yaml

```yaml
name: my-toolkit
version: 1.0.0

setup_script: true
```

### setup.py

```python
from pathlib import Path
from toolbase.setup import SetupContext

DATA_URL = "https://data.toolbase.org/my-toolkit/dataset_v1.tar.gz"
DATA_SHA256 = "abc123def456..."  # Optional: verify download


def setup(ctx: SetupContext) -> bool:
    dest = ctx.data_dir / 'dataset'

    if dest.exists() and any(dest.iterdir()):
        if not ctx.confirm("Dataset already exists. Re-download?", default=False):
            ctx.set_config('dataset_path', str(dest))
            return True

    ctx.info("Downloading dataset (~500MB)...")
    ctx.download(
        url=DATA_URL,
        destination=dest,
        description="Dataset",
        size_hint="500MB",
        extract=True,        # Auto-extract .tar.gz
        sha256=DATA_SHA256,
    )

    ctx.set_config('dataset_path', str(dest))
    ctx.success(f"Dataset installed at {dest}")
    return True


def validate(ctx: SetupContext) -> bool:
    path = ctx.get_config('dataset_path')
    if not path or not Path(path).exists():
        ctx.error("Dataset not found")
        ctx.hint("Run: toolbase setup my-toolkit")
        return False
    return True
```

---

## Recipe 5: Download OR Provide Path (Multiple Options)

**User can either auto-download data or point to existing data.**

### toolkit.yaml

```yaml
name: aster
version: 1.0.0

setup_script: true
```

### setup.py

```python
from pathlib import Path
from toolbase.setup import SetupContext

OPACITY_URL = "https://data.toolbase.org/aster/opacity_v2.tar.gz"


def setup(ctx: SetupContext) -> bool:
    # Already configured?
    existing = ctx.get_config('opacity_path')
    if existing and Path(existing).expanduser().exists():
        ctx.info(f"Opacity data already configured: {existing}")
        if not ctx.confirm("Reconfigure?", default=False):
            return validate(ctx)

    choice = ctx.choice(
        "How would you like to set up opacity data?",
        [
            ("download", "Download automatically (~2.3GB)"),
            ("path", "I have the data - let me provide the path"),
            ("skip", "Skip for now"),
        ]
    )

    if choice == "download":
        return _download_opacity(ctx)
    elif choice == "path":
        return _prompt_opacity_path(ctx)
    else:
        ctx.warn("Setup skipped. ASTER will not be available until configured.")
        return False


def _download_opacity(ctx: SetupContext) -> bool:
    dest = ctx.data_dir / 'opacity'
    ctx.download(
        url=OPACITY_URL,
        destination=dest,
        description="Opacity data",
        size_hint="2.3GB",
        extract=True,
    )
    ctx.set_config('opacity_path', str(dest))
    return validate(ctx)


def _prompt_opacity_path(ctx: SetupContext) -> bool:
    path = ctx.prompt_path(
        "Enter path to opacity data:",
        must_exist=True,
    )
    ctx.set_config('opacity_path', str(path))
    return validate(ctx)


def validate(ctx: SetupContext) -> bool:
    path_str = ctx.get_config('opacity_path')
    if not path_str:
        ctx.error("opacity_path not configured")
        return False

    path = Path(path_str).expanduser()
    if not path.exists():
        ctx.error(f"Opacity path not found: {path}")
        return False

    h5_files = list(path.glob("*.h5"))
    if len(h5_files) < 10:
        ctx.error(f"Expected 10+ .h5 files in {path}, found {len(h5_files)}")
        ctx.hint("Re-run setup or check your data directory")
        return False

    return True
```

---

## Recipe 6: Custom Detection (CUDA, System Libs)

**You need to detect system capabilities.**

### toolkit.yaml

```yaml
name: gpu-toolkit
version: 1.0.0

setup_script: true
```

### setup.py

```python
import subprocess
import shutil
from toolbase.setup import SetupContext


def setup(ctx: SetupContext) -> bool:
    # Check for CUDA
    if shutil.which('nvcc'):
        try:
            result = subprocess.run(
                ['nvcc', '--version'],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                version = _parse_cuda_version(result.stdout)
                ctx.success(f"CUDA detected: {version}")
                ctx.set_config('compute_mode', 'gpu')
                ctx.set_config('cuda_version', version)
                return True
        except subprocess.TimeoutExpired:
            pass

    # CUDA not available - ask user
    ctx.warn("CUDA not detected")
    if ctx.confirm("Continue in CPU-only mode?", default=True):
        ctx.set_config('compute_mode', 'cpu')
        return True

    ctx.error("GPU is required for this toolkit")
    ctx.hint("Install CUDA: https://developer.nvidia.com/cuda-downloads")
    return False


def validate(ctx: SetupContext) -> bool:
    mode = ctx.get_config('compute_mode')
    if mode not in ('gpu', 'cpu'):
        ctx.error("compute_mode not configured")
        return False
    return True


def _parse_cuda_version(nvcc_output: str) -> str:
    for line in nvcc_output.split('\n'):
        if 'release' in line:
            # "Cuda compilation tools, release 11.8, V11.8.89"
            return line.split('release')[1].split(',')[0].strip()
    return "unknown"
```

---

## Recipe 7: Stateful Tools (base_directory pattern)

**Your tools need config injected that the agent doesn't provide.**

This is the classic pattern for tools like `WriteFile(base_directory)` where the working directory is set by the toolkit, not the agent.

### toolkit.yaml

```yaml
name: my-toolkit
version: 1.0.0

config:
  base_directory:
    type: path
    description: "Working directory for all file operations"
    default: "~/my-toolkit-workspace"
```

### Tool code

```python
from pathlib import Path
from orchestral import define_tool
from toolbase.runtime import get_config
import json


@define_tool
def write_file(relative_path: str, content: str) -> str:
    """
    Write content to a file.

    Args:
        relative_path: Path relative to workspace
        content: Text content to write
    """
    # base_directory is a StateField - from config, not from agent
    base_dir = Path(get_config('base_directory')).expanduser()

    # Agent only provides relative paths
    full_path = base_dir / relative_path

    # Safety: ensure we're writing inside base_dir (no ../ escapes)
    full_path = full_path.resolve()
    base_dir = base_dir.resolve()
    if not str(full_path).startswith(str(base_dir)):
        return json.dumps({"error": "Path escapes workspace"})

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)

    return json.dumps({"written": str(full_path.relative_to(base_dir))})


@define_tool
def read_file(relative_path: str) -> str:
    """Read a file from the workspace."""
    base_dir = Path(get_config('base_directory')).expanduser()
    full_path = (base_dir / relative_path).resolve()

    if not str(full_path).startswith(str(base_dir.resolve())):
        return json.dumps({"error": "Path escapes workspace"})

    content = full_path.read_text()
    return json.dumps({"content": content})
```

**Why this matters:** The agent sees `write_file(relative_path, content)` - clean and simple. It doesn't need to know about `base_directory`. The toolkit's config determines where files actually go.

---

## Anti-Patterns

Avoid these - they break consistency and break the user experience:

### Don't use `input()` in setup.py

```python
# Bad - inconsistent styling, no validation helpers
def setup(ctx):
    name = input("Enter name: ")

# Good - consistent UI, validation built-in
def setup(ctx):
    name = ctx.prompt("Enter name:")
```

### Don't write to .env directly

```python
# Bad - bypasses Toolbase's config system
def setup(ctx):
    with open(ctx.toolkit_path / '.env', 'a') as f:
        f.write("MY_VAR=value\n")

# Good - use the config API
def setup(ctx):
    ctx.set_config('my_var', 'value')
```

### Don't hardcode paths in tools

```python
# Bad - not portable, not configurable
@define_tool
def process():
    data = load_data('/Users/alex/data/file.csv')

# Good - configurable, portable
@define_tool
def process():
    data_path = get_config('data_path')
    data = load_data(data_path)
```

### Don't skip validation

```python
# Bad - user gets cryptic error at runtime
def validate(ctx):
    return True

# Good - user gets helpful error at serve time
def validate(ctx):
    path = ctx.get_config('data_path')
    if not path:
        ctx.error("data_path not set")
        ctx.hint("Run: toolbase setup my-toolkit")
        return False
    if not Path(path).exists():
        ctx.error(f"data_path does not exist: {path}")
        return False
    return True
```

### Don't crash on setup failure

```python
# Bad - user sees ugly traceback
def setup(ctx):
    response = requests.get(url)  # Network error → traceback
    response.raise_for_status()

# Good - caught and reported nicely
def setup(ctx):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        ctx.error(f"Could not reach download server: {e}")
        ctx.hint("Check your internet connection and try again")
        return False
```

---

## Template: toolbase init

When a user runs `toolbase init my-toolkit`, they get this starter `setup.py`:

```python
"""
Setup script for my-toolkit.

This file is called by Toolbase to configure your toolkit
before it can be served.

- `setup(ctx)` runs when user does: toolbase setup my-toolkit
- `validate(ctx)` runs before: toolbase serve
"""
from toolbase.setup import SetupContext


def setup(ctx: SetupContext) -> bool:
    """Interactive setup. Return True if setup succeeded."""

    # TODO: Collect configuration from user.
    # Example:
    # path = ctx.prompt_path("Enter data directory:")
    # ctx.set_config('data_path', str(path))

    return validate(ctx)


def validate(ctx: SetupContext) -> bool:
    """
    Check if the toolkit is ready to serve.
    Return True if all required config is valid.
    """

    # TODO: Validate required config.
    # Example:
    # if not ctx.get_config('data_path'):
    #     ctx.error("data_path not configured")
    #     ctx.hint("Run: toolbase setup my-toolkit")
    #     return False

    return True
```

---

## Full Example: ASTER

For reference, here's what ASTER's complete setup will look like:

### toolkit.yaml

```yaml
name: aster
version: 1.0.0
category: astro
description: "Agentic Science Toolkit for Exoplanet Research"
author: "Alex Roman"
license: "MIT"

environment:
  python: "3.12"

config:
  max_workers:
    type: integer
    description: "Parallel workers for computation"
    default: 4

  base_directory:
    type: path
    description: "Workspace for outputs"
    default: "~/.aster/workspace"

setup_script: true
```

### setup.py

```python
"""ASTER setup - handles opacity data download and workspace config."""
from pathlib import Path
from toolbase.setup import SetupContext

OPACITY_URL = "https://data.toolbase.org/aster/opacity_v2.tar.gz"
OPACITY_SHA256 = "..."  # Fill in actual hash


def setup(ctx: SetupContext) -> bool:
    # Workspace - create if needed
    workspace = Path(ctx.get_config('base_directory')).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    ctx.info(f"Workspace: {workspace}")

    # Opacity data
    existing = ctx.get_config('opacity_path')
    if existing and _opacity_valid(Path(existing).expanduser()):
        ctx.info(f"Opacity data already configured: {existing}")
        return True

    choice = ctx.choice(
        "ASTER needs opacity data (~2.3GB). How would you like to set it up?",
        [
            ("download", "Download automatically"),
            ("path", "I have the data - let me provide the path"),
            ("skip", "Skip for now (ASTER won't work until configured)"),
        ]
    )

    if choice == "download":
        dest = ctx.data_dir / 'opacity'
        ctx.download(
            url=OPACITY_URL,
            destination=dest,
            description="Opacity data",
            size_hint="2.3GB",
            extract=True,
            sha256=OPACITY_SHA256,
        )
        ctx.set_config('opacity_path', str(dest))

    elif choice == "path":
        path = ctx.prompt_path("Path to opacity data:", must_exist=True)
        ctx.set_config('opacity_path', str(path))

    else:
        return False

    return validate(ctx)


def validate(ctx: SetupContext) -> bool:
    # Check opacity data
    opacity_str = ctx.get_config('opacity_path')
    if not opacity_str:
        ctx.error("opacity_path not configured")
        ctx.hint("Run: toolbase setup aster")
        return False

    if not _opacity_valid(Path(opacity_str).expanduser()):
        ctx.error(f"Opacity data at {opacity_str} is invalid or incomplete")
        ctx.hint("Re-run: toolbase setup aster")
        return False

    # Check workspace exists
    workspace = Path(ctx.get_config('base_directory')).expanduser()
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    return True


def _opacity_valid(path: Path) -> bool:
    if not path.exists():
        return False
    h5_files = list(path.glob("*.h5"))
    return len(h5_files) >= 10
```

This sets the standard other toolkit authors can copy.

---

## See Also

- [SETUP_SYSTEM_SPEC.md](SETUP_SYSTEM_SPEC.md) - Full specification
- [TOOLKIT_FORMAT_GUIDE.md](../../TOOLKIT_FORMAT_GUIDE.md) - Overall toolkit structure
