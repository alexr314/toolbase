"""
Configuration paths for Toolbase.

The CLI persists per-toolkit publish tokens, installed toolkits, and execution
logs under ~/.toolbase/. The directories below are the canonical locations
for those, and are created on import so the rest of the package can assume
they exist.

    ~/.toolbase/
    ├── <toolkit_name>/token     # publish tokens (mode 0600)
    ├── toolkits/<toolkit_name>/ # installed toolkits (with .stk_meta.json)
    └── logs/                    # execution logs (populated by serve)
"""

from pathlib import Path

CONFIG_DIR = Path.home() / ".toolbase"
TOOLKITS_DIR = CONFIG_DIR / "toolkits"
LOGS_DIR = CONFIG_DIR / "logs"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
TOOLKITS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
