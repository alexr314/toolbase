"""
Resumable, SHA256-verified, progress-equipped HTTP downloads with
auto-extract.

Used by ``ctx.download(...)`` in Tier-2 setup scripts. Lives in the
**parent process** (not the toolkit subprocess) — Rich progress bars
need parent's terminal, the cache lives in the user's home dir, and
network I/O is more cleanly handled outside the toolkit's venv.

The subprocess invokes downloads via the ``download`` RPC; the parent
calls into this module and pushes ``progress`` notifications back
during transfer.

Public surface:

- ``download(url, destination, *, sha256=None, extract=False,
            description=None, size_hint=None, on_progress=None,
            cache_dir=None) -> Path``

  Synchronous. Returns the destination ``Path`` once the file is in
  place (and extracted, if requested). Raises ``DownloadError`` on
  failure (network, SHA mismatch, extraction problem).

- ``DownloadError`` — distinct from ``RuntimeError`` so callers can
  distinguish download failures from arbitrary author-code crashes.

Resumability discipline:

The download writes to ``<destination>.partial`` (or, when caching,
``<cache_dir>/<urlhash>-<filename>.partial``). On retry within the
same call, we issue a ``Range`` request for the byte offset already
on disk. Across calls, the partial sticks around in the cache so a
cancelled download resumes where it left off.

SHA256 discipline:

If the caller provides ``sha256``, the hash is computed during
streaming (no second pass) and compared at the end. Mismatch → delete
partial, raise. We hash even on cache hits — a corrupted cache file
should produce the same loud failure as a corrupted download.

Extract discipline:

Extension-detected. ``.tar.gz``/``.tgz``, ``.tar.bz2``/``.tbz2``,
``.tar``, ``.zip`` are supported. **Zip-slip defense** is mandatory:
every entry's resolved path is checked to live inside ``destination``;
any escape attempt aborts extraction with a ``DownloadError``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class DownloadError(RuntimeError):
    """Raised on download failure (network, hash, extract).

    Subclass of ``RuntimeError`` so author code that catches
    ``RuntimeError`` (the spec's recommended pattern) catches it
    too. Has a ``code`` attribute for programmatic dispatch.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── chunk size ─────────────────────────────────────────────────────────


# 64 KB is a reasonable default: small enough to update progress
# smoothly on slow connections, big enough that per-chunk overhead
# (system calls, hashlib update, progress callback) stays < 1% of
# wall time. Same as ``shutil.copyfileobj`` default.
_CHUNK_SIZE = 64 * 1024

# Network retry config.
_MAX_RETRIES = 3
_INITIAL_BACKOFF_S = 1.0


# ── helpers ────────────────────────────────────────────────────────────


def _url_filename(url: str) -> str:
    """Extract the filename from a URL.

    Falls back to a hash-derived name if the URL has no path component
    or it ends in '/'.
    """
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path)
    if not name:
        name = "download-" + hashlib.sha256(url.encode()).hexdigest()[:16]
    return name


def _cache_path(cache_dir: Path, url: str, sha256: Optional[str]) -> Path:
    """Compute the cache path for a URL.

    Cache key is URL + SHA256 (if provided) so two URLs that resolve
    to different content on different days don't collide. If no SHA,
    fall back to URL + filename — weaker but still better than no
    cache.
    """
    h = hashlib.sha256()
    h.update(url.encode())
    if sha256:
        h.update(b"|")
        h.update(sha256.encode())
    digest = h.hexdigest()[:16]
    name = _url_filename(url)
    return cache_dir / f"{digest}-{name}"


