"""
Unit tests for ``toolbase.setup.downloads``.

Drive the helper against a fixture-managed localhost HTTP server so
no real network is touched. Cover:

- Happy path: download → SHA verify → write to destination.
- Cache hit: known file with matching SHA = no network.
- Retry: simulated transient failure → eventual success.
- SHA mismatch: corrupted bytes detected, file deleted, error raised.
- Resume: partial file on disk gets a Range request and continues.
- Auto-extract: ``.tar.gz``, ``.tar``, ``.zip`` all work; ``.exe`` is
  rejected with ``unsupported_archive``.
- **Zip-slip defense:** synthetic malicious tarball with ``../../etc/x``
  is rejected with ``unsafe_archive`` (per the manager's explicit ask
  on Day 3 sign-off).
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import socket
import tarfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import pytest

from toolbase.setup.downloads import (
    DownloadError, download, _hash_file, _safe_extract_member_check,
    _url_filename, _cache_path,
)


# ── fixtures ──────────────────────────────────────────────────────────


class _ContentServer:
    """Minimal HTTP server serving controllable byte content.

    The test sets ``self.content`` (bytes) and optionally ``self.fail_first_n``
    (int) to simulate transient failures. Each request consumes one
    "fail-first-n" if positive.
    """

    def __init__(self):
        self.content = b""
        self.content_type = "application/octet-stream"
        self.support_range = True
        self.fail_first_n = 0
        self.requests_received: list = []
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a, **_kw):
                pass  # suppress test noise

            def do_GET(self):
                outer.requests_received.append({
                    "path": self.path,
                    "range": self.headers.get("Range"),
                })
                if outer.fail_first_n > 0:
                    outer.fail_first_n -= 1
                    self.send_response(503)
                    self.end_headers()
                    return
                rng = self.headers.get("Range")
                if rng and outer.support_range:
                    # Parse "bytes=N-"
                    try:
                        start = int(rng.replace("bytes=", "").split("-")[0])
                    except ValueError:
                        start = 0
                    payload = outer.content[start:]
                    self.send_response(206)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Content-Type", outer.content_type)
                    self.send_header(
                        "Content-Range",
                        f"bytes {start}-{len(outer.content) - 1}/{len(outer.content)}",
                    )
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(outer.content)))
                self.send_header("Content-Type", outer.content_type)
                self.end_headers()
                self.wfile.write(outer.content)

        # Bind to a free loopback port.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        self._httpd = HTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True,
        )
        self._thread.start()
        return port

    def url_for(self, path: str = "/file.bin") -> str:
        port = self._httpd.server_port if self._httpd else 0
        return f"http://127.0.0.1:{port}{path}"

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()


@pytest.fixture
def server():
    s = _ContentServer()
    s.start()
    yield s
    s.stop()


# ── happy path ────────────────────────────────────────────────────────


def test_simple_download(server, tmp_path):
    server.content = b"hello world"
    dest = tmp_path / "file.bin"
    cache = tmp_path / "cache"

    result = download(server.url_for(), dest, cache_dir=cache)
    assert result == dest
    assert dest.read_bytes() == b"hello world"


def test_download_with_sha256_match(server, tmp_path):
    payload = b"verified content"
    server.content = payload
    sha = hashlib.sha256(payload).hexdigest()

    result = download(
        server.url_for(), tmp_path / "f.bin",
        sha256=sha, cache_dir=tmp_path / "cache",
    )
    assert result.exists()


def test_download_with_sha256_mismatch_raises(server, tmp_path):
    server.content = b"original"
    bad_sha = "0" * 64

    with pytest.raises(DownloadError) as ei:
        download(
            server.url_for(), tmp_path / "f.bin",
            sha256=bad_sha, cache_dir=tmp_path / "cache",
        )
    assert ei.value.code == "sha_mismatch"
    # Corrupted cache entry deleted so a retry won't loop on the same bad bytes.
    cache = tmp_path / "cache"
    assert not list(cache.glob("*partial*"))


def test_download_progress_callback_invoked(server, tmp_path):
    server.content = b"x" * 200_000  # bigger than one chunk
    events = []
    download(
        server.url_for(), tmp_path / "f.bin",
        cache_dir=tmp_path / "cache",
        on_progress=lambda **kw: events.append(kw),
    )
    # We got at least one "download" event with bytes_so_far > 0.
    download_events = [e for e in events if e["stage"] == "download"]
    assert download_events
    assert download_events[-1]["bytes_so_far"] >= 200_000


# ── cache behavior ────────────────────────────────────────────────────


def test_cache_hit_skips_network(server, tmp_path):
    """Second download with same URL+SHA reads from cache."""
    payload = b"cacheable"
    sha = hashlib.sha256(payload).hexdigest()
    server.content = payload
    cache = tmp_path / "cache"

    download(server.url_for(), tmp_path / "a.bin", sha256=sha, cache_dir=cache)
    requests_before = len(server.requests_received)

    download(server.url_for(), tmp_path / "b.bin", sha256=sha, cache_dir=cache)
    # Second call should not have hit the network.
    assert len(server.requests_received) == requests_before


def test_cache_with_corrupted_file_redownloads(server, tmp_path):
    """Cache file exists but its bytes don't match the SHA → re-fetch."""
    payload = b"original content"
    sha = hashlib.sha256(payload).hexdigest()
    server.content = payload
    cache = tmp_path / "cache"
    cache.mkdir()

    # Pre-seed cache with corrupted content under the right cache name.
    bad_path = _cache_path(cache, server.url_for(), sha)
    bad_path.write_bytes(b"corrupted")

    download(server.url_for(), tmp_path / "f.bin", sha256=sha, cache_dir=cache)
    # We hit the network at least once.
    assert len(server.requests_received) >= 1
    # And got the right content into dest.
    assert (tmp_path / "f.bin").read_bytes() == payload


