"""Unit tests for ``_toolkit_host._import_module_no_syspath`` and
``_import_explicit_tools``.

The explicit-form host loader is the runtime counterpart to ``ingest`` —
it imports each tool's module by file resolution against the toolkit
root, never adding the root to sys.path (HANDOFF gotcha #2).

Cover:

- Single-segment module imports.
- Nested package imports (a.b.c).
- Top-level package imports (the leaf is __init__.py).
- Invalid dotted paths rejected.
- Missing leaf file → ImportError with a clear message.
- ``_import_explicit_tools`` rejects non-BaseTool values.
- ``_import_explicit_tools`` instantiates BaseTool subclasses.
- ``_import_explicit_tools`` accepts BaseTool instances directly.
- sys.path is NOT mutated by these imports (regression guard for
  HANDOFF gotcha #2).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from toolbase._toolkit_host import (
    _import_explicit_tools,
    _import_module_no_syspath,
)


@pytest.fixture(autouse=True)
def _clean_sys_modules():
    """Snapshot sys.modules and restore after each test.

    The host loader registers modules into sys.modules under their
    dotted name; tests must not leak those into other tests.
    """
    snapshot = dict(sys.modules)
    yield
    for k in list(sys.modules):
        if k not in snapshot:
            del sys.modules[k]


def _make_pkg(root: Path, *paths: tuple[str, str]) -> None:
    for rel, content in paths:
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


class TestImportModuleNoSyspath:
    def test_simple_submodule(self, tmp_path):
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            ("pkg/leaf.py", "VALUE = 42\n"),
        )
        mod = _import_module_no_syspath("pkg.leaf", tmp_path)
        assert mod.VALUE == 42

    def test_nested_package(self, tmp_path):
        _make_pkg(
            tmp_path,
            ("a/__init__.py", ""),
            ("a/b/__init__.py", ""),
            ("a/b/c.py", "MARKER = 'c'\n"),
        )
        mod = _import_module_no_syspath("a.b.c", tmp_path)
        assert mod.MARKER == "c"

    def test_package_leaf_via_init(self, tmp_path):
        _make_pkg(
            tmp_path,
            ("only_pkg/__init__.py", "EXPORTED = 'init'\n"),
        )
        mod = _import_module_no_syspath("only_pkg", tmp_path)
        assert mod.EXPORTED == "init"

    def test_missing_intermediate_package(self, tmp_path):
        # `pkg/sub/leaf.py` exists but pkg/sub has no __init__.py.
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            ("pkg/sub/leaf.py", "X = 1\n"),
        )
        with pytest.raises(ImportError, match="no __init__.py"):
            _import_module_no_syspath("pkg.sub.leaf", tmp_path)

    def test_missing_leaf(self, tmp_path):
        _make_pkg(tmp_path, ("pkg/__init__.py", ""))
        with pytest.raises(ImportError, match="cannot find module"):
            _import_module_no_syspath("pkg.absent", tmp_path)

    def test_invalid_dotted_path(self, tmp_path):
        with pytest.raises(ImportError, match="invalid"):
            _import_module_no_syspath("pkg.123-bad", tmp_path)

    def test_does_not_mutate_syspath(self, tmp_path):
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            ("pkg/leaf.py", "X = 1\n"),
        )
        before = list(sys.path)
        _import_module_no_syspath("pkg.leaf", tmp_path)
        assert sys.path == before, "sys.path must not be mutated"
        assert str(tmp_path) not in sys.path
        assert str(tmp_path / "pkg") not in sys.path

    def test_relative_imports_work_inside_package(self, tmp_path):
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            ("pkg/util.py", "VALUE = 'util-value'\n"),
            ("pkg/main.py", "from pkg.util import VALUE\nLOADED = VALUE\n"),
        )
        mod = _import_module_no_syspath("pkg.main", tmp_path)
        assert mod.LOADED == "util-value"


class TestImportExplicitTools:
    def test_imports_basetool_instance(self, tmp_path):
        # Use @define_tool which produces a proper BaseTool instance.
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            (
                "pkg/mytool.py",
                "from orchestral import define_tool\n"
                "@define_tool\n"
                "def instance_tool():\n"
                "    \"\"\"Instance-form tool.\"\"\"\n"
                "    return 'ok'\n",
            ),
        )
        spec = [{"name": "instance_tool", "module": "pkg.mytool"}]
        tools = _import_explicit_tools(spec, tmp_path)
        from orchestral.tools.base.tool import BaseTool
        assert len(tools) == 1
        assert isinstance(tools[0], BaseTool)

    def test_instantiates_basetool_subclass(self, tmp_path):
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            (
                "pkg/cls.py",
                "from orchestral.tools import BaseTool\n"
                "class MyToolClass(BaseTool):\n"
                "    name: str = 'cls_tool'\n"
                "    description: str = 'A class-based tool.'\n"
                "    def _run(self, **kw):\n"
                "        return 'ok'\n",
            ),
        )
        spec = [{"name": "MyToolClass", "module": "pkg.cls"}]
        tools = _import_explicit_tools(spec, tmp_path)
        from orchestral.tools.base.tool import BaseTool
        assert len(tools) == 1
        assert isinstance(tools[0], BaseTool)

    def test_skips_non_basetool_attribute(self, tmp_path, capsys):
        # Bad entries no longer raise — they skip and emit a structured
        # tool_import_skipped JSON line on stderr (captured by the
        # per-toolkit log in production).
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            ("pkg/junk.py", "not_a_tool = 42\n"),
        )
        spec = [{"name": "not_a_tool", "module": "pkg.junk"}]
        tools = _import_explicit_tools(spec, tmp_path)
        assert tools == []
        err = capsys.readouterr().err
        assert '"event": "tool_import_skipped"' in err
        assert '"tool": "not_a_tool"' in err
        assert "not a BaseTool" in err

    def test_skips_missing_attribute(self, tmp_path, capsys):
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            ("pkg/empty.py", "# empty\n"),
        )
        spec = [{"name": "missing_attr", "module": "pkg.empty"}]
        tools = _import_explicit_tools(spec, tmp_path)
        assert tools == []
        err = capsys.readouterr().err
        assert '"event": "tool_import_skipped"' in err
        assert "has no attribute" in err

    def test_skips_entry_missing_module(self, tmp_path, capsys):
        spec = [{"name": "no_module"}]
        tools = _import_explicit_tools(spec, tmp_path)
        assert tools == []
        err = capsys.readouterr().err
        assert '"event": "tool_import_skipped"' in err
        assert "missing 'module'" in err

    def test_one_bad_tool_does_not_block_the_others(self, tmp_path, capsys):
        # The whole reason for skip-on-fail: a toolkit with one broken
        # entry must still come up with its other tools served. This is
        # the regression that motivated the change — a heptapod tool with
        # a missing state field was halting the entire toolkit import.
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            (
                "pkg/good.py",
                "from orchestral import define_tool\n"
                "@define_tool\n"
                "def good_tool():\n"
                "    \"\"\"works.\"\"\"\n"
                "    return 'ok'\n",
            ),
            ("pkg/bad.py", "not_a_tool = 42\n"),
        )
        spec = [
            {"name": "good_tool", "module": "pkg.good"},
            {"name": "not_a_tool", "module": "pkg.bad"},
        ]
        tools = _import_explicit_tools(spec, tmp_path)
        from orchestral.tools.base.tool import BaseTool
        assert len(tools) == 1
        assert isinstance(tools[0], BaseTool)
        err = capsys.readouterr().err
        assert '"event": "tool_import_skipped"' in err
        assert '"tool": "not_a_tool"' in err

    def test_passes_state_config_to_subclass_constructor(self, tmp_path):
        # A class with a no-default StateField must receive its value
        # via state_config; toolbase forwards matching entries as kwargs.
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            (
                "pkg/stateful.py",
                "from orchestral.tools import BaseTool\n"
                "from orchestral.tools.base.field_utils import StateField\n"
                "class StatefulTool(BaseTool):\n"
                "    name: str = 'stateful'\n"
                "    description: str = 'needs state'\n"
                "    base_directory: str = StateField(description='wd')\n"
                "    def _run(self, **kw):\n"
                "        return self.base_directory\n",
            ),
        )
        spec = [{"name": "StatefulTool", "module": "pkg.stateful"}]
        tools = _import_explicit_tools(
            spec, tmp_path, state_config={"base_directory": "/sandbox"}
        )
        assert len(tools) == 1
        assert tools[0].base_directory == "/sandbox"

    def test_subclass_missing_required_state_is_skipped(self, tmp_path, capsys):
        # Without state_config, a class with a required-no-default StateField
        # fails to construct (pydantic ValidationError). Toolbase skips it
        # with a tool_import_skipped warning rather than halting the whole
        # toolkit, so other tools in the toolkit still come up.
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            (
                "pkg/stateful.py",
                "from orchestral.tools import BaseTool\n"
                "from orchestral.tools.base.field_utils import StateField\n"
                "class StatefulTool(BaseTool):\n"
                "    name: str = 'stateful'\n"
                "    description: str = 'needs state'\n"
                "    base_directory: str = StateField(description='wd')\n"
                "    def _run(self, **kw):\n"
                "        return 'ok'\n",
            ),
        )
        spec = [{"name": "StatefulTool", "module": "pkg.stateful"}]
        tools = _import_explicit_tools(spec, tmp_path)
        assert tools == []
        err = capsys.readouterr().err
        assert '"event": "tool_import_skipped"' in err
        assert '"tool": "StatefulTool"' in err
        assert "base_directory" in err

    def test_state_config_filtered_to_declared_state_fields(self, tmp_path):
        # Extra keys in state_config that don't correspond to declared
        # StateFields must not be forwarded to the constructor (orchestral
        # BaseTool sets `extra="allow"`, so they'd land as instance attrs
        # otherwise, which would silently mask author mistakes).
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            (
                "pkg/stateful.py",
                "from orchestral.tools import BaseTool\n"
                "from orchestral.tools.base.field_utils import StateField\n"
                "class StatefulTool(BaseTool):\n"
                "    name: str = 'stateful'\n"
                "    description: str = 'needs state'\n"
                "    base_directory: str = StateField(description='wd')\n"
                "    def _run(self, **kw):\n"
                "        return self.base_directory\n",
            ),
        )
        spec = [{"name": "StatefulTool", "module": "pkg.stateful"}]
        tools = _import_explicit_tools(
            spec,
            tmp_path,
            state_config={
                "base_directory": "/sandbox",
                "unrelated_key": "ignored",
            },
        )
        assert tools[0].base_directory == "/sandbox"
        assert not hasattr(tools[0], "unrelated_key")

    def test_handles_multiple_entries(self, tmp_path):
        _make_pkg(
            tmp_path,
            ("pkg/__init__.py", ""),
            (
                "pkg/a.py",
                "from orchestral import define_tool\n"
                "@define_tool\n"
                "def a_inst():\n"
                "    \"\"\"a-desc\"\"\"\n"
                "    return 'a'\n",
            ),
            (
                "pkg/b.py",
                "from orchestral.tools import BaseTool\n"
                "class B(BaseTool):\n"
                "    name: str = 'b'\n"
                "    description: str = 'b-desc'\n"
                "    def _run(self, **kw):\n"
                "        return 'b'\n",
            ),
        )
        spec = [
            {"name": "a_inst", "module": "pkg.a"},
            {"name": "B", "module": "pkg.b"},
        ]
        tools = _import_explicit_tools(spec, tmp_path)
        assert len(tools) == 2
