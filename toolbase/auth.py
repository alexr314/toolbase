"""
Auth helpers for the Toolbase CLI.

Owns:

- The on-disk locations and read/write helpers for the per-user CLI
  token (``~/.toolbase/token``) and the legacy per-toolkit publish
  tokens (``~/.toolbase/<toolkit>/token``).
- The browser-flow login dance (loopback callback server + state CSRF
  check + browser open).
- API helpers that talk to the backend's auth endpoints (``whoami``,
  token revocation).

Design context: see ``docs/PER_USER_TOKEN_DESIGN.md``. Tier-2 #8 in
STATUS.md.

Token prefixes the CLI must distinguish:

- ``tb_user_...`` — per-user CLI token (the current toolbase standard).
  Stored at ``USER_TOKEN_PATH``. The per-user prefix evolved
  ``sct_user_`` → ``stk_user_`` → ``tb_user_`` across rebrands/rollovers.
- ``stk_user_...`` / ``sct_user_...`` — **retired** per-user CLI token
  prefixes. Backend no longer issues these; CLI rejects them at paste
  time and short-circuits any command that finds one in
  ``~/.toolbase/token`` with a "run toolbase logout && toolbase
  login" message before any HTTP call.
- ``stk_...`` — per-toolkit token (legacy from a different deprecation
  track). Stored at ``CONFIG_DIR/<toolkit>/token``. Kept working through
  Phase B. **NB:** the per-toolkit prefix is ``stk_`` (no ``user_``);
  the per-user prefix is ``tb_user_``. The two are distinguished by
  whether ``user_`` follows the leading characters.
- ``toolkit_...`` — earliest MVP per-toolkit prefix. Treated as
  ``stk_`` for compatibility (some users still have these).

The publish flow loads per-user token preferentially and falls back to
the legacy per-toolkit file if no per-user token exists. The backend's
auth middleware accepts all three; the CLI's job is just to prefer the
right one.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config as _config_mod
from .config import CONFIG_DIR  # re-exported for `from toolbase.auth import CONFIG_DIR`


# Public path to the per-user CLI token. Mode 0600 on POSIX. The path
# is intentionally a flat file at the root of CONFIG_DIR so it's
# discoverable next to ``config.json`` and ``serve.yaml`` rather than
# tucked away in a subdir.
#
# This module-level binding is the *default* — it's captured at import
# time. Functions that need the current value (e.g. after a test
# monkeypatches ``config.CONFIG_DIR``) should use ``_resolve_config_dir()``
# below, which re-reads from the ``config`` module each call.
USER_TOKEN_PATH = CONFIG_DIR / "token"


def _resolve_config_dir() -> Path:
    """Get the current CONFIG_DIR, re-reading from the config module.

    Tests sometimes monkeypatch ``toolbase.config.CONFIG_DIR`` to
    redirect storage into a tmp dir. The auth module's own bound
    ``CONFIG_DIR`` reference would shadow that patch and silently send
    reads/writes back to the real ``~/.toolbase/``. Resolving via
    ``_config_mod.CONFIG_DIR`` keeps the patch effective at call time.
    """
    return _config_mod.CONFIG_DIR


_IMPORT_TIME_USER_TOKEN_PATH = CONFIG_DIR / "token"


def _resolve_user_token_path() -> Path:
    """Get the current per-user token path.

    Honors monkeypatching of either ``auth.USER_TOKEN_PATH`` directly
    or of ``config.CONFIG_DIR``:

    - If ``auth.USER_TOKEN_PATH`` was patched away from its import-time
      default, return the patched value (highest priority).
    - Otherwise recompute as ``<current CONFIG_DIR>/token`` so patching
      ``config.CONFIG_DIR`` alone is enough to redirect the auth surface.

    The ``_IMPORT_TIME_USER_TOKEN_PATH`` baseline is captured here at
    module import — both ``USER_TOKEN_PATH`` and that baseline shift
    when this module is reloaded, but they don't shift independently
    under normal use, so the comparison reliably distinguishes "patched"
    from "default."
    """
    import toolbase.auth as _self
    if _self.USER_TOKEN_PATH != _IMPORT_TIME_USER_TOKEN_PATH:
        return _self.USER_TOKEN_PATH
    return _resolve_config_dir() / "token"

# Default API base URL and resolver — canonical definition lives in config.py
# so all modules can import it without pulling in the full auth stack.
from .config import DEFAULT_API_URL, _api_url  # noqa: F401 — re-exported

# Browser-flow defaults.
BROWSER_FLOW_TIMEOUT_S = 300.0  # 5 minutes — generous for sign-in + approve
WEB_AUTH_PATH = "/cli-auth"     # path under the website host


# ── prefix helpers ─────────────────────────────────────────────────────
#
# Per-user CLI token prefix history (each rollover invalidated the prior
# prefix server-side):
#   ``sct_user_`` → ``stk_user_`` (2026-05-15) → ``tb_user_`` (toolbase rebrand).
# The canonical prefix is now ``tb_user_``. Both earlier prefixes are
# retired: the CLI rejects them on paste, and short-circuits any command
# that finds one stored locally, with a clear "rerun login" message.
#
# Separately, the much older per-toolkit-token system used ``stk_`` (no
# ``user_``) / ``toolkit_`` prefixes. That track is distinct from the
# per-user-prefix history above; its classifier must NOT match per-user
# tokens, hence the explicit ``user_`` check in ``is_legacy_toolkit_token``.

# The canonical per-user prefix (current). Constant so it's a single
# point of change if it rolls again.
USER_TOKEN_PREFIX = "tb_user_"

# Retired per-user prefixes. The backend no longer issues these. CLI
# rejects on paste and short-circuits on any stored value matching one
# of these prefixes.
RETIRED_USER_TOKEN_PREFIXES = ("stk_user_", "sct_user_")


def is_user_token(token: str) -> bool:
    """A per-user CLI token (current toolbase standard, ``tb_user_``)."""
    return token.startswith(USER_TOKEN_PREFIX)


def is_retired_user_token(token: str) -> bool:
    """A per-user CLI token using a **retired** prefix (``stk_user_`` / ``sct_user_``).

    The backend no longer issues these. The CLI catches stored values
    matching a retired prefix at the pre-flight step and short-circuits
    with a clear "rerun ``toolbase logout && toolbase login``" message
    rather than letting the request hit the backend and come back with
    an opaque 401.
    """
    return token.startswith(RETIRED_USER_TOKEN_PREFIXES)


def is_legacy_toolkit_token(token: str) -> bool:
    """A per-toolkit publish token (Phase 0 / Phase 1 legacy).

    Distinct from the per-user-prefix-retirement track. Per-toolkit
    tokens start with ``stk_`` (not followed by ``user_``) or
    ``toolkit_``. Returns False for per-user tokens (current or retired)
    that happen to share leading characters.
    """
    if token.startswith(USER_TOKEN_PREFIX) or token.startswith(RETIRED_USER_TOKEN_PREFIXES):
        # per-user token (current or retired), not a per-toolkit token.
        return False
    return token.startswith("stk_") or token.startswith("toolkit_")


# ── stale-token pre-flight ─────────────────────────────────────────────


# Sentinel surfaced to the CLI; matches the wording in the brief.
STALE_TOKEN_MESSAGE = (
    "Your stored CLI token uses a retired prefix (stk_user_ / sct_user_).\n"
    "  Run `toolbase logout && toolbase login` to get a "
    "fresh tb_user_ token."
)


def stored_token_is_retired(*, path: Optional[Path] = None) -> bool:
    """True iff the on-disk per-user token uses the retired prefix.

    Cheap, offline check. Returns False when no token is stored.
    Callers (commands that authenticate against the backend) should
    invoke this before any HTTP request and short-circuit with
    ``STALE_TOKEN_MESSAGE`` when it returns True — saves a backend
    round-trip and gives the same actionable error in offline /
    network-down scenarios.
    """
    token = load_user_token(path=path)
    if not token:
        return False
    return is_retired_user_token(token)


# ── token storage: per-user ───────────────────────────────────────────


def save_user_token(token: str, *, path: Optional[Path] = None) -> Path:
    """Write the per-user CLI token to disk with mode 0600.

    ``path=None`` resolves to the current ``USER_TOKEN_PATH`` *at call
    time* (not function-definition time) so monkeypatching the module
    attribute in tests works correctly. Same sentinel-default pattern
    as ``skills.py``; see HANDOFF.md gotcha #7.
    """
    if path is None:
        path = _resolve_user_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip())
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):  # pragma: no cover (Windows)
        pass
    return path


def load_user_token(*, path: Optional[Path] = None) -> Optional[str]:
    """Read the per-user CLI token. Returns None if missing or empty."""
    if path is None:
        path = _resolve_user_token_path()
    if not path.exists():
        return None
    try:
        token = path.read_text().strip()
    except OSError:
        return None
    return token or None


def delete_user_token(*, path: Optional[Path] = None) -> bool:
    """Remove the per-user CLI token file. Returns True if a file was removed."""
    if path is None:
        path = _resolve_user_token_path()
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


# ── token storage: legacy per-toolkit ──────────────────────────────────


def legacy_token_path(toolkit_name: str, *, base: Optional[Path] = None) -> Path:
    """Per-toolkit token path (legacy)."""
    if base is None:
        base = _resolve_config_dir()
    return base / toolkit_name / "token"


def save_legacy_toolkit_token(
    toolkit_name: str, token: str, *, base: Optional[Path] = None,
) -> Path:
    """Write a legacy per-toolkit token. Mode 0600."""
    if base is None:
        base = _resolve_config_dir()
    path = legacy_token_path(toolkit_name, base=base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip())
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):  # pragma: no cover (Windows)
        pass
    return path


def load_legacy_toolkit_token(
    toolkit_name: str, *, base: Optional[Path] = None,
) -> Optional[str]:
    """Read a legacy per-toolkit token. Returns None if missing."""
    if base is None:
        base = _resolve_config_dir()
    path = legacy_token_path(toolkit_name, base=base)
    if not path.exists():
        return None
    try:
        token = path.read_text().strip()
    except OSError:
        return None
    return token or None


def find_legacy_token_files(*, base: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """Return ``(toolkit_name, path)`` for every legacy per-toolkit token.

    Walks the children of CONFIG_DIR and looks for ``<child>/token``
    files. Skips known non-toolkit subdirectories (``toolkits``,
    ``logs``, ``config``, the new TUI cache, etc.) so a stray token
    file in a config subdir doesn't confuse the migration prompt.
    """
    if base is None:
        base = _resolve_config_dir()
    if not base.exists():
        return []
    skip = {"toolkits", "logs", "config", "cache", "groups"}
    found: List[Tuple[str, Path]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in skip:
            continue
        token_file = entry / "token"
        if token_file.is_file():
            found.append((entry.name, token_file))
    return found


def delete_legacy_token_files(*, base: Optional[Path] = None) -> List[str]:
    """Remove every legacy per-toolkit token file. Returns toolkit names removed."""
    if base is None:
        base = _resolve_config_dir()
    removed: List[str] = []
    for name, path in find_legacy_token_files(base=base):
        try:
            path.unlink()
            removed.append(name)
            # Best-effort cleanup of an empty parent directory. Don't
            # rmtree — the directory may hold non-token state we don't
            # know about (future Phase 3C config files, etc.).
            try:
                path.parent.rmdir()
            except OSError:
                pass
        except OSError:
            pass
    return removed


# ── publish-time resolution ───────────────────────────────────────────


def load_token_for_publish(
    toolkit_name: str, *, base: Optional[Path] = None,
    user_path: Optional[Path] = None,
) -> Tuple[Optional[str], str]:
    """Pick the best token for a publish request.

    Returns ``(token, source)`` where ``source`` is one of:
        ``"user"``    — per-user CLI token at USER_TOKEN_PATH
        ``"legacy"``  — per-toolkit token at CONFIG_DIR/<name>/token
        ``"none"``    — neither file exists; caller should error out

    Per-user is strictly preferred. The backend accepts both during the
    migration window so the choice is local-policy only.
    """
    if base is None:
        base = _resolve_config_dir()
    if user_path is None:
        user_path = _resolve_user_token_path()
    user_tok = load_user_token(path=user_path)
    if user_tok:
        return user_tok, "user"
    legacy = load_legacy_toolkit_token(toolkit_name, base=base)
    if legacy:
        return legacy, "legacy"
    return None, "none"


# ── browser-flow login ────────────────────────────────────────────────


@dataclass
class BrowserFlowResult:
    """Outcome of the browser-flow callback wait."""
    token: Optional[str] = None
    denied: bool = False
    timed_out: bool = False
    error: Optional[str] = None


class BrowserFlow:
    """Loopback callback server + browser opener for ``toolbase login``.

    The flow:

    1. Generate a CSRF state nonce.
    2. Bind a localhost HTTP server on a random port.
    3. Construct the auth URL: ``<web_base>/cli-auth?callback=...&state=...&hostname=...``
    4. Open the user's browser at that URL.
    5. Wait for a single POST to ``/cli-callback`` carrying either
       ``{"state": ..., "token": "tb_user_..."}`` (approval) or
       ``{"state": ..., "denied": true}`` (denial).
    6. Validate the state nonce and return.

    The callback responds with CORS headers so the website (different
    origin) can POST to it from JavaScript. This avoids putting the
    token in browser history (vs the redirect-with-query-params
    pattern).

    Threading note: the HTTP server runs on a daemon thread; ``run()``
    blocks the caller until either a callback arrives, the user denies,
    or the timeout fires. Idempotent shutdown — the same instance can
    be ``run()`` once.
    """

    def __init__(
        self,
        *,
        web_base: str = "https://tool-base.org",
        timeout_s: float = BROWSER_FLOW_TIMEOUT_S,
        open_browser: Callable[[str], bool] = webbrowser.open,
    ):
        self.web_base = web_base.rstrip("/")
        self.timeout_s = timeout_s
        self._open_browser = open_browser
        self._state = secrets.token_urlsafe(32)
        self._result_event = threading.Event()
        self._result = BrowserFlowResult()
        self._httpd: Optional[http.server.HTTPServer] = None

    @property
    def state(self) -> str:
        """The CSRF nonce we expect the frontend to echo back."""
        return self._state

    def build_auth_url(self, callback_url: str) -> str:
        """Compose the website URL the user lands on."""
        params = {
            "callback": callback_url,
            "state": self._state,
            "hostname": socket.gethostname(),
        }
        return f"{self.web_base}{WEB_AUTH_PATH}?{urllib.parse.urlencode(params)}"

    def run(self) -> BrowserFlowResult:
        """Run the full flow. Blocks until callback / denial / timeout.

        Returns a populated ``BrowserFlowResult``. The CSRF state check
        is enforced inside the callback handler — a mismatch fills
        ``result.error`` and does NOT populate ``result.token``.
        """
        # Bind to a random port. We bind to 127.0.0.1 explicitly
        # (not 0.0.0.0) so we never expose the callback to the LAN.
        port = self._pick_free_port()
        callback_url = f"http://127.0.0.1:{port}/cli-callback"
        handler_cls = self._make_handler()

        self._httpd = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
        server_thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="toolbase-cli-callback",
            daemon=True,
        )
        server_thread.start()

        auth_url = self.build_auth_url(callback_url)

        # Best-effort browser open. If it fails (headless box, etc.),
        # the URL is still printed by the caller so a human can
        # paste it into a browser somewhere else.
        try:
            self._open_browser(auth_url)
        except Exception:
            pass

        completed = self._result_event.wait(timeout=self.timeout_s)
        try:
            self._httpd.shutdown()
        except Exception:
            pass
        try:
            self._httpd.server_close()
        except Exception:
            pass

        if not completed:
            self._result.timed_out = True
        return self._result

    @property
    def auth_url_preview(self) -> Optional[str]:
        """For tests / displays: build the URL without binding a server.

        Uses a placeholder port. Real ``run()`` rebuilds with the actual
        port the server bound. Don't use this for the real handshake.
        """
        return self.build_auth_url("http://127.0.0.1:0/cli-callback")

    # ── internals ──────────────────────────────────────────────────

    @staticmethod
    def _pick_free_port() -> int:
        """Bind, getsockname, release. Tiny race vs. the real bind, but
        the next bind happens immediately on the same loopback so OS
        port reuse is fine in practice."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _make_handler(self) -> type:
        """Build the BaseHTTPRequestHandler subclass bound to this flow."""
        flow = self

        class _CallbackHandler(http.server.BaseHTTPRequestHandler):
            # Suppress access-log noise on stderr — the CLI prints its
            # own status messages.
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_OPTIONS(self) -> None:
                # CORS preflight. Frontend's POST is cross-origin
                # (tool-base.org → 127.0.0.1) so we must answer this.
                self.send_response(204)
                self._send_cors_headers()
                self.end_headers()

            def do_POST(self) -> None:
                if self.path != "/cli-callback":
                    self.send_response(404)
                    self._send_cors_headers()
                    self.end_headers()
                    return

                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None

                self._handle_payload(payload)

            def _handle_payload(self, payload: Optional[Dict[str, Any]]) -> None:
                if not isinstance(payload, dict):
                    flow._result.error = "callback body was not valid JSON"
                    self._respond(400, "invalid body")
                    flow._result_event.set()
                    return

                # CSRF check: the state echoed by the frontend MUST
                # match what we generated.
                if payload.get("state") != flow._state:
                    flow._result.error = "state mismatch (possible CSRF)"
                    self._respond(400, "state mismatch")
                    flow._result_event.set()
                    return

                if payload.get("denied"):
                    flow._result.denied = True
                    self._respond(200, "received")
                    flow._result_event.set()
                    return

                token = payload.get("token")
                if not isinstance(token, str) or not token:
                    flow._result.error = "callback missing token"
                    self._respond(400, "missing token")
                    flow._result_event.set()
                    return

                flow._result.token = token
                self._respond(200, "received")
                flow._result_event.set()

            def _respond(self, code: int, status: str) -> None:
                body = json.dumps({"status": status}).encode("utf-8")
                self.send_response(code)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_cors_headers(self) -> None:
                # We allow the public website origin specifically. A
                # wildcard would also work (the token is meaningless to
                # any other origin), but being precise costs nothing
                # and is good hygiene.
                origin = flow.web_base
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type",
                )

        return _CallbackHandler


# ── backend API helpers ───────────────────────────────────────────────
# _api_url() and DEFAULT_API_URL are imported from config.py above.


def whoami(token: str, *, timeout_s: float = 10.0) -> Optional[Dict[str, Any]]:
    """Call ``GET /api/auth/whoami``. Returns the JSON dict or None on failure.

    Errors (network, non-200) are swallowed and surfaced as None — the
    caller decides how to render them. Local-import ``requests`` so
    importing this module doesn't pull the dep into modules that don't
    need it.
    """
    import requests
    try:
        r = requests.get(
            f"{_api_url()}/api/auth/whoami",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def revoke_token(token_id: str, bearer_token: str, *, timeout_s: float = 10.0) -> bool:
    """Call ``DELETE /api/auth/cli-tokens/<id>``. Returns True on 2xx, False otherwise.

    Best-effort — used by ``logout`` which still deletes the local file
    regardless of API success. Network errors return False.
    """
    import requests
    try:
        r = requests.delete(
            f"{_api_url()}/api/auth/cli-tokens/{token_id}",
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=timeout_s,
        )
    except requests.exceptions.RequestException:
        return False
    return 200 <= r.status_code < 300