# ── retry ─────────────────────────────────────────────────────────────


def test_retry_on_transient_failure(server, tmp_path, monkeypatch):
    """Server fails first 2 attempts, then succeeds."""
    # Speed up the test by reducing the backoff.
    import toolbase.setup.downloads as dl_mod
    monkeypatch.setattr(dl_mod, "_INITIAL_BACKOFF_S", 0.01)

    server.content = b"finally-ok"
    server.fail_first_n = 2

    download(server.url_for(), tmp_path / "f.bin",
             cache_dir=tmp_path / "cache")
    assert (tmp_path / "f.bin").read_bytes() == b"finally-ok"


def test_retry_exhaustion_raises_network_error(server, tmp_path, monkeypatch):
    """All retries fail → DownloadError(code='network')."""
    import toolbase.setup.downloads as dl_mod
    monkeypatch.setattr(dl_mod, "_INITIAL_BACKOFF_S", 0.01)

    # Drop the entire server so nothing answers.
    server.stop()
    # The server.url_for still points at the (now-dead) port.

    with pytest.raises(DownloadError) as ei:
        download(server.url_for(), tmp_path / "f.bin",
                 cache_dir=tmp_path / "cache")
    assert ei.value.code == "network"


# ── resume ────────────────────────────────────────────────────────────


def test_resume_via_range_header(server, tmp_path):
    """If a partial file exists, the next call should resume."""
    server.content = b"the full content of the download"
    cache = tmp_path / "cache"
    cache.mkdir()

    # Fake a partial: write the first 10 bytes to the right cache
    # location's .partial file.
    partial = _cache_path(cache, server.url_for(), None)
    partial = partial.with_suffix(partial.suffix + ".partial")
    partial.write_bytes(server.content[:10])

    download(server.url_for(), tmp_path / "f.bin", cache_dir=cache)

    # We sent a Range request.
    has_range = any(
        r.get("range") and "bytes=10" in r["range"]
        for r in server.requests_received
    )
    assert has_range, "expected a Range request for resume"
    assert (tmp_path / "f.bin").read_bytes() == server.content


def test_server_ignores_range_header(server, tmp_path):
    """If the server returns 200 instead of 206, we restart from
    scratch — partial bytes get discarded."""
    server.support_range = False
    server.content = b"complete content"
    cache = tmp_path / "cache"
    cache.mkdir()
    partial = _cache_path(cache, server.url_for(), None)
    partial = partial.with_suffix(partial.suffix + ".partial")
    partial.write_bytes(b"WRONG-BYTES")  # would corrupt if we appended

    download(server.url_for(), tmp_path / "f.bin", cache_dir=cache)
    assert (tmp_path / "f.bin").read_bytes() == server.content


# ── extraction ────────────────────────────────────────────────────────


