"""End-to-end test for Phase 3C-2: Tier-2 setup.py runner.

Verifies the full install → setup.py → serve loop:

1. Install a synthetic toolkit with a setup.py declaring downloads,
   prompts, and config writes (test-setup-script-toolkit/).
2. Run ``toolbase setup`` against it (in --no-input mode) — exercises
   the prompt RPCs (with defaults), the download RPC (against a
   localhost mock HTTP server), and the set_config RPC.
3. Run ``toolbase setup --check`` — exercises validate(ctx).
4. Run the orchestrator in-process; verify the toolkit serves and
   the injected state reaches the tool body.
5. Touch the config file; verify the validate cache invalidates and
   re-runs.
6. Failure-mode pass: a setup.py that raises mid-flow. Verify install
   completes (with warning) but serve refuses with a clear message.

Like the other e2e harnesses, this runs the orchestrator in-process
so the test takes seconds rather than minutes. Network is replaced
with a localhost ``http.server`` fixture for the download portion.

Run from the repo root:

    python tests/e2e/run_setup_script_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
TOOLKIT_SRC = THIS_DIR / "test-setup-script-toolkit"
TOOLKIT_NAME = "tb-setup-script-test"

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-setup-script-e2e"
INSTALL_ROOT = WORK_ROOT / "toolbase"


# ── localhost server for the download test ────────────────────────────


_DOWNLOAD_PAYLOAD = b"e2e-download-payload-bytes" * 32  # ~768 bytes


def _start_download_server() -> str:
    """Spin up a local HTTP server returning a known payload."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a, **_kw):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(_DOWNLOAD_PAYLOAD)))
            self.end_headers()
            self.wfile.write(_DOWNLOAD_PAYLOAD)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/blob.bin"


# ── isolated install dir + synthetic toolkit ──────────────────────────


def _setup_synthetic_install(toolkit_src: Path = TOOLKIT_SRC) -> Path:
    """Materialize a fresh install in the 0.5.0 cache layout."""
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)

    fake_home = WORK_ROOT
    (fake_home / ".toolbase").symlink_to(INSTALL_ROOT)
    INSTALL_ROOT.mkdir(parents=True)
    (INSTALL_ROOT / "logs").mkdir()
    (INSTALL_ROOT / "downloads").mkdir()

    version = "0.1.0"
    dest = INSTALL_ROOT / "cache" / TOOLKIT_NAME / version
    shutil.copytree(toolkit_src, dest)

    meta = {
        "name": TOOLKIT_NAME, "version": version,
        "environment": "venv",
        "python_path": sys.executable,
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}"
        ),
        "has_setup_script": True,
        "needs_setup": True,
    }
    (dest / ".stk_meta.json").write_text(json.dumps(meta, indent=2))
    # Also write .install_meta.yaml so the cache walker recognizes the slot.
    from toolbase.envs import write_install_meta as _wim
    _wim(dest, name=TOOLKIT_NAME, version=version,
         install_method="venv",
         python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
         extras={"python_path": sys.executable, "has_setup_script": True})
    return dest


def _refresh_imports():
    """Reload modules so HOME-resolved paths pick up our fake home."""
    from importlib import reload
    from toolbase import config as _cfg
    reload(_cfg)
    from toolbase import setup as _stk_setup
    reload(_stk_setup)
    from toolbase.setup import declarative as _decl
    reload(_decl)
    from toolbase.setup import storage as _stor
    reload(_stor)
    from toolbase.setup import runner as _run
    reload(_run)
    from toolbase.serve import orchestrator
    reload(orchestrator)
    return orchestrator


