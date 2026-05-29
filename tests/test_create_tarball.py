"""create_tarball must not leak consumer/harness/local state into a package.

A toolkit dir often doubles as a place you install + serve from, so it
accumulates .toolbase/, .mcp.json, .codex/, .claude/. None of that
belongs in the published tarball (it carries machine-specific paths).
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from toolbase.cli import create_tarball


def test_create_tarball_excludes_consumer_state(tmp_path: Path):
    src = tmp_path / "calc"
    src.mkdir()

    # Toolkit files that SHOULD ship.
    (src / "toolkit.yaml").write_text("name: calc\nversion: 0.1.0\n")
    (src / "README.md").write_text("# calc\n")
    (src / "tools").mkdir()
    (src / "tools" / "__init__.py").write_text("")

    # Consumer / harness / local state that must NOT ship.
    (src / ".mcp.json").write_text('{"mcpServers": {}}')
    (src / ".toolbase" / "profiles").mkdir(parents=True)
    (src / ".toolbase" / "serve.yaml").write_text("default: {}\n")
    (src / ".toolbase" / "profiles" / "default.yaml").write_text("toolkits: {}\n")
    (src / ".claude").mkdir()
    (src / ".claude" / "settings.local.json").write_text("{}")
    (src / ".codex").mkdir()
    (src / ".codex" / "config.toml").write_text("")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("")

    out = tmp_path / "calc.tar.gz"
    create_tarball(src, out, "calc")

    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())

    # Shipped.
    assert "toolkit.yaml" in names
    assert "README.md" in names
    assert "tools/__init__.py" in names

    # Excluded.
    leaked = [
        n for n in names
        if n == ".mcp.json"
        or n.split("/", 1)[0] in {".toolbase", ".claude", ".codex", "__pycache__"}
    ]
    assert not leaked, f"leaked consumer/local state into package: {leaked}"
