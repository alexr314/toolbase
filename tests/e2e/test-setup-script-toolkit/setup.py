"""Phase 3C-2 e2e setup.py.

Exercises the full SetupContext API: prompts, downloads (against a
localhost mock server provided by the harness), set_config write-
through, and a validate(ctx) that reads stored state.
"""

from __future__ import annotations

import os
from pathlib import Path


def setup(ctx):
    ctx.info("running setup script")

    # Pull a value the Tier-1 declarative pass should have stored.
    api_key = ctx.get_config("api_key")
    ctx.info(f"api_key from Tier-1: {'<set>' if api_key else '<unset>'}")

    # Prompt for something with a default; in --no-input mode we get
    # the default. In TTY mode we'd get the real answer.
    workers = ctx.prompt_int(
        "Number of background workers?", default=4, min=1, max=64,
    )
    ctx.set_config("worker_count", workers)
    ctx.info(f"persisted worker_count={workers}")

    # Confirm-style prompt with a default. In --no-input mode (skip),
    # we use the default.
    use_gpu = ctx.confirm("Enable GPU mode?", default=False)
    ctx.set_config("use_gpu", use_gpu)

    # Choice prompt — three options. In skip mode we get the first.
    mode = ctx.choice(
        "How would you like to fetch data?",
        [
            ("download", "Download from a remote URL"),
            ("local", "I have it locally; provide a path"),
            ("skip", "Skip"),
        ],
    )
    ctx.set_config("data_mode", mode)

    # Optional download. The harness provides DOWNLOAD_URL via env;
    # if it's set, we download a small file. If not, we skip the
    # download portion (the harness uses this to test both paths).
    download_url = os.environ.get("STK_E2E_DOWNLOAD_URL")
    if download_url:
        dest = ctx.data_dir / "downloaded-blob.bin"
        ctx.info(f"downloading {download_url} → {dest}")
        result = ctx.download(download_url, dest, description="test data")
        ctx.set_config("downloaded_bytes", result.stat().st_size)
        ctx.info(f"download complete: {result.stat().st_size} bytes")
    else:
        ctx.info("STK_E2E_DOWNLOAD_URL not set; skipping download")
        ctx.set_config("downloaded_bytes", 0)

    ctx.success("setup complete")
    return True


def validate(ctx):
    """Quick check called at every serve startup. Read-only.

    Pass iff worker_count and api_key are set.
    """
    if not ctx.get_config("api_key"):
        return False
    if ctx.get_config("worker_count") is None:
        return False
    return True
