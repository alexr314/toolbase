"""Unit tests for `toolbase ingest` merge mode (0.6.1).

Re-running ingest over an existing toolkit.yaml re-syncs the tools:
list against source WITHOUT clobbering hand-edits. The load-bearing
requirement: matched entries (keyed on module+name) are left
byte-for-byte untouched — custom description:, bundle:, ordering, and
comments all survive. Only genuinely-new tools are appended (ungrouped);
stale entries are reported, removed only with --prune.

Tests both the merge function directly and the CLI command wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase.cli import main
from toolbase.ingest import (
    ToolDescriptor,
    merge_tools_into_existing,
    load_existing_yaml,
    ingest,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _td(module: str, name: str, desc: str = "generated") -> ToolDescriptor:
    return ToolDescriptor(
        module=module,
        name=name,
        description=desc,
        source_path=Path("/tmp/fake.py"),
        source_line=1,
        kind="function",
    )


def _write_tool(pkg: Path, mod: str, fn: str, doc: str = "Doc.") -> None:
    (pkg / f"{mod}.py").write_text(
        "from orchestral import define_tool\n\n"
        "@define_tool\n"
        f"def {fn}(x: int) -> int:\n"
        f'    """{doc}"""\n'
        "    return x\n",
        encoding="utf-8",
    )


def _make_repo(root: Path, tools: dict) -> None:
    """tools maps module-file-stem -> function name."""
    pkg = root / "tools"
    pkg.mkdir(parents=True, exist_ok=True)
    imports = "".join(f"from .{m} import {fn}\n" for m, fn in tools.items())
    (pkg / "__init__.py").write_text(imports, encoding="utf-8")
    for m, fn in tools.items():
        _write_tool(pkg, m, fn)


HAND_EDITED_YAML = """\
name: mykit
version: 0.1.0
description: My toolkit
category: hep
# Carefully annotated — must survive re-ingest.
tools:
  - module: tools.beta
    name: beta
    description: CUSTOM beta description
    bundle: extras
  - module: tools.alpha
    name: alpha
    description: CUSTOM alpha description
    bundle: core
bundles:
  core:
    requires: []
