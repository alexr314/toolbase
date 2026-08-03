"""Synthetic calculator setup — exercises the Phase 3C-2 download flow.

Models a toolkit whose install ships a large reference dataset — a
constants table here — at 10 KB instead of the multi-GB scale the flow
was designed for. Same surface: `choice` between download and
provide-path, SHA256-verified extract into ``ctx.data_dir``, and a
``set_config`` writeback for ``constants_path``, against a localhost
mock server. The harness asserts the extracted layout matches what the
toolkit's tools would expect.

The harness sets STK_E2E_CONSTANTS_URL and STK_E2E_CONSTANTS_SHA256
in the environment so this script is self-contained against the
fixture server.
"""

from __future__ import annotations

import os
from pathlib import Path


def setup(ctx):
    ctx.info("Synthetic calculator setup starting...")

    # Workspace already declared in config:; the Tier-1 pass writes
    # it. Confirm it exists or create it.
    workspace = Path(ctx.get_config("workspace") or
                     str(ctx.data_dir / "workspace")).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    ctx.set_config("workspace", str(workspace))
    ctx.info(f"Workspace: {workspace}")

    # Constants flow: choice between download and provide-path.
    existing = ctx.get_config("constants_path")
    if existing and Path(existing).expanduser().exists():
        ctx.info(f"Constants data already configured: {existing}")
        return validate(ctx)

    choice = ctx.choice(
        "How would you like to set up constants data?",
        [
            ("download", "Download automatically"),
            ("path", "I have the data — let me provide the path"),
            ("skip", "Skip for now (toolkit unavailable until configured)"),
        ],
    )

    if choice == "download":
        return _download_constants(ctx)
    elif choice == "path":
        return _prompt_constants_path(ctx)
    else:
        ctx.warn("Setup skipped. Toolkit will refuse to serve until configured.")
        return False


def _download_constants(ctx):
    url = os.environ.get("STK_E2E_CONSTANTS_URL")
    sha = os.environ.get("STK_E2E_CONSTANTS_SHA256")
    if not url:
        ctx.error("STK_E2E_CONSTANTS_URL not set — harness must set this")
        return False

    dest = ctx.data_dir / "constants"
    ctx.info(f"Downloading constants data → {dest}")
    ctx.download(
        url=url,
        destination=dest,
        description="Constants data (synthetic)",
        size_hint="2.3 GB",  # cosmetic — real fixture is much smaller
        extract=True,
        sha256=sha or None,
    )
    ctx.set_config("constants_path", str(dest))
    ctx.success("Constants data installed.")
    return validate(ctx)


def _prompt_constants_path(ctx):
    path = ctx.prompt_path(
        "Path to constants data:", must_exist=True,
    )
    if path is None:
        ctx.warn("No path provided; deferring constants_path setup.")
        return False
    ctx.set_config("constants_path", str(path))
    return validate(ctx)


def validate(ctx):
    """Quick check called at every serve startup. Read-only.

    Pass iff workspace and constants_path are set and the latter
    contains the expected sentinel file the harness drops into the
    archive.
    """
    if not ctx.get_config("api_key"):
        return False

    workspace = ctx.get_config("workspace")
    if not workspace:
        return False

    constants_path_str = ctx.get_config("constants_path")
    if not constants_path_str:
        return False

    op = Path(constants_path_str).expanduser()
    if not op.exists():
        return False
    # The harness's archive contains a sentinel file `manifest.txt`
    # so we can prove the extract reached the right place.
    if not (op / "manifest.txt").exists():
        return False

    return True
