"""Unit tests for ``toolbase.ingest.extract_tools_from_file``.

The AST detector is the load-bearing piece — false negatives mean
authors lose tools silently in their toolkit.yaml; false positives
mean fake entries that fail at validate. Cover:

- ``@define_tool`` direct, aliased, ``@define_tool()`` call form.
- ``@orchestral.define_tool`` attribute-access form.
- ``BaseTool`` direct, aliased, ``orchestral.tools.BaseTool`` form.
- Multi-base inheritance.
- TYPE_CHECKING block exclusion.
- Nested-scope exclusion (only top-level definitions count).
- Docstring extraction (first line, fallback to "(no description)").
- Source line capture.
- Module path resolution from package layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase.ingest import (
    ToolDescriptor,
    discover_tools,
    extract_tools_from_file,
)


def _make_pkg(root: Path, *files_with_content: tuple[str, str]) -> None:
    """Helper: create a package layout from (path, content) pairs."""
    for rel_path, content in files_with_content:
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


def _emit_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "__init__.py").write_text("", encoding="utf-8")


class TestDecoratorDetection:
    def test_direct_define_tool(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    \"\"\"Does a thing.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "my_tool"
        assert tools[0].module == "pkg.mod"
        assert tools[0].kind == "function"
        assert tools[0].description == "Does a thing."

    def test_aliased_define_tool(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool as dt\n"
            "@dt\n"
            "def aliased_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "aliased_tool"

    def test_call_form_decorator(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral.tools import define_tool\n"
            "@define_tool(state=['api_key'])\n"
            "def call_form_tool(api_key, q):\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "call_form_tool"

    def test_attribute_access_decorator(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "import orchestral\n"
            "@orchestral.define_tool\n"
            "def attr_form_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "attr_form_tool"

    def test_unrelated_decorator_ignored(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from functools import lru_cache\n"
            "@lru_cache\n"
            "def cached_func():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []

    def test_async_function_with_decorator(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "async def async_tool():\n"
            "    \"\"\"Async tool.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "async_tool"


class TestBaseToolDetection:
    def test_direct_basetool_subclass(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral.tools import BaseTool\n"
            "class MyTool(BaseTool):\n"
            "    \"\"\"A class-based tool.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "MyTool"
        assert tools[0].kind == "class"
        assert tools[0].description == "A class-based tool."

    def test_aliased_basetool(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral.tools import BaseTool as BT\n"
            "class AliasedTool(BT):\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "AliasedTool"

    def test_deep_import_path(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral.tools.base.tool import BaseTool\n"
            "class DeepImportTool(BaseTool):\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "DeepImportTool"

    def test_multi_base_with_basetool(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral.tools import BaseTool\n"
            "class Mixin: pass\n"
            "class Combined(Mixin, BaseTool):\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        names = {t.name for t in tools}
        assert "Combined" in names
        assert "Mixin" not in names

    def test_unrelated_class_ignored(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "class NotATool:\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []


class TestExclusions:
    def test_type_checking_block_excluded(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from typing import TYPE_CHECKING\n"
            "from orchestral import define_tool\n"
            "if TYPE_CHECKING:\n"
            "    @define_tool\n"
            "    def type_only_tool():\n"
            "        pass\n"
            "@define_tool\n"
            "def real_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        names = {t.name for t in tools}
        assert "real_tool" in names
        assert "type_only_tool" not in names

    def test_nested_function_definition_excluded(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "def outer():\n"
            "    @define_tool\n"
            "    def nested_tool():\n"
            "        pass\n"
            "    return nested_tool\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []

    def test_class_method_excluded(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "class Container:\n"
            "    @define_tool\n"
            "    def method(self):\n"
            "        pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []


class TestDocstringHandling:
    def test_first_line_extracted(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    \"\"\"Short summary.\n\n"
            "    Long detailed description across multiple lines.\n"
            "    \"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools[0].description == "Short summary."

    def test_missing_docstring_falls_back(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def no_docs():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools[0].description == "(no description)"

    def test_empty_docstring_falls_back(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def empty_docs():\n"
            "    \"\"\"   \"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools[0].description == "(no description)"


class TestModulePathResolution:
    def test_nested_package(self, tmp_path):
        _emit_init(tmp_path / "outer")
        _emit_init(tmp_path / "outer" / "inner")
        f = tmp_path / "outer" / "inner" / "mod.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def nested_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools[0].module == "outer.inner.mod"

    def test_init_file_module_path(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "__init__.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def init_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert len(tools) == 1
        assert tools[0].module == "pkg"

    def test_non_package_file_skipped(self, tmp_path):
        # A .py file in a directory without __init__.py shouldn't yield
        # a usable module path. The discoverer skips it.
        f = tmp_path / "loose" / "mod.py"
        f.parent.mkdir()
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def loose_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []

    def test_top_level_init_returns_empty_module_path_skipped(self, tmp_path):
        # __init__.py at the toolkit root would have an empty dotted path;
        # we skip it (no useful import path).
        f = tmp_path / "__init__.py"
        f.write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def root_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []


class TestSourceLocation:
    def test_line_number_captured(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text(
            "# comment\n"
            "# another\n"
            "from orchestral import define_tool\n"
            "\n"
            "\n"
            "@define_tool\n"
            "def line_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        tools = extract_tools_from_file(f, tmp_path)
        # The function definition is on line 7 (1-indexed).
        assert tools[0].source_line == 7
        assert tools[0].source_path == f


class TestSyntaxRobustness:
    def test_syntax_error_yields_empty(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "broken.py"
        f.write_text("def broken(:\n    pass\n", encoding="utf-8")
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []

    def test_unreadable_file_yields_empty(self, tmp_path, monkeypatch):
        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text("# fine\n", encoding="utf-8")

        def boom(*a, **k):
            raise OSError("nope")

        monkeypatch.setattr(Path, "read_text", boom)
        tools = extract_tools_from_file(f, tmp_path)
        assert tools == []


class TestDiscoveryDriver:
    def test_discover_walks_full_tree(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        _emit_init(tmp_path / "pkg" / "sub")
        (tmp_path / "pkg" / "a.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def a_tool(): pass\n",
            encoding="utf-8",
        )
        (tmp_path / "pkg" / "sub" / "b.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def b_tool(): pass\n",
            encoding="utf-8",
        )
        tools = discover_tools(tmp_path)
        names = [(t.module, t.name) for t in tools]
        assert ("pkg.a", "a_tool") in names
        assert ("pkg.sub.b", "b_tool") in names

    def test_discover_sorts_deterministically(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        (tmp_path / "pkg" / "z.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def z(): pass\n",
            encoding="utf-8",
        )
        (tmp_path / "pkg" / "a.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def a(): pass\n",
            encoding="utf-8",
        )
        tools = discover_tools(tmp_path)
        keys = [(t.module, t.name) for t in tools]
        assert keys == sorted(keys)

    def test_discover_skips_test_files(self, tmp_path):
        _emit_init(tmp_path / "pkg")
        (tmp_path / "pkg" / "real.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def real_tool(): pass\n",
            encoding="utf-8",
        )
        (tmp_path / "pkg" / "test_real.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def fake_tool(): pass\n",
            encoding="utf-8",
        )
        tools = discover_tools(tmp_path)
        names = {t.name for t in tools}
        assert "real_tool" in names
        assert "fake_tool" not in names


class TestDiscoverToolsAndDrops:
    """Issue #1 regression: files whose dotted module path can't be
    resolved must surface as :class:`DroppedFile` entries when they
    contain tool-shaped definitions, not silently disappear.
    """

    def test_collects_drop_with_reason_for_missing_init(self, tmp_path):
        from toolbase.ingest import discover_tools_and_drops

        _emit_init(tmp_path / "pkg")
        sub = tmp_path / "pkg" / "subdir"
        sub.mkdir()
        # Missing __init__.py on purpose.
        (sub / "mod.py").write_text(
            "from orchestral.tools import BaseTool\n"
            "class T(BaseTool): pass\n",
            encoding="utf-8",
        )
        tools, dropped = discover_tools_and_drops(tmp_path)
        assert tools == []
        assert len(dropped) == 1
        d = dropped[0]
        assert d.source_path.name == "mod.py"
        assert "missing __init__.py" in d.reason
        assert "pkg/subdir" in d.reason or "pkg\\subdir" in d.reason

    def test_no_drop_for_plain_file_without_tools(self, tmp_path):
        from toolbase.ingest import discover_tools_and_drops

        _emit_init(tmp_path / "pkg")
        sub = tmp_path / "pkg" / "subdir"
        sub.mkdir()
        (sub / "plain.py").write_text(
            "def helper(): return 42\n",
            encoding="utf-8",
        )
        tools, dropped = discover_tools_and_drops(tmp_path)
        # Plain helper: silent skip is correct.
        assert tools == []
        assert dropped == []

    def test_no_drop_when_module_paths_resolve(self, tmp_path):
        from toolbase.ingest import discover_tools_and_drops

        _emit_init(tmp_path / "pkg")
        _emit_init(tmp_path / "pkg" / "sub")
        (tmp_path / "pkg" / "sub" / "mod.py").write_text(
            "from orchestral.tools import BaseTool\n"
            "class T(BaseTool): pass\n",
            encoding="utf-8",
        )
        tools, dropped = discover_tools_and_drops(tmp_path)
        assert dropped == []
        assert len(tools) == 1
        assert tools[0].module == "pkg.sub.mod"

    def test_drops_sorted_deterministically(self, tmp_path):
        from toolbase.ingest import discover_tools_and_drops

        _emit_init(tmp_path / "pkg")
        sub = tmp_path / "pkg" / "subdir"
        sub.mkdir()
        for name in ("zzz.py", "aaa.py", "mmm.py"):
            (sub / name).write_text(
                "from orchestral.tools import BaseTool\n"
                "class T(BaseTool): pass\n",
                encoding="utf-8",
            )
        _tools, dropped = discover_tools_and_drops(tmp_path)
        names = [d.source_path.name for d in dropped]
        assert names == sorted(names)


class TestModulePathReason:
    """Direct tests for ``_module_path_for_file_with_reason``."""

    def test_returns_none_with_reason_for_missing_init(self, tmp_path):
        from toolbase.ingest import _module_path_for_file_with_reason

        _emit_init(tmp_path / "pkg")
        sub = tmp_path / "pkg" / "subdir"
        sub.mkdir()
        f = sub / "mod.py"
        f.write_text("", encoding="utf-8")
        module, reason = _module_path_for_file_with_reason(f, tmp_path)
        assert module is None
        assert reason is not None
        assert "missing __init__.py" in reason

    def test_returns_path_no_reason_when_resolvable(self, tmp_path):
        from toolbase.ingest import _module_path_for_file_with_reason

        _emit_init(tmp_path / "pkg")
        f = tmp_path / "pkg" / "mod.py"
        f.write_text("", encoding="utf-8")
        module, reason = _module_path_for_file_with_reason(f, tmp_path)
        assert module == "pkg.mod"
        assert reason is None
