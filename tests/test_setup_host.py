"""
Integration tests for ``toolbase._setup_host``.

These actually invoke the host as a subprocess (using the same
PYTHONPATH-injected toolbase slice the orchestrator uses) and
exercise the full conversation: hello → go → setup(ctx) calls →
done. The mocked-subprocess tests in ``test_setup_runner.py`` cover
the parent-side pump in isolation; these cover the seam between
parent and child.

We use the runner's public entry point ``run_setup_script`` for the
happy paths, and drop down to ``subprocess.Popen`` directly for the
edge cases where we need to drive the host with hand-crafted RPC
traffic (e.g., to verify protocol-violation behavior).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from toolbase.setup import _rpc
from toolbase.setup.runner import (
    SetupResult, run_setup_script, validate_setup_script,
    _build_subprocess_env,
)


# ── fixtures ──────────────────────────────────────────────────────────


def _write_meta(toolkit_dir: Path):
    """Drop a minimal .stk_meta.json pointing at the dev venv's Python."""
    (toolkit_dir / ".stk_meta.json").write_text(json.dumps({
        "name": toolkit_dir.name,
        "version": "0.1.0",
        "environment": "venv",
        "python_path": sys.executable,
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}"
        ),
    }))


def _write_toolkit_yaml(toolkit_dir: Path, extra: str = ""):
    (toolkit_dir / "toolkit.yaml").write_text(
        f"name: {toolkit_dir.name}\n"
        "version: 0.1.0\n"
        "category: misc\n"
        "description: test\n"
        f"{extra}\n"
    )


def _make_toolkit(
    base: Path, name: str, *,
    setup_py: str = "",
    extra_yaml: str = "",
):
    """Create a synthetic installed-toolkit directory."""
    tdir = base / name
    tdir.mkdir(parents=True, exist_ok=True)
    _write_toolkit_yaml(tdir, extra_yaml)
    _write_meta(tdir)
    if setup_py:
        (tdir / "setup.py").write_text(setup_py)
    # Empty tools/ for completeness, though the setup-host doesn't
    # import from it.
    (tdir / "tools").mkdir(exist_ok=True)
    (tdir / "tools" / "__init__.py").write_text("TOOLS = []\n")
    return tdir


@pytest.fixture
def toolkits_dir(tmp_path, monkeypatch):
    """Isolated toolkits dir; the runner's home-resolution stays
    inside the test's tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".toolbase").mkdir()
    (home / ".toolbase" / "toolkits").mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Clear any cached config-dir resolution.
    import toolbase.config as _cfg
    monkeypatch.setattr(_cfg, "CONFIG_DIR", home / ".toolbase" / "config")
    monkeypatch.setattr(_cfg, "TOOLKITS_DIR", home / ".toolbase" / "toolkits")
    monkeypatch.setattr(_cfg, "LOGS_DIR", home / ".toolbase" / "logs")
    return home / ".toolbase" / "toolkits"


# ── happy paths through the real host subprocess ──────────────────────


def test_real_host_setup_returns_true(toolkits_dir):
    """The simplest possible setup.py: returns True."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-a", setup_py=setup_py)

    result = run_setup_script("kit-a", toolkits_dir=toolkits_dir, prompt_mode="skip")
    assert result.ok is True
    assert result.traceback is None


def test_real_host_setup_returns_none_treated_as_success(toolkits_dir):
    """An author who forgets to return is treated as success — the
    spec is permissive about return values for ergonomics."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            pass
    """)
    _make_toolkit(toolkits_dir, "kit-b", setup_py=setup_py)
    assert run_setup_script("kit-b", toolkits_dir=toolkits_dir, prompt_mode="skip").ok


def test_real_host_setup_returns_false(toolkits_dir):
    """Author signals failure with explicit False."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            return False
    """)
    _make_toolkit(toolkits_dir, "kit-c", setup_py=setup_py)
    result = run_setup_script("kit-c", toolkits_dir=toolkits_dir, prompt_mode="skip")
    assert result.ok is False
    assert result.traceback is None  # clean refusal, not a crash


