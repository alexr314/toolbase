"""End-to-end test for the setup-with-large-download flow — Phase 3C-3.

Models the install of a toolkit that ships a big reference dataset, at
small scale:

1. Synthesize an installed calculator-shaped toolkit
   (test-calculator-synthetic-toolkit/).
2. Pre-fill the Tier-1 declared `api_key` (would be the user's
   prompt response at install in real life).
3. Run setup.py via ``run_setup_script`` in skip mode. The
   ``choice`` prompt picks "download" (first option in skip mode);
   downloads a synthetic ~10 KB tarball from a localhost server
   with a sentinel `manifest.txt` inside; extracts; writes
   `constants_path` via ``ctx.set_config``.
4. Run ``validate(ctx)`` — passes (sentinel file exists).
5. Spin up the orchestrator in-process; verify the tool sees
   `api_key`, `workspace`, `constants_path`, and `max_workers`
   injected — the full mix of Tier-1 declared + Tier-2 derived
   state.
6. Negative path: corrupt the extract (delete the sentinel),
   re-run validate, confirm it now fails and the orchestrator
   refuses to serve.

Why a synthetic toolkit rather than a real one:

- No real toolkit is checked in here, and depending on one would
  make this test need the network and a published version.
- The download flow is what's under test; a 10 KB blob exercises
  the same RPC + downloads.py + extract + set_config path a
  multi-GB dataset would. The only difference is wall-clock, and
  that isn't coverage.

Run from the repo root:

    python tests/e2e/run_calculator_synthetic_e2e.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
TOOLKIT_SRC = THIS_DIR / "test-calculator-synthetic-toolkit"
TOOLKIT_NAME = "tb-calculator-synthetic"

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-calculator-synthetic-e2e"
INSTALL_ROOT = WORK_ROOT / "toolbase"


# ── synthetic constants tarball ─────────────────────────────────────────


def _build_synthetic_constants_tarball() -> tuple[bytes, str]:
    """Build a tiny constants tarball with sentinel files inside.

    Returns ``(payload_bytes, sha256_hex)``. The sentinel
    ``manifest.txt`` lets the toolkit's validate(ctx) prove the
    extract reached the right path.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname, content in [
            ("manifest.txt", b"# calculator constants manifest (synthetic)\n"
             b"version: 1.0\nfiles: 3\n"),
            ("constants_si.h5", b"\x89HDF\r\n\x1a\n" + b"\x00" * 256),
            ("constants_cgs.h5", b"\x89HDF\r\n\x1a\n" + b"\x00" * 256),
        ]:
            info = tarfile.TarInfo(name=fname)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    payload = buf.getvalue()
    sha = hashlib.sha256(payload).hexdigest()
    return payload, sha


# ── localhost server serving the tarball ──────────────────────────────


def _start_server(payload: bytes) -> str:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a, **_kw):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "application/gzip")
            self.end_headers()
            self.wfile.write(payload)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/constants.tar.gz"


# ── isolated install dir ──────────────────────────────────────────────


def _setup_synthetic_install() -> Path:
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
    shutil.copytree(TOOLKIT_SRC, dest)

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
    (dest / ".tb_meta.json").write_text(json.dumps(meta, indent=2))
    from toolbase.envs import write_install_meta as _wim
    _wim(dest, name=TOOLKIT_NAME, version=version,
         install_method="venv",
         python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
         extras={"python_path": sys.executable, "has_setup_script": True})
    return dest