def _hash_file(path: Path) -> str:
    """Return the SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _safe_extract_member_check(member_path: str, dest: Path) -> Path:
    """Resolve member path against dest; refuse if it escapes.

    Zip-slip defense: a malicious archive can include entries with
    absolute paths or ``..`` traversal that, when extracted naively,
    write outside the intended directory. Fix per CWE-22 / Python
    advisory: resolve the joined path and assert it's inside dest.
    """
    full = (dest / member_path).resolve()
    try:
        full.relative_to(dest.resolve())
    except ValueError:
        raise DownloadError(
            "unsafe_archive",
            f"archive entry escapes destination: {member_path!r} → {full}",
        )
    return full


def _extract_archive(archive_path: Path, dest: Path) -> None:
    """Extract a known archive type into dest.

    Detects type by extension. Performs zip-slip checks on every entry.
    Raises ``DownloadError`` on unknown types or unsafe entries.
    """
    dest.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()

    if name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar")):
        with tarfile.open(archive_path, "r:*") as tar:
            for m in tar.getmembers():
                _safe_extract_member_check(m.name, dest)
            # Python 3.12 added the `filter` kwarg; explicit "data"
            # rejects unsafe entries (links to absolute paths, etc.).
            try:
                tar.extractall(path=dest, filter="data")
            except TypeError:
                # Older Python without filter kwarg — already did our
                # own walk above; fall back to plain extractall.
                tar.extractall(path=dest)
        return

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            for m in zf.namelist():
                _safe_extract_member_check(m, dest)
            zf.extractall(path=dest)
        return

    raise DownloadError(
        "unsupported_archive",
        f"don't know how to extract {archive_path.name}; "
        "supported: .tar.gz, .tgz, .tar.bz2, .tbz2, .tar, .zip",
    )


# ── progress callback shape ────────────────────────────────────────────


# A progress callback receives kwargs:
#   bytes_so_far: int
#   total_bytes: Optional[int]   (None if Content-Length missing)
#   stage: "download" | "extract" | "verify"
#
# It's invoked roughly once per chunk during download (more often than
# strictly necessary, but cheap if the callback is a small RPC notify).
ProgressCallback = Callable[..., None]


def _noop_progress(**_kwargs: Any) -> None:
    pass


# ── core download ──────────────────────────────────────────────────────


def download(
    url: str,
    destination: Path,
    *,
    sha256: Optional[str] = None,
    extract: bool = False,
    description: Optional[str] = None,
    size_hint: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    cache_dir: Optional[Path] = None,
    _requests_module: Any = None,  # injection seam for tests
) -> Path:
    """Download a URL to ``destination``.

    See module docstring for detailed semantics. Args:

    - ``url``: HTTP/HTTPS URL.
    - ``destination``: target file path (or, with ``extract=True``,
      the directory the archive should be extracted into).
    - ``sha256``: hex digest. Verified on completion.
    - ``extract``: auto-extract by extension after download.
    - ``description``: shown in progress UI.
    - ``size_hint``: shown in progress UI when Content-Length is
      missing or unreliable.
    - ``on_progress``: called with kwargs ``bytes_so_far``,
      ``total_bytes``, ``stage``. Used by the parent's RPC pump to
      stream updates to the toolkit subprocess.
    - ``cache_dir``: where to keep partials and resumable bytes.
      Defaults to ``~/.toolbase/cache/``. Pass ``None`` explicitly
      and we use the default; pass an explicit path to override (tests).

    Returns the resolved destination path.
    """
    if _requests_module is None:
        import requests as _requests_module  # type: ignore[no-redef]

    progress = on_progress or _noop_progress
    destination = Path(destination)

    if cache_dir is None:
        # 0.5.0: was ``~/.toolbase/cache/`` — relocated to
        # ``~/.toolbase/downloads/`` to make room for the new
        # cache-of-toolkit-binaries layout under ``cache/<name>/<version>/``.
        cache_dir = Path.home() / ".toolbase" / "downloads"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # The "live" file is in the cache. We move/copy from cache into
    # destination at the end. Two reasons:
    # - Resumability across calls (the partial sticks around).
    # - Re-runs (e.g. ``--reset``) skip the network entirely on cache
    #   hits.
    cache_file = _cache_path(cache_dir, url, sha256)
    partial_file = cache_file.with_suffix(cache_file.suffix + ".partial")

    # Cache hit fast-path: file exists, hash matches.
    if cache_file.exists() and sha256:
        progress(bytes_so_far=cache_file.stat().st_size,
                 total_bytes=cache_file.stat().st_size, stage="verify")
        actual = _hash_file(cache_file)
        if actual == sha256:
            return _finalize(cache_file, destination, extract=extract,
                             progress=progress)
        # Hash mismatch on cache → corrupted cache. Delete and re-fetch.
        cache_file.unlink()

    # Streaming download into partial_file, with retries.
    last_err: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            _stream_to_partial(
                _requests_module, url, partial_file, progress=progress,
                description=description, size_hint=size_hint,
            )
            break
        except (DownloadError,) as e:
            # Hard failures (404, etc.) — don't retry.
            raise
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_INITIAL_BACKOFF_S * (2 ** attempt))
                continue
            # Out of retries — surface.
            raise DownloadError(
                "network",
                f"download failed after {_MAX_RETRIES} attempts: {e}",
            ) from e

    # Move partial → cache_file.
    if partial_file.exists():
        os.replace(partial_file, cache_file)
    elif not cache_file.exists():
        # Should not happen; defensive.
        raise DownloadError(
            "internal",
            "download finished but no file landed in cache",
        )

    # Verify SHA256.
    if sha256:
        progress(bytes_so_far=cache_file.stat().st_size,
                 total_bytes=cache_file.stat().st_size, stage="verify")
        actual = _hash_file(cache_file)
        if actual != sha256:
            cache_file.unlink()
            raise DownloadError(
                "sha_mismatch",
                f"SHA256 mismatch: expected {sha256}, got {actual}",
            )

    return _finalize(cache_file, destination, extract=extract,
                     progress=progress)


def _stream_to_partial(
    requests_module: Any,
    url: str,
    partial_file: Path,
    *,
    progress: ProgressCallback,
    description: Optional[str],
    size_hint: Optional[str],
) -> None:
    """One streaming attempt. Resumes from existing partial bytes via
    HTTP Range request."""
    headers: Dict[str, str] = {}
    resume_from = 0
    if partial_file.exists():
        resume_from = partial_file.stat().st_size
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

    partial_file.parent.mkdir(parents=True, exist_ok=True)

    with requests_module.get(
        url, stream=True, headers=headers,
        timeout=(30, None),  # (connect, read) — read=None means stream forever
    ) as resp:
        # 200 = full content (server didn't honor Range).
        # 206 = partial content (resume worked).
        # Anything else, including 416 "Range Not Satisfiable" if our
        # partial happens to equal full size, is suspicious.
        if resp.status_code in (200,) and resume_from > 0:
            # Server ignored our Range; restart from scratch.
            partial_file.unlink()
            resume_from = 0
        elif resp.status_code == 416:
            # Already have everything? Treat as success — caller will
            # verify SHA. Rare edge case.
            return
        elif 500 <= resp.status_code < 600:
            # Server-side / transient. Raise a *non*-DownloadError so
            # the outer retry loop catches it and backs off.
            raise OSError(f"HTTP {resp.status_code} from {url}")
        elif resp.status_code not in (200, 206):
            # 4xx (404, 403, etc.): author pointed at a bad URL or
            # wrong auth — no retry will help. Surface immediately.
            raise DownloadError(
                "http_error",
                f"HTTP {resp.status_code} from {url}",
            )

        total_bytes: Optional[int] = None
        if "Content-Length" in resp.headers:
            try:
                total_bytes = int(resp.headers["Content-Length"]) + resume_from
            except ValueError:
                total_bytes = None

        # Open in append mode so we extend the existing partial.
        mode = "ab" if resume_from > 0 else "wb"
        bytes_so_far = resume_from
        with open(partial_file, mode) as f:
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                f.write(chunk)
                bytes_so_far += len(chunk)
                progress(
                    bytes_so_far=bytes_so_far,
                    total_bytes=total_bytes,
                    stage="download",
                )


def _finalize(
    cache_file: Path,
    destination: Path,
    *,
    extract: bool,
    progress: ProgressCallback,
) -> Path:
    """Move from cache to destination, extracting if requested."""
    if extract:
        progress(bytes_so_far=0, total_bytes=None, stage="extract")
        # destination is a *directory* in extract mode.
        _extract_archive(cache_file, destination)
        return destination
    # Plain copy: destination is the final file path.
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    # Use copy2 not rename — cache_file should stay in place for re-use.
    shutil.copy2(cache_file, destination)
    return destination