def test_real_host_setup_uses_ctx_info(toolkits_dir):
    """``ctx.info(...)`` arrives at the parent's console_print sink."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            ctx.info("hello from setup.py")
            ctx.success("all good")
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-d", setup_py=setup_py)

    captured = []
    result = run_setup_script(
        "kit-d", toolkits_dir=toolkits_dir, prompt_mode="skip",
        console_print=captured.append,
    )
    assert result.ok is True
    assert any("hello from setup.py" in p for p in captured)
    assert any("all good" in p for p in captured)
    assert any("[cyan]" in p for p in captured)
    assert any("[green]" in p for p in captured)


def test_real_host_setup_can_read_local_paths(toolkits_dir):
    """``ctx.toolkit_path`` arrives correctly at the subprocess."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            ctx.info(f"toolkit_path={ctx.toolkit_path}")
            ctx.info(f"data_dir={ctx.data_dir}")
            return True
    """)
    tdir = _make_toolkit(toolkits_dir, "kit-e", setup_py=setup_py)

    captured = []
    result = run_setup_script(
        "kit-e", toolkits_dir=toolkits_dir, prompt_mode="skip",
        console_print=captured.append,
    )
    assert result.ok is True
    # Path is preserved through the wire intact (no quoting weirdness).
    assert any(str(tdir) in p for p in captured)


def test_real_host_validate_runs_validate_function(toolkits_dir):
    """``validate(ctx)`` runs and its return value is the result."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            return True
        def validate(ctx):
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-f", setup_py=setup_py)
    assert validate_setup_script("kit-f", toolkits_dir=toolkits_dir).ok is True


def test_real_host_validate_returns_false_when_validate_fails(toolkits_dir):
    setup_py = textwrap.dedent("""
        def setup(ctx):
            return True
        def validate(ctx):
            return False
    """)
    _make_toolkit(toolkits_dir, "kit-g", setup_py=setup_py)
    result = validate_setup_script("kit-g", toolkits_dir=toolkits_dir)
    assert result.ok is False


