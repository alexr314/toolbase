"""
Configuration paths and API defaults for Toolbase.

The CLI persists per-toolkit publish tokens, installed toolkits, and execution
logs under ~/.toolbase/. The directories below are the canonical locations
for those, and are created on import so the rest of the package can assume
they exist.

    ~/.toolbase/
    ├── <toolkit_name>/token     # publish tokens (mode 0600)
    ├── toolkits/<toolkit_name>/ # installed toolkits (with .tb_meta.json)
    └── logs/                    # execution logs (populated by serve)
"""

import os
from pathlib import Path

# Default API base URL.  Override at run-time via the ``TOOLBASE_API_URL``
# environment variable (used in tests and staging environments).
# Do NOT change this value without a separate cutover task — it points at
# the live backend.  (Cut over from the legacy ``api.scitoolkit.org`` host on
# 2026-05-23; that host still serves in parallel for now, but this is the
# canonical one.)
DEFAULT_API_URL = "https://api.toolbase-ai.com"


def _api_url() -> str:
    """Return the effective API base URL.

    Checks ``TOOLBASE_API_URL`` first so tests and staging can override
    without touching code.  Falls back to ``DEFAULT_API_URL``.
    """
    return os.environ.get("TOOLBASE_API_URL") or DEFAULT_API_URL

CONFIG_DIR = Path.home() / ".toolbase"
TOOLKITS_DIR = CONFIG_DIR / "toolkits"
LOGS_DIR = CONFIG_DIR / "logs"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
TOOLKITS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