def _make_tarball(path: Path, contents: dict):
    """Build a .tar.gz with the given {filename: bytes} contents."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data in contents.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _make_zipfile(path: Path, contents: dict):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in contents.items():
            zf.writestr(name, data)


def test_download_and_extract_tar_gz(server, tmp_path):
    archive_payload = io.BytesIO()
    with tarfile.open(fileobj=archive_payload, mode="w:gz") as tar:
        for name, data in {"a.txt": b"AAA", "sub/b.txt": b"BBB"}.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    server.content = archive_payload.getvalue()
    server.content_type = "application/gzip"

    dest = tmp_path / "extract-here"
    download(
        server.url_for("/archive.tar.gz"), dest,
        extract=True, cache_dir=tmp_path / "cache",
    )
    assert (dest / "a.txt").read_bytes() == b"AAA"
    assert (dest / "sub" / "b.txt").read_bytes() == b"BBB"


def test_download_and_extract_zip(server, tmp_path):
    archive_payload = io.BytesIO()
    with zipfile.ZipFile(archive_payload, "w") as zf:
        zf.writestr("hello.txt", "hello")
        zf.writestr("nested/world.txt", "world")
    server.content = archive_payload.getvalue()

    dest = tmp_path / "z-extract"
    download(
        server.url_for("/archive.zip"), dest,
        extract=True, cache_dir=tmp_path / "cache",
    )
    assert (dest / "hello.txt").read_text() == "hello"
    assert (dest / "nested" / "world.txt").read_text() == "world"


def test_extract_unsupported_format_raises(server, tmp_path):
    server.content = b"this is not an archive"

    with pytest.raises(DownloadError) as ei:
        download(
            server.url_for("/file.exe"), tmp_path / "extract-target",
            extract=True, cache_dir=tmp_path / "cache",
        )
    assert ei.value.code == "unsupported_archive"


# ── ZIP-SLIP DEFENSE (manager called this out specifically) ────────────


def test_zip_slip_in_tarball_refused(server, tmp_path):
    """A tarball with a malicious ``../../etc/passwd`` entry must be
    refused with unsafe_archive — never written outside dest."""
    archive_payload = io.BytesIO()
    with tarfile.open(fileobj=archive_payload, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../../escape.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"BAD!"))
    server.content = archive_payload.getvalue()
    server.content_type = "application/gzip"

    dest = tmp_path / "ext"
    with pytest.raises(DownloadError) as ei:
        download(
            server.url_for("/bad.tar.gz"), dest,
            extract=True, cache_dir=tmp_path / "cache",
        )
    assert ei.value.code == "unsafe_archive"
    # And of course nothing was written outside dest.
    assert not (tmp_path / "escape.txt").exists()


def test_zip_slip_with_absolute_path_refused(server, tmp_path):
    """Tarball entry with absolute path /etc/x → refused."""
    archive_payload = io.BytesIO()
    with tarfile.open(fileobj=archive_payload, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="/abs/escape.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"X"))
    server.content = archive_payload.getvalue()

    dest = tmp_path / "ext"
    with pytest.raises(DownloadError) as ei:
        download(
            server.url_for("/abs.tar.gz"), dest,
            extract=True, cache_dir=tmp_path / "cache",
        )
    assert ei.value.code == "unsafe_archive"


def test_zip_slip_in_zipfile_refused(server, tmp_path):
    """Zip with ``../escape.txt`` entry → refused."""
    archive_payload = io.BytesIO()
    with zipfile.ZipFile(archive_payload, "w") as zf:
        zf.writestr("../escape.txt", "BAD")
    server.content = archive_payload.getvalue()

    dest = tmp_path / "z-ext"
    with pytest.raises(DownloadError) as ei:
        download(
            server.url_for("/bad.zip"), dest,
            extract=True, cache_dir=tmp_path / "cache",
        )
    assert ei.value.code == "unsafe_archive"


def test_safe_extract_member_check_unit(tmp_path):
    """Direct test of the helper for thoroughness."""
    dest = tmp_path / "dest"
    dest.mkdir()
    # Valid relative path: ok.
    _safe_extract_member_check("subdir/file.txt", dest)
    # Path traversal: rejected.
    with pytest.raises(DownloadError):
        _safe_extract_member_check("../../escape.txt", dest)
    # Absolute path: rejected.
    with pytest.raises(DownloadError):
        _safe_extract_member_check("/abs/path.txt", dest)


# ── helper unit tests ─────────────────────────────────────────────────


def test_url_filename_basic():
    assert _url_filename("http://example.com/foo/bar.tar.gz") == "bar.tar.gz"


def test_url_filename_no_path_falls_back_to_hash():
    name = _url_filename("http://example.com/")
    assert name.startswith("download-")


def test_cache_path_uses_url_and_sha(tmp_path):
    p1 = _cache_path(tmp_path, "http://example.com/x.bin", "abc")
    p2 = _cache_path(tmp_path, "http://example.com/x.bin", "def")
    p3 = _cache_path(tmp_path, "http://example.com/x.bin", None)
    # Different SHAs = different cache entries.
    assert p1 != p2
    # No-SHA path is yet another entry.
    assert p1 != p3


def test_hash_file_matches_sha256(tmp_path):
    payload = b"check me"
    f = tmp_path / "x"
    f.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert _hash_file(f) == expected
