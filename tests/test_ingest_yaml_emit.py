"""Unit tests for ``toolbase.ingest.emit_toolkit_yaml`` and the
``ingest`` entry point's overwrite handling.

The emission produces the file authors edit and version-control. We
care about:

- Yaml structure matches the explicit form schema.
- Placeholder metadata gets injected when no existing yaml exists.
- Existing top-level metadata is preserved when overwriting.
- The ``tools:`` block is always rewritten (never merged with old entries).
- Dry-run never writes.
- Overwrite refusal returns the right flag without touching the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ruamel.yaml import YAML

from toolbase.ingest import (
    IngestResult,
    ToolDescriptor,
    emit_toolkit_yaml,
    ingest,
    load_existing_yaml,
)


def _read_yaml(path: Path) -> dict:
    yaml = YAML()
    with path.open("r", encoding="utf-8") as f:
        return yaml.load(f)


def _td(module: str, name: str, desc: str = "x") -> ToolDescriptor:
    return ToolDescriptor(
        module=module,
        name=name,
        description=desc,
        source_path=Path("/tmp/fake.py"),
        source_line=1,
        kind="function",
    )


class TestEmit:
    def test_emit_basic_structure(self, tmp_path):
        target = tmp_path / "toolkit.yaml"
        tools = [_td("pkg.a", "tool_a"), _td("pkg.b", "tool_b", "Does b things.")]
        emit_toolkit_yaml(tools, target)
        assert target.exists()
        data = _read_yaml(target)
        assert "tools" in data
        assert len(data["tools"]) == 2
        assert data["tools"][0]["module"] == "pkg.a"
        assert data["tools"][0]["name"] == "tool_a"
        assert data["tools"][1]["description"] == "Does b things."

    def test_emit_includes_placeholder_metadata(self, tmp_path):
        target = tmp_path / "toolkit.yaml"
        emit_toolkit_yaml([_td("a", "t")], target)
        data = _read_yaml(target)
        assert data["name"] == "TODO_set_toolkit_name"
        assert data["version"] == "0.1.0"
        assert data["author"] == "TODO_your_name"
        assert data["category"] == "other"

    def test_emit_creates_parent_dir(self, tmp_path):
        target = tmp_path / "nested" / "deep" / "toolkit.yaml"
        emit_toolkit_yaml([_td("a", "t")], target)
        assert target.exists()

    def test_emit_with_no_tools(self, tmp_path):
        # Edge case: walker found nothing. Still emit a valid yaml.
        target = tmp_path / "toolkit.yaml"
        emit_toolkit_yaml([], target)
        data = _read_yaml(target)
        assert data["tools"] == [] or list(data["tools"]) == []


class TestEmitWithExistingMetadata:
    def test_preserves_metadata_overwrites_tools(self, tmp_path):
        target = tmp_path / "toolkit.yaml"
        target.write_text(
            "name: real-name\n"
            "version: 1.2.3\n"
            "description: Author wrote this.\n"
            "author: Real Author\n"
            "category: hep\n"
            "tools:\n"
            "  - module: stale.module\n"
            "    name: stale_tool\n"
            "    description: stale\n",
            encoding="utf-8",
        )
        existing = load_existing_yaml(target)
        emit_toolkit_yaml(
            [_td("fresh.mod", "fresh_tool")], target, existing=existing
        )
        data = _read_yaml(target)
        assert data["name"] == "real-name"
        assert data["version"] == "1.2.3"
        assert data["description"] == "Author wrote this."
        assert data["category"] == "hep"
        # tools always rewritten
        assert len(data["tools"]) == 1
        assert data["tools"][0]["module"] == "fresh.mod"

    def test_existing_without_tools_field_still_works(self, tmp_path):
        target = tmp_path / "toolkit.yaml"
        target.write_text(
            "name: partial\n"
            "version: 0.1.0\n",
            encoding="utf-8",
        )
        existing = load_existing_yaml(target)
        emit_toolkit_yaml([_td("a.b", "tool")], target, existing=existing)
        data = _read_yaml(target)
        assert data["name"] == "partial"
        assert len(data["tools"]) == 1


class TestLoadExisting:
    def test_load_missing_returns_none(self, tmp_path):
        assert load_existing_yaml(tmp_path / "nope.yaml") is None

    def test_load_invalid_returns_none(self, tmp_path):
        target = tmp_path / "toolkit.yaml"
        target.write_text("this is: not\n  valid: [yaml\n", encoding="utf-8")
        assert load_existing_yaml(target) is None

    def test_load_non_dict_returns_none(self, tmp_path):
        target = tmp_path / "toolkit.yaml"
        target.write_text("- just a list\n- of things\n", encoding="utf-8")
        assert load_existing_yaml(target) is None


class TestIngestEntryPoint:
    def _make_simple_repo(self, root: Path) -> None:
        (root / "pkg").mkdir()
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (root / "pkg" / "tool_mod.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    \"\"\"Does a thing.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )

    def test_dry_run_writes_nothing(self, tmp_path):
        self._make_simple_repo(tmp_path)
        result = ingest(tmp_path, output=None, overwrite=False, dry_run=True)
        assert isinstance(result, IngestResult)
        assert result.wrote is False
        assert len(result.tools) == 1
        assert not (tmp_path / "toolkit.yaml").exists()

    def test_writes_when_no_existing(self, tmp_path):
        self._make_simple_repo(tmp_path)
        result = ingest(tmp_path, output=None, overwrite=False, dry_run=False)
        assert result.wrote is True
        assert (tmp_path / "toolkit.yaml").exists()

    def test_merges_when_existing_and_no_overwrite(self, tmp_path):
        # 0.6.1: an existing toolkit.yaml without --force triggers MERGE
        # mode (not the old refuse-to-overwrite). The discovered tool is
        # new (the existing yaml has no tools: key), so it's appended;
        # the existing metadata key is preserved.
        self._make_simple_repo(tmp_path)
        (tmp_path / "toolkit.yaml").write_text("name: existing\n", encoding="utf-8")
        result = ingest(tmp_path, output=None, overwrite=False, dry_run=False)
        assert result.merged is True
        assert result.wrote is True
        assert result.overwrite_blocked is False
        data = _read_yaml(tmp_path / "toolkit.yaml")
        assert data["name"] == "existing"  # metadata preserved
        assert len(data["tools"]) == 1     # discovered tool appended
        assert result.merge is not None
        assert len(result.merge.added) == 1

    def test_overwrites_when_flag_set(self, tmp_path):
        self._make_simple_repo(tmp_path)
        (tmp_path / "toolkit.yaml").write_text(
            "name: keep-me\nversion: 9.9.9\n", encoding="utf-8"
        )
        result = ingest(tmp_path, output=None, overwrite=True, dry_run=False)
        assert result.wrote is True
        data = _read_yaml(tmp_path / "toolkit.yaml")
        # Existing metadata preserved when overwriting
        assert data["name"] == "keep-me"
        assert data["version"] == "9.9.9"
        # Tools added
        assert len(data["tools"]) == 1

    def test_custom_output_path(self, tmp_path):
        self._make_simple_repo(tmp_path)
        out = tmp_path / "elsewhere" / "manifest.yaml"
        result = ingest(tmp_path, output=out, overwrite=False, dry_run=False)
        assert result.wrote is True
        assert out.exists()
        assert not (tmp_path / "toolkit.yaml").exists()

    def test_requirements_present_flag(self, tmp_path):
        self._make_simple_repo(tmp_path)
        (tmp_path / "requirements.txt").write_text("orchestral-ai\n", encoding="utf-8")
        result = ingest(tmp_path, output=None, overwrite=False, dry_run=True)
        assert result.requirements_present is True

    def test_requirements_missing_flag(self, tmp_path):
        self._make_simple_repo(tmp_path)
        result = ingest(tmp_path, output=None, overwrite=False, dry_run=True)
        assert result.requirements_present is False