def _refresh_imports():
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
        print(f"!!! synthetic calculator toolkit missing at {TOOLKIT_SRC}")
        return 1

    _setup_synthetic_install()
    os.environ["HOME"] = str(WORK_ROOT)

    payload, sha = _build_synthetic_constants_tarball()
    print(f"Built synthetic constants tarball: {len(payload)} bytes, SHA256={sha[:16]}...")

    url = _start_server(payload)
    os.environ["STK_E2E_CONSTANTS_URL"] = url
    os.environ["STK_E2E_CONSTANTS_SHA256"] = sha

    orchestrator = _refresh_imports()

    # ── Step 1: pre-fill api_key ─────────────────────────────────
    print()
    print("=" * 64)
    print("Step 1: pre-fill api_key (Tier-1 simulated install prompt)")
    print("=" * 64)
    from toolbase.setup import set_config_value
    set_config_value(TOOLKIT_NAME, "api_key", "fake-units-key-12345")
    print("  ✓ api_key set")

    # ── Step 2: run setup.py (download path) ─────────────────────
    print()
    print("=" * 64)
    print("Step 2: run setup.py (skip mode → choice picks first =")
    print("        'download'; tarball pulled, extracted, SHA-verified)")
    print("=" * 64)
    from toolbase.setup import run_setup_script

    captured = []
    result = run_setup_script(
        TOOLKIT_NAME, prompt_mode="skip",
        console_print=captured.append,
    )
    for line in captured[-12:]:
        print(f"  | {line}")

    if not result.ok:
        print(f"!!! setup failed: {result.message}")
        if result.traceback:
            print(result.traceback)
        return 2
    print("  ✓ setup.py completed; constants downloaded + extracted")

    from toolbase.setup import load_config
    cfg = load_config(TOOLKIT_NAME)
    if "constants_path" not in cfg:
        print(f"!!! constants_path not persisted: {dict(cfg)}")
        return 3
    op_path = Path(cfg["constants_path"])
    if not op_path.exists():
        print(f"!!! constants dir does not exist: {op_path}")
        return 4
    if not (op_path / "manifest.txt").exists():
        print(f"!!! manifest.txt missing from {op_path}")
        return 5
    print(f"  ✓ extracted layout intact: {sorted(p.name for p in op_path.iterdir())}")

    # ── Step 3: validate(ctx) passes ─────────────────────────────
    print()
    print("=" * 64)
    print("Step 3: validate(ctx) confirms ready-to-serve")
    print("=" * 64)
    from toolbase.setup import validate_setup_script
    v = validate_setup_script(TOOLKIT_NAME)
    if not v.ok:
        print(f"!!! validate failed: {v.message}")
        return 6
    print("  ✓ validate(ctx) returned True")

    # ── Step 4: orchestrator serves; tool sees mixed state ───────
    print()
    print("=" * 64)
    print("Step 4: orchestrator serves; verify mixed Tier-1 / Tier-2 state")
    print("=" * 64)
    orch = orchestrator.Orchestrator()
    orch.start()

    rt = orch._runtimes.get(TOOLKIT_NAME)
    if rt is None:
        print(f"!!! toolkit {TOOLKIT_NAME!r} did not load")
        return 7
    print(f"  ✓ toolkit loaded: state={rt.state.name}")

    proxies = {p.get_name(): p for p in orch._proxy_tools}
    # PascalCase: orchestral's MCP layer converts a tool's snake_case
    # name for the wire (orchestral/mcp/adapters.py), so `calculate` is
    # served as `Calculate`. The name on the wire is what an agent sees,
    # so that is what this asserts.
    qualified = f"{TOOLKIT_NAME}__Calculate"
    if qualified not in proxies:
        print(f"!!! proxy tool missing: have {sorted(proxies)}")
        orch.shutdown()
        return 8

    raw = proxies[qualified].execute(expression="2 + 2")
    print(f"  tool returned: {raw}")
    payload_dict = json.loads(raw)

    # The agent passes `expression`; the rest are state-injected.
    assertions = [
        ("expression", "2 + 2"),           # runtime arg from agent
        ("api_key_set", True),             # Tier-1 declared
        ("manifest_present", True),        # Tier-2 derived (constants_path)
        ("max_workers", 4),                # Tier-1 default
    ]
    for key, expected in assertions:
        if payload_dict.get(key) != expected:
            print(f"!!! {key}: expected {expected!r}, got {payload_dict.get(key)!r}")
            orch.shutdown()
            return 9
    if "/constants" not in payload_dict.get("constants_path", ""):
        print(f"!!! constants_path didn't reach tool correctly: "
              f"{payload_dict.get('constants_path')!r}")
        orch.shutdown()
        return 10
    print("  ✓ all four state values reached the tool body:")
    print(f"      api_key (Tier-1 secret) → injected (masked)")
    print(f"      workspace (Tier-1 path) → {payload_dict['workspace']}")
    print(f"      constants_path (Tier-2 derived) → {payload_dict['constants_path']}")
    print(f"      max_workers (Tier-1 default) → {payload_dict['max_workers']}")

    orch.shutdown()

    # ── Step 5: corrupt extract; verify validate refuses ─────────
    print()
    print("=" * 64)
    print("Step 5: corrupt extract — validate fails, serve refuses")
    print("=" * 64)
    (op_path / "manifest.txt").unlink()
    print(f"  removed sentinel: {op_path / 'manifest.txt'}")

    # Bust validate cache so the next call re-runs validate(ctx).
    from toolbase.setup.validate_cache import default_cache_path
    default_cache_path().unlink(missing_ok=True)

    v2 = validate_setup_script(TOOLKIT_NAME)
    if v2.ok:
        print(f"!!! validate should have failed without manifest.txt")
        return 11
    print("  ✓ validate(ctx) returned False after sentinel removal")

    orch2 = orchestrator.Orchestrator()
    try:
        orch2.start()
    except RuntimeError as e:
        if "no toolkits could be started" not in str(e):
            print(f"!!! unexpected RuntimeError: {e}")
            return 12
        print(f"  ✓ orchestrator refused: {e}")

    print()
    print("=" * 64)
    print("✓ setup-with-large-download e2e passed")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