def test_real_host_validate_short_circuits_when_no_validate_defined(toolkits_dir):
    """A setup.py with no validate(ctx) — passing validate by default
    (the spec's intended semantic for "this toolkit has no quick
    check")."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-h", setup_py=setup_py)
    result = validate_setup_script("kit-h", toolkits_dir=toolkits_dir)
    assert result.ok is True


# ── error paths through the real host subprocess ──────────────────────


def test_real_host_setup_raises_exception_captured_as_traceback(toolkits_dir):
    """An unhandled exception in setup() comes back as a done with a
    traceback. The traceback is written to a log file; result.message
    points at it."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            ctx.info("about to fail")
            raise RuntimeError("kaboom from the toolkit")
    """)
    _make_toolkit(toolkits_dir, "kit-i", setup_py=setup_py)

    result = run_setup_script("kit-i", toolkits_dir=toolkits_dir, prompt_mode="skip")
    assert result.ok is False
    assert result.traceback is not None
    assert "kaboom from the toolkit" in result.traceback
    assert "RuntimeError" in result.traceback
    assert result.log_path is not None and result.log_path.exists()


def test_real_host_no_setup_py_in_setup_mode_fails_loudly(toolkits_dir):
    """A toolkit with no setup.py + a setup-mode invocation: the
    runner detects has_setup=False on hello and refuses with a clear
    message pointing at the docs."""
    _make_toolkit(toolkits_dir, "kit-j")  # no setup.py

    result = run_setup_script("kit-j", toolkits_dir=toolkits_dir, prompt_mode="skip")
    assert result.ok is False
    assert "setup(ctx)" in result.message


def test_real_host_no_setup_py_in_validate_mode_passes_trivially(toolkits_dir):
    """Inverse of the above: no setup.py, validate-mode → trivially passes."""
    _make_toolkit(toolkits_dir, "kit-k")
    result = validate_setup_script("kit-k", toolkits_dir=toolkits_dir)
    assert result.ok is True


def test_real_host_setup_py_with_syntax_error(toolkits_dir):
    """``setup.py`` fails to import. The host catches that, sends a
    'done' with the import traceback. Result is ok=False with the
    traceback captured."""
    setup_py = "def setup(ctx)\n    return True\n"  # missing colon
    _make_toolkit(toolkits_dir, "kit-l", setup_py=setup_py)

    result = run_setup_script("kit-l", toolkits_dir=toolkits_dir, prompt_mode="skip")
    assert result.ok is False
    # The traceback or message references the syntax error somehow.
    blob = (result.traceback or "") + (result.message or "")
    assert "SyntaxError" in blob or "syntax" in blob.lower() or result.log_path is not None


def test_real_host_setup_py_imports_stdlib(toolkits_dir):
    """setup.py can use stdlib normally — the subprocess Python is
    the toolkit's venv, which always has stdlib."""
    setup_py = textwrap.dedent("""
        import json, os, sys
        from pathlib import Path

        def setup(ctx):
            ctx.info(f"json module: {json.__name__}")
            ctx.info(f"sys.executable: {sys.executable}")
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-m", setup_py=setup_py)
    captured = []
    result = run_setup_script(
        "kit-m", toolkits_dir=toolkits_dir, prompt_mode="skip",
        console_print=captured.append,
    )
    assert result.ok is True
    assert any("json module: json" in p for p in captured)


# ── direct-subprocess tests for protocol edge cases ────────────────────


# ── Day 2: prompts + set_config through the real subprocess ───────────


def test_real_host_prompt_skip_mode_uses_default(toolkits_dir):
    """In skip mode, ctx.prompt with a default returns the default."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            answer = ctx.prompt("Your name?", default="anon")
            ctx.info(f"got name: {answer}")
            return answer == "anon"
    """)
    _make_toolkit(toolkits_dir, "kit-prompt-1", setup_py=setup_py)
    captured = []
    result = run_setup_script(
        "kit-prompt-1", toolkits_dir=toolkits_dir, prompt_mode="skip",
        console_print=captured.append,
    )
    assert result.ok is True
    assert any("got name: anon" in p for p in captured)


def test_real_host_prompt_skip_no_default_returns_none(toolkits_dir):
    """In skip mode without a default, ctx.prompt returns None."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            answer = ctx.prompt("required field?")
            ctx.info(f"answer={answer!r}")
            return answer is None
    """)
    _make_toolkit(toolkits_dir, "kit-prompt-2", setup_py=setup_py)
    captured = []
    result = run_setup_script(
        "kit-prompt-2", toolkits_dir=toolkits_dir, prompt_mode="skip",
        console_print=captured.append,
    )
    assert result.ok is True
    assert any("answer=None" in p for p in captured)


def test_real_host_confirm_yes_mode_returns_true(toolkits_dir):
    setup_py = textwrap.dedent("""
        def setup(ctx):
            ok = ctx.confirm("Continue?", default=False)
            ctx.info(f"confirm={ok}")
            return ok is True
    """)
    _make_toolkit(toolkits_dir, "kit-confirm-1", setup_py=setup_py)
    captured = []
    result = run_setup_script(
        "kit-confirm-1", toolkits_dir=toolkits_dir, prompt_mode="yes",
        console_print=captured.append,
    )
    assert result.ok is True


def test_real_host_choice_skip_mode_picks_first(toolkits_dir):
    setup_py = textwrap.dedent("""
        def setup(ctx):
            choice = ctx.choice("How?", [
                ("download", "Download"),
                ("path", "Provide path"),
                ("skip", "Skip"),
            ])
            ctx.info(f"choice={choice!r}")
            return choice == "download"
    """)
    _make_toolkit(toolkits_dir, "kit-choice-1", setup_py=setup_py)
    captured = []
    result = run_setup_script(
        "kit-choice-1", toolkits_dir=toolkits_dir, prompt_mode="skip",
        console_print=captured.append,
    )
    assert result.ok is True


def test_real_host_set_config_writes_to_file(toolkits_dir, tmp_path):
    """ctx.set_config persists to ~/.toolbase/config/<toolkit>.yaml.
    HOME is patched at the fixture level; the storage layer's resolver
    pattern picks up CONFIG_DIR fresh."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            ctx.set_config("derived_value", "from_setup")
            ctx.set_config("count", 42)
            ctx.info("wrote config")
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-write", setup_py=setup_py)

    result = run_setup_script(
        "kit-write", toolkits_dir=toolkits_dir, prompt_mode="skip",
    )
    assert result.ok is True

    # Read back from the canonical location.
    from toolbase.setup import load_config
    data = load_config("kit-write")
    assert data["derived_value"] == "from_setup"
    assert data["count"] == 42


def test_real_host_set_config_write_through_visible_to_get_config(toolkits_dir):
    """ctx.set_config updates the snapshot so get_config sees it."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            ctx.set_config("x", "one")
            assert ctx.get_config("x") == "one", "write-through broken"
            ctx.set_config("x", "two")
            assert ctx.get_config("x") == "two", "second write-through broken"
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-write-through", setup_py=setup_py)
    result = run_setup_script(
        "kit-write-through", toolkits_dir=toolkits_dir, prompt_mode="skip",
    )
    assert result.ok is True


def test_real_host_set_config_in_validate_mode_raises(toolkits_dir):
    """validate(ctx) calling set_config raises a clear RuntimeError on
    the subprocess side. The runner returns ok=False with the
    exception traceback."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            return True
        def validate(ctx):
            ctx.set_config("x", 1)  # forbidden in validate mode
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-validate-write", setup_py=setup_py)
    result = validate_setup_script("kit-validate-write", toolkits_dir=toolkits_dir)
    assert result.ok is False
    assert result.traceback is not None
    assert "not allowed in validate" in result.traceback


def test_real_host_prompt_in_validate_mode_raises(toolkits_dir):
    setup_py = textwrap.dedent("""
        def setup(ctx):
            return True
        def validate(ctx):
            ctx.prompt("nope")  # forbidden
            return True
    """)
    _make_toolkit(toolkits_dir, "kit-validate-prompt", setup_py=setup_py)
    result = validate_setup_script("kit-validate-prompt", toolkits_dir=toolkits_dir)
    assert result.ok is False
    assert "not allowed in validate" in (result.traceback or "")


def test_real_host_get_config_reads_initial_snapshot(toolkits_dir):
    """If a config file already exists, ctx.get_config reads from it."""
    # Pre-populate the config file.
    from toolbase.setup import set_config_value
    set_config_value("kit-prefilled", "preset_key", "preset_value")

    setup_py = textwrap.dedent("""
        def setup(ctx):
            v = ctx.get_config("preset_key")
            ctx.info(f"preset={v}")
            return v == "preset_value"
    """)
    _make_toolkit(toolkits_dir, "kit-prefilled", setup_py=setup_py)

    captured = []
    result = run_setup_script(
        "kit-prefilled", toolkits_dir=toolkits_dir, prompt_mode="skip",
        console_print=captured.append,
    )
    assert result.ok is True
    assert any("preset=preset_value" in p for p in captured)


def test_real_host_setup_can_chain_prompt_and_set_config(toolkits_dir):
    """Realistic flow: prompt → set_config → get_config."""
    setup_py = textwrap.dedent("""
        def setup(ctx):
            n = ctx.prompt_int("How many?", default=4, min=1, max=16)
            ctx.set_config("worker_count", n)
            ctx.info(f"persisted {n} workers")
            stored = ctx.get_config("worker_count")
            return stored == 4
    """)
    _make_toolkit(toolkits_dir, "kit-chain", setup_py=setup_py)
    result = run_setup_script(
        "kit-chain", toolkits_dir=toolkits_dir, prompt_mode="skip",
    )
    assert result.ok is True


# ── Day 3: download through the real subprocess ───────────────────────


def test_real_host_download_through_rpc(toolkits_dir, tmp_path):
    """Actually invoke ctx.download(...) from a subprocess setup.py
    against a localhost HTTP server. Proves the RPC pipeline carries
    download requests end-to-end."""
    import threading
    import socket
    from http.server import BaseHTTPRequestHandler, HTTPServer

    payload = b"downloaded content from the e2e test"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a, **_kw):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    try:
        url = f"http://127.0.0.1:{port}/file.bin"
        download_dest = tmp_path / "downloaded.bin"

        setup_py_template = textwrap.dedent("""
            from pathlib import Path

            def setup(ctx):
                dest = Path({dest!r})
                result = ctx.download({url!r}, dest)
                ctx.info(f"downloaded to {{result}}")
                ctx.info(f"size: {{result.stat().st_size}}")
                return result.exists() and result.read_bytes() == {payload!r}
        """).format(dest=str(download_dest), url=url, payload=payload)

        _make_toolkit(toolkits_dir, "kit-dl", setup_py=setup_py_template)
        captured = []
        result = run_setup_script(
            "kit-dl", toolkits_dir=toolkits_dir, prompt_mode="skip",
            console_print=captured.append,
        )
        assert result.ok is True, f"failed: {result.message}"
        assert download_dest.exists()
        assert download_dest.read_bytes() == payload
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_real_host_download_sha_mismatch_raises_in_setup(toolkits_dir, tmp_path):
    """SHA mismatch should propagate to the toolkit author as
    RuntimeError, which they can catch."""
    import threading
    import socket
    from http.server import BaseHTTPRequestHandler, HTTPServer

    payload = b"some bytes"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a, **_kw):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        url = f"http://127.0.0.1:{port}/file.bin"
        bad_sha = "0" * 64

        setup_py = textwrap.dedent(f"""
            from pathlib import Path

            def setup(ctx):
                try:
                    ctx.download({url!r}, Path("/tmp/x"), sha256={bad_sha!r})
                    ctx.error("download should have failed")
                    return False
                except RuntimeError as e:
                    ctx.info(f"caught expected error: {{e}}")
                    return True
        """)
        _make_toolkit(toolkits_dir, "kit-dl-bad", setup_py=setup_py)
        captured = []
        result = run_setup_script(
            "kit-dl-bad", toolkits_dir=toolkits_dir, prompt_mode="skip",
            console_print=captured.append,
        )
        assert result.ok is True
        assert any("caught expected error" in p for p in captured)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_host_exits_cleanly_when_stdin_closes_before_go(toolkits_dir):
    """Spawn the host directly; close its stdin before sending go.
    The host should exit cleanly (non-zero, but not hung)."""
    setup_py = "def setup(ctx): return True\n"
    tdir = _make_toolkit(toolkits_dir, "kit-n", setup_py=setup_py)

    env = _build_subprocess_env()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "toolbase._setup_host",
            "--toolkit-dir", str(tdir),
            "--name", "kit-n",
        ],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True, bufsize=1,
    )
    # Read hello, then close stdin without sending go.
    hello = proc.stdout.readline()
    assert hello, "host did not send hello"
    parsed = json.loads(hello)
    assert parsed["method"] == "hello"
    proc.stdin.close()
    # The host should exit promptly without hanging — the exact
    # exit code (0 for "parent disconnected cleanly," non-zero for
    # other failures) is less important than the no-hang invariant.
    rc = proc.wait(timeout=10)
    assert rc == 0