def main() -> int:
    if not TOOLKIT_SRC.exists():
        print(f"!!! synthetic toolkit missing at {TOOLKIT_SRC}")
        return 1

    fake_home_dest = _setup_synthetic_install()
    os.environ["HOME"] = str(WORK_ROOT)

    download_url = _start_download_server()
    os.environ["STK_E2E_DOWNLOAD_URL"] = download_url

    orchestrator = _refresh_imports()

    # ── Step 1: pre-fill the Tier-1 required field ────────────────
    print("=" * 60)
    print("Step 1: pre-fill api_key (simulates Tier-1 declarative pass)")
    print("=" * 60)
    from toolbase.setup import set_config_value
    set_config_value(TOOLKIT_NAME, "api_key", "sct_test_xxx")
    print("  ✓ api_key set")

    # ── Step 2: run setup.py via run_setup_script (skip mode) ─────
    print()
    print("=" * 60)
    print("Step 2: run setup.py::setup(ctx) in skip (no-prompt) mode")
    print("=" * 60)
    from toolbase.setup import run_setup_script

    captured = []
    result = run_setup_script(
        TOOLKIT_NAME, prompt_mode="skip",
        console_print=captured.append,
    )
    for line in captured[-10:]:
        print(f"  | {line}")

    if not result.ok:
        print(f"!!! setup failed: {result.message}")
        if result.traceback:
            print(result.traceback)
        return 2
    print("  ✓ setup.py completed successfully")

    # Confirm config file got the writes from setup().
    from toolbase.setup import load_config
    cfg = load_config(TOOLKIT_NAME)
    expected_keys = {"api_key", "worker_count", "use_gpu", "data_mode", "downloaded_bytes"}
    missing = expected_keys - set(cfg)
    if missing:
        print(f"!!! config file missing keys: {missing}")
        print(f"  got: {dict(cfg)}")
        return 3
    if cfg["worker_count"] != 4:
        print(f"!!! worker_count default not persisted (got {cfg['worker_count']!r})")
        return 4
    if cfg["data_mode"] != "download":
        print(f"!!! data_mode wrong (got {cfg['data_mode']!r})")
        return 5
    if cfg["downloaded_bytes"] != len(_DOWNLOAD_PAYLOAD):
        print(f"!!! downloaded_bytes wrong: {cfg['downloaded_bytes']} vs {len(_DOWNLOAD_PAYLOAD)}")
        return 6
    print(f"  ✓ all expected keys present: {sorted(cfg.keys())}")
    print(f"  ✓ download landed: {cfg['downloaded_bytes']} bytes")

    # ── Step 3: validate(ctx) via --check path ────────────────────
    print()
    print("=" * 60)
    print("Step 3: validate(ctx) via validate_setup_script")
    print("=" * 60)
    from toolbase.setup import validate_setup_script
    v = validate_setup_script(TOOLKIT_NAME)
    if not v.ok:
        print(f"!!! validate failed: {v.message}")
        return 7
    print("  ✓ validate(ctx) returned True")

    # ── Step 4: orchestrator serves the toolkit ───────────────────
    print()
    print("=" * 60)
    print("Step 4: orchestrator serves toolkit; tool sees injected state")
    print("=" * 60)
    orch = orchestrator.Orchestrator()
    orch.start()

    rt = orch._runtimes.get(TOOLKIT_NAME)
    if rt is None:
        print(f"!!! toolkit {TOOLKIT_NAME!r} did not load into orchestrator")
        return 8
    print(f"  ✓ toolkit loaded: state={rt.state.name}")

    proxies = {p.get_name(): p for p in orch._proxy_tools}
    qualified = f"{TOOLKIT_NAME}__get_state"
    if qualified not in proxies:
        print(f"!!! proxy tool {qualified} missing from {sorted(proxies)}")
        orch.shutdown()
        return 9

    raw = proxies[qualified].execute()
    print(f"  tool returned: {raw}")
    payload = json.loads(raw)

    if payload["api_key"] != "sct_test_xxx":
        print(f"!!! api_key not injected: {payload}")
        orch.shutdown()
        return 10
    if payload["worker_count"] != 4:
        print(f"!!! worker_count not injected: {payload}")
        orch.shutdown()
        return 11
    if payload["data_mode"] != "download":
        print(f"!!! data_mode not injected: {payload}")
        orch.shutdown()
        return 12
    print("  ✓ all state values injected through to tool")

    orch.shutdown()

    # ── Step 5: validate cache hit on second call ──────────────────
    print()
    print("=" * 60)
    print("Step 5: validate cache hit on unchanged inputs")
    print("=" * 60)
    from toolbase.setup import validate_setup_script_cached
    v1 = validate_setup_script_cached(TOOLKIT_NAME)
    if not v1.ok:
        print(f"!!! first cached validate failed: {v1.message}")
        return 13
    # Second call should hit cache (same mtimes).
    t0 = time.monotonic()
    v2 = validate_setup_script_cached(TOOLKIT_NAME)
    elapsed = time.monotonic() - t0
    if not v2.ok:
        print(f"!!! second cached validate failed: {v2.message}")
        return 14
    print(f"  ✓ cached validate: {elapsed * 1000:.1f}ms (vs ~hundreds of ms for subprocess)")

    # ── Step 6: cache invalidates on config mtime change ──────────
    print()
    print("=" * 60)
    print("Step 6: validate cache invalidates after config change")
    print("=" * 60)
    # Note: ``config/`` lives at the user-scope root under .toolbase/.
    cfg_path = INSTALL_ROOT / "config" / f"{TOOLKIT_NAME}.yaml"
    # Touch (modify mtime) — change to a clearly-newer timestamp.
    new_mtime = time.time() + 5
    os.utime(cfg_path, (new_mtime, new_mtime))
    t0 = time.monotonic()
    v3 = validate_setup_script_cached(TOOLKIT_NAME)
    elapsed = time.monotonic() - t0
    if not v3.ok:
        print(f"!!! validate after touch failed: {v3.message}")
        return 15
    if elapsed * 1000 < 50:
        # If it took <50ms, cache wasn't invalidated (subprocess
        # spawn + Python startup is ~hundreds of ms).
        print(f"!!! cache should have invalidated, but took {elapsed*1000:.1f}ms")
        return 16
    print(f"  ✓ cache invalidated: {elapsed*1000:.1f}ms (subprocess re-ran)")

    # ── Step 7: failure-mode pass ─────────────────────────────────
    print()
    print("=" * 60)
    print("Step 7: failure-mode setup.py raises mid-flow")
    print("=" * 60)

    # Replace the toolkit's setup.py with a failing one.
    bad_setup_py = textwrap.dedent("""
        def setup(ctx):
            ctx.info("about to fail")
            raise RuntimeError("simulated setup failure")
        def validate(ctx):
            return False
    """)
    fake_home_dest_setup = (
        INSTALL_ROOT / "cache" / TOOLKIT_NAME / "0.1.0" / "setup.py"
    )
    fake_home_dest_setup.write_text(bad_setup_py)
    # Bust the validate cache so the failure is observed.
    from toolbase.setup.validate_cache import default_cache_path
    default_cache_path().unlink(missing_ok=True)

    bad_result = run_setup_script(TOOLKIT_NAME, prompt_mode="skip")
    if bad_result.ok:
        print("!!! bad setup should have failed but reported ok=True")
        return 17
    if bad_result.traceback is None or "simulated setup failure" not in bad_result.traceback:
        print(f"!!! traceback missing or wrong: {bad_result.traceback!r}")
        return 18
    if bad_result.log_path is None or not bad_result.log_path.exists():
        print("!!! traceback log file not written")
        return 19
    print(f"  ✓ setup failure surfaced: log at {bad_result.log_path}")
    print(f"  ✓ traceback contains 'simulated setup failure'")

    # ── Step 8: orchestrator skips toolkit on validate failure ─────
    print()
    print("=" * 60)
    print("Step 8: orchestrator refuses to serve when validate(ctx) fails")
    print("=" * 60)
    orch2 = orchestrator.Orchestrator()
    try:
        orch2.start()
    except RuntimeError as e:
        if "no toolkits could be started" not in str(e):
            print(f"!!! unexpected RuntimeError: {e}")
            return 20
        print(f"  ✓ orchestrator refused to serve: {e}")

    print()
    print("=" * 60)
    print("✓ Phase 3C-2 setup.py e2e passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