"""


# ── merge function: the load-bearing semantics ──────────────────────────────


def test_no_op_when_source_matches_yaml():
    existing = load_existing_yaml_from_str(HAND_EDITED_YAML)
    discovered = [_td("tools.alpha", "alpha"), _td("tools.beta", "beta")]
    outcome = merge_tools_into_existing(existing, discovered, prune=False)
    assert outcome.changed is False
    assert outcome.added == []
    assert outcome.stale == []
    assert outcome.preserved_count == 2


def test_new_tool_appended_existing_preserved_byte_for_byte(tmp_path):
    # The load-bearing test: hand-edited entries untouched, new tool
    # appended ungrouped at the end.
    target = tmp_path / "toolkit.yaml"
    target.write_text(HAND_EDITED_YAML, encoding="utf-8")
    existing = load_existing_yaml(target)
    discovered = [
        _td("tools.alpha", "alpha"),
        _td("tools.beta", "beta"),
        _td("tools.gamma", "gamma", "Gamma docstring"),
    ]
    outcome = merge_tools_into_existing(existing, discovered, prune=False)
    assert outcome.changed is True
    assert [(t.module, t.name) for t in outcome.added] == [("tools.gamma", "gamma")]
    assert outcome.preserved_count == 2

    tools = existing["tools"]
    # Existing entries unchanged: custom descriptions, groups, ordering.
    assert tools[0]["name"] == "beta"
    assert tools[0]["description"] == "CUSTOM beta description"
    assert tools[0]["bundle"] == "extras"
    assert tools[1]["name"] == "alpha"
    assert tools[1]["description"] == "CUSTOM alpha description"
    assert tools[1]["bundle"] == "core"
    # New tool appended at the END, ungrouped.
    assert tools[2]["module"] == "tools.gamma"
    assert tools[2]["name"] == "gamma"
    assert tools[2]["description"] == "Gamma docstring"
    assert "bundle" not in tools[2]


def test_stale_entry_reported_not_removed_by_default():
    existing = load_existing_yaml_from_str(HAND_EDITED_YAML)
    # beta's source is gone; only alpha discovered.
    discovered = [_td("tools.alpha", "alpha")]
    outcome = merge_tools_into_existing(existing, discovered, prune=False)
    assert ("tools.beta", "beta") in outcome.stale
    assert outcome.pruned == []
    # beta still present in the yaml (non-destructive default).
    names = [e["name"] for e in existing["tools"]]
    assert "beta" in names


def test_stale_entry_removed_with_prune():
    existing = load_existing_yaml_from_str(HAND_EDITED_YAML)
    discovered = [_td("tools.alpha", "alpha")]
    outcome = merge_tools_into_existing(existing, discovered, prune=True)
    assert outcome.changed is True
    assert ("tools.beta", "beta") in outcome.pruned
    names = [e["name"] for e in existing["tools"]]
    assert "beta" not in names
    assert "alpha" in names


def test_renamed_tool_reports_both_facts_no_migration():
    existing = load_existing_yaml_from_str(HAND_EDITED_YAML)
    # alpha renamed to alpha2 (new module+name); beta unchanged.
    discovered = [_td("tools.alpha", "alpha2"), _td("tools.beta", "beta")]
    outcome = merge_tools_into_existing(existing, discovered, prune=False)
    # New tool appeared...
    assert ("tools.alpha", "alpha2") in [(t.module, t.name) for t in outcome.added]
    # ...and the old one's source vanished. Both reported, no migration.
    assert ("tools.alpha", "alpha") in outcome.stale
    # The old alpha entry keeps its custom annotation (not migrated).
    old_alpha = next(e for e in existing["tools"] if e["name"] == "alpha")
    assert old_alpha["description"] == "CUSTOM alpha description"
    # The new tool is ungrouped (no annotation guessed).
    new_alpha2 = next(e for e in existing["tools"] if e["name"] == "alpha2")
    assert "bundle" not in new_alpha2


def test_config_and_bundles_untouched():
    existing = load_existing_yaml_from_str(HAND_EDITED_YAML)
    discovered = [
        _td("tools.alpha", "alpha"),
        _td("tools.beta", "beta"),
        _td("tools.gamma", "gamma"),
    ]
    merge_tools_into_existing(existing, discovered, prune=False)
    # bundles block is byte-identical.
    assert existing["bundles"] == {"core": {"requires": []}}
    # Top-level metadata untouched.
    assert existing["name"] == "mykit"
    assert existing["category"] == "hep"


def load_existing_yaml_from_str(text: str):
    """Parse a yaml string into a ruamel roundtrippable mapping."""
    from ruamel.yaml import YAML
    from io import StringIO
    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml.load(StringIO(text))


# ── CLI command: end-to-end merge wiring ─────────────────────────────────────


def test_cli_merge_appends_new_tool(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha", "beta": "beta"})
    # Scaffold first.
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    # Hand-edit: add a custom description + bundle to alpha.
    target = tmp_path / "toolkit.yaml"
    data = load_existing_yaml(target)
    data["tools"][0]["description"] = "HAND EDITED"
    data["tools"][0]["bundle"] = "core"
    from toolbase.ingest import _dump_yaml
    _dump_yaml(data, target)
    # Add a new tool to source.
    pkg = tmp_path / "tools"
    _write_tool(pkg, "gamma", "gamma")
    (pkg / "__init__.py").write_text(
        "from .alpha import alpha\nfrom .beta import beta\n"
        "from .gamma import gamma\n",
        encoding="utf-8",
    )
    # Re-ingest (merge).
    result = CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    assert result.exit_code == 0, result.output
    assert "Merge complete" in result.output
    assert "gamma" in result.output

    after = load_existing_yaml(target)
    names = [e["name"] for e in after["tools"]]
    assert "gamma" in names
    # Hand edit preserved.
    alpha = next(e for e in after["tools"] if e["name"] == "alpha")
    assert alpha["description"] == "HAND EDITED"
    assert alpha["bundle"] == "core"


def test_cli_merge_no_op_does_not_rewrite(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha"})
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    target = tmp_path / "toolkit.yaml"
    before = target.read_text()
    mtime_before = target.stat().st_mtime_ns
    # Re-ingest with no source change.
    result = CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    assert result.exit_code == 0
    assert "No changes" in result.output
    # File content identical and not rewritten (mtime unchanged).
    assert target.read_text() == before
    assert target.stat().st_mtime_ns == mtime_before


def test_cli_merge_stale_warns_not_removed(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha", "beta": "beta"})
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    # Delete beta source + import.
    (tmp_path / "tools" / "beta.py").unlink()
    (tmp_path / "tools" / "__init__.py").write_text(
        "from .alpha import alpha\n", encoding="utf-8"
    )
    result = CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    assert result.exit_code == 0
    assert "no longer found" in result.output
    # beta still in yaml.
    after = load_existing_yaml(tmp_path / "toolkit.yaml")
    assert "beta" in [e["name"] for e in after["tools"]]


def test_cli_prune_removes_stale_with_yes(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha", "beta": "beta"})
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    (tmp_path / "tools" / "beta.py").unlink()
    (tmp_path / "tools" / "__init__.py").write_text(
        "from .alpha import alpha\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        main, ["ingest", str(tmp_path), "--prune", "--yes"]
    )
    assert result.exit_code == 0
    assert "pruned" in result.output
    after = load_existing_yaml(tmp_path / "toolkit.yaml")
    assert "beta" not in [e["name"] for e in after["tools"]]


def test_cli_prune_declined_keeps_stale(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha", "beta": "beta"})
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    (tmp_path / "tools" / "beta.py").unlink()
    (tmp_path / "tools" / "__init__.py").write_text(
        "from .alpha import alpha\n", encoding="utf-8"
    )
    # --prune with --no declines the confirmation → stale kept.
    result = CliRunner().invoke(
        main, ["ingest", str(tmp_path), "--prune", "--no"]
    )
    assert result.exit_code == 0
    after = load_existing_yaml(tmp_path / "toolkit.yaml")
    assert "beta" in [e["name"] for e in after["tools"]]


def test_cli_force_overwrites_destroying_hand_edits(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha"})
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    target = tmp_path / "toolkit.yaml"
    data = load_existing_yaml(target)
    data["tools"][0]["description"] = "HAND EDITED"
    from toolbase.ingest import _dump_yaml
    _dump_yaml(data, target)
    # --force = full overwrite from scratch.
    result = CliRunner().invoke(
        main, ["ingest", str(tmp_path), "--force", "--no-input"]
    )
    assert result.exit_code == 0
    after = load_existing_yaml(target)
    # Hand edit gone; regenerated description.
    assert after["tools"][0]["description"] != "HAND EDITED"


def test_cli_comments_and_ordering_survive_merge(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha", "beta": "beta"})
    target = tmp_path / "toolkit.yaml"
    target.write_text(HAND_EDITED_YAML, encoding="utf-8")
    # Add gamma to source.
    pkg = tmp_path / "tools"
    _write_tool(pkg, "gamma", "gamma")
    (pkg / "__init__.py").write_text(
        "from .alpha import alpha\nfrom .beta import beta\n"
        "from .gamma import gamma\n",
        encoding="utf-8",
    )
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    text = target.read_text()
    # Comment preserved.
    assert "Carefully annotated" in text
    # Ordering preserved: beta still before alpha (the hand-arranged order).
    assert text.index("name: beta") < text.index("name: alpha")
    # gamma appended after both.
    assert text.index("name: gamma") > text.index("name: alpha")


def test_cli_scaffold_mode_unchanged_when_no_existing_yaml(tmp_path):
    _make_repo(tmp_path, {"alpha": "alpha"})
    result = CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    assert result.exit_code == 0
    assert "Merge complete" not in result.output
    assert (tmp_path / "toolkit.yaml").exists()


def test_cli_dropped_file_warning_fires_in_merge_mode(tmp_path):
    # The 0.5.3 dropped-file warning (a tool-shaped file whose module
    # path can't be resolved) must still fire when re-ingesting over an
    # existing yaml, not just on the initial scaffold.
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .good import good\n", encoding="utf-8")
    _write_tool(pkg, "good", "good")
    # sub/ has NO __init__.py → orphan's module path is unresolvable.
    _write_tool(pkg / "sub", "orphan", "orphan")
    # Scaffold, then re-ingest (merge). The default CliRunner captures
    # stderr into .output, where the dropped-file warning lands.
    CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    result = CliRunner().invoke(main, ["ingest", str(tmp_path), "--no-input"])
    assert result.exit_code == 0
    assert "could not be resolved" in result.output
