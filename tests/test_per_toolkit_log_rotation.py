"""Test the per-toolkit stderr log tail-prune behavior.

The orchestrator pumps each child subprocess's stderr into
``~/.toolbase/logs/<toolkit>.log``. Without rotation those files
accumulated forever — HANDOFF.md flagged this as #7. We mirror the
serve.log tail-prune approach: at session start, if the file is past
the size cap, keep the last ~tail bytes and discard the rest.
"""

from __future__ import annotations

from pathlib import Path

from toolbase.serve import orchestrator


def test_prune_no_op_when_file_missing(tmp_path: Path):
    log_path = tmp_path / "missing.log"
    orchestrator._prune_per_toolkit_log_if_oversized(log_path)
    assert not log_path.exists()


def test_prune_no_op_when_under_cap(tmp_path: Path):
    log_path = tmp_path / "small.log"
    content = b"hello\n" * 100
    log_path.write_bytes(content)
    orchestrator._prune_per_toolkit_log_if_oversized(log_path)
    assert log_path.read_bytes() == content


def test_prune_trims_when_oversized(tmp_path: Path):
    log_path = tmp_path / "big.log"
    # Build > MAX_BYTES of distinct lines so we can confirm only the tail
    # survives.
    # Each line is 100 bytes total: 14-char prefix + 86 bytes of body+newline.
    body = b"x" * 85 + b"\n"
    line_len = len(body) + len("line-NNNNNNNN-")
    assert line_len == 100
    n_lines = (orchestrator.PER_TOOLKIT_LOG_MAX_BYTES // line_len) + 5000
    with open(log_path, "wb") as f:
        for i in range(n_lines):
            f.write(f"line-{i:08d}-".encode() + body)

    original_size = log_path.stat().st_size
    assert original_size > orchestrator.PER_TOOLKIT_LOG_MAX_BYTES

    orchestrator._prune_per_toolkit_log_if_oversized(log_path)

    new_size = log_path.stat().st_size
    assert new_size < original_size
    # Must have kept roughly the last TAIL_BYTES, plus the banner.
    assert new_size <= orchestrator.PER_TOOLKIT_LOG_TAIL_BYTES + 200

    text = log_path.read_text()
    assert text.startswith("# --- log pruned to last ~2 MB ---\n")
    # Earlier lines (start of file) should be gone.
    assert "line-00000000" not in text
    # Recent lines should be present.
    assert f"line-{n_lines - 1:08d}" in text


def test_prune_handles_no_newline_in_tail(tmp_path: Path):
    """If the tail-window doesn't begin at a line boundary, we drop the
    partial leading line; we don't crash."""
    log_path = tmp_path / "weird.log"
    # One huge line of binary garbage with no newlines.
    log_path.write_bytes(b"a" * (orchestrator.PER_TOOLKIT_LOG_MAX_BYTES + 1000))
    orchestrator._prune_per_toolkit_log_if_oversized(log_path)
    # Should not raise; file should be smaller and start with the banner.
    assert log_path.stat().st_size <= orchestrator.PER_TOOLKIT_LOG_TAIL_BYTES + 200
    assert log_path.read_bytes().startswith(b"# --- log pruned to last ~2 MB ---\n")
