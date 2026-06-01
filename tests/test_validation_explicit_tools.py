"""Unit tests for the explicit-form ``tools:`` schema and validation rules
introduced for ``toolbase ingest``.

Cover:

- ``ToolDefinition`` rejects entries with both ``function`` and ``module``.
- Rejects entries with neither.
- Implicit form requires ``description``; explicit form does not.
- ``validate_toolkit`` enforces duplicate detection across the merged
  ``tools:`` list.
- Path-residence: explicit-form ``module:`` outside toolkit root and not
  in requirements.txt → error.
- Path-residence: same module declared in requirements.txt → ok.
- ``tools/`` directory not required when all tools are explicit-form.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from toolbase.validation import (
    ToolDefinition,
    ToolkitMetadata,
    validate_toolkit,
)


def _scaffold_toolkit(
    root: Path,
    yaml_text: str,
    *,
    with_tools_dir: bool = True,
    with_mcp: bool = True,
    requirements: str = "orchestral-ai>=1.0.0\n",
) -> None:
    (root / "toolkit.yaml").write_text(yaml_text, encoding="utf-8")
    if with_tools_dir:
        (root / "tools").mkdir(exist_ok=True)
        (root / "tools" / "__init__.py").write_text(
            "from tools.example_tool import example_tool\n",
            encoding="utf-8",
        )
        (root / "tools" / "example_tool.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def example_tool():\n"
            "    \"\"\"Example.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
    if with_mcp:
        (root / "mcp").mkdir(exist_ok=True)
        (root / "mcp" / "__init__.py").write_text("", encoding="utf-8")
        (root / "mcp" / "server_stdio.py").write_text("", encoding="utf-8")
    (root / "requirements.txt").write_text(requirements, encoding="utf-8")
    (root / "README.md").write_text("# Toolkit\n", encoding="utf-8")


class TestToolDefinitionSchema:
    def test_function_only_ok(self):
        td = ToolDefinition(
            name="my_tool",
            function="tools.my_tool",
            description="Does a thing.",
        )
        assert td.function == "tools.my_tool"
        assert td.module is None

    def test_module_only_ok(self):
        td = ToolDefinition(
            name="my_tool",
            module="heptapod.scattering.amplitudes",
        )
        assert td.module == "heptapod.scattering.amplitudes"
        assert td.function is None

    def test_module_with_optional_description(self):
        td = ToolDefinition(
            name="my_tool",
            module="pkg.mod",
            description="Override description.",
        )
        assert td.description == "Override description."

    def test_both_function_and_module_rejected(self):
        with pytest.raises(ValidationError, match="both 'function' and 'module'"):
            ToolDefinition(
                name="bad",
                function="tools.bad",
                module="pkg.bad",
                description="Both.",
            )

    def test_neither_rejected(self):
        with pytest.raises(ValidationError, match="needs either"):
            ToolDefinition(name="orphan")

    def test_implicit_form_requires_description(self):
        with pytest.raises(ValidationError, match="'description' is required"):
            ToolDefinition(name="implicit", function="tools.x")

    def test_explicit_form_description_optional(self):
        # No description; should pass.
        td = ToolDefinition(name="explicit", module="pkg.x")
        assert td.description is None

    def test_display_name_optional_and_round_trips(self):
        """``display_name:`` is an optional MCP-wire name override.
        When present, it's what the agent sees (after the toolkit's
        ``<name>__`` prefix); when absent, the host derives a default
        from the class name."""
        td = ToolDefinition(
            name="InspireSearchTool",
            module="pkg.x",
            display_name="search_papers",
        )
        assert td.display_name == "search_papers"

    def test_display_name_defaults_to_none(self):
        td = ToolDefinition(name="my_tool", module="pkg.x")
        assert td.display_name is None


class TestToolkitMetadataIntegration:
    def test_loads_explicit_only_yaml(self):
        meta = ToolkitMetadata(
            name="hep-tk",
            version="0.1.0",
            description="HEP toolkit.",
            author="Tony",
            tools=[
                {"name": "amp", "module": "heptapod.amplitudes"},
                {"name": "obs", "module": "heptapod.observables",
                 "description": "Observables."},
            ],
        )
        assert len(meta.tools) == 2
        assert meta.tools[0].module == "heptapod.amplitudes"
        assert meta.tools[1].description == "Observables."

    def test_loads_mixed_yaml(self):
        meta = ToolkitMetadata(
            name="mixed-tk",
            version="0.1.0",
            description="Mixed-form toolkit.",
            author="Author",
            tools=[
                {"name": "old", "function": "tools.old",
                 "description": "Old style."},
                {"name": "new", "module": "tk.new"},
            ],
        )
        assert meta.tools[0].function == "tools.old"
        assert meta.tools[1].module == "tk.new"


class TestValidateToolkitExplicit:
    def test_explicit_only_no_tools_dir_required(self, tmp_path):
        # All-explicit: no tools/ dir required.
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pkg" / "mod.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    \"\"\"Doc.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        yaml_text = (
            "name: explicit-tk\n"
            "version: 0.1.0\n"
            "description: Explicit-form only.\n"
            "author: A\n"
            "category: other\n"
            "tools:\n"
            "  - name: my_tool\n"
            "    module: pkg.mod\n"
        )
        _scaffold_toolkit(
            tmp_path, yaml_text,
            with_tools_dir=False,  # not needed for all-explicit
        )
        result = validate_toolkit(tmp_path)
        assert result.is_valid, f"errors: {result.errors}"

    def test_explicit_module_outside_root_rejected(self, tmp_path):
        # No matching module under root, not in requirements.
        yaml_text = (
            "name: missing-tk\n"
            "version: 0.1.0\n"
            "description: External module not declared.\n"
            "author: A\n"
            "category: other\n"
            "tools:\n"
            "  - name: external_tool\n"
            "    module: nonexistent_pkg.module\n"
        )
        _scaffold_toolkit(
            tmp_path, yaml_text,
            with_tools_dir=False,
            requirements="orchestral-ai>=1.0.0\n",
        )
        result = validate_toolkit(tmp_path)
        assert not result.is_valid
        assert any(
            "not under the toolkit root" in e
            for e in result.errors
        )

    def test_explicit_module_declared_in_requirements_ok(self, tmp_path):
        # External module IS declared as a dep.
        yaml_text = (
            "name: declared-tk\n"
            "version: 0.1.0\n"
            "description: External dep declared.\n"
            "author: A\n"
            "category: other\n"
            "tools:\n"
            "  - name: dep_tool\n"
            "    module: declared_pkg.module\n"
        )
        _scaffold_toolkit(
            tmp_path, yaml_text,
            with_tools_dir=False,
            requirements="orchestral-ai>=1.0.0\ndeclared_pkg>=0.1\n",
        )
        result = validate_toolkit(tmp_path)
        # Should pass (the module is in requirements; tarball will install it).
        assert result.is_valid, f"errors: {result.errors}"

    def test_duplicate_explicit_entries_rejected(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pkg" / "mod.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    \"\"\"Doc.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        yaml_text = (
            "name: dup-tk\n"
            "version: 0.1.0\n"
            "description: Duplicate entries.\n"
            "author: A\n"
            "category: other\n"
            "tools:\n"
            "  - name: my_tool\n"
            "    module: pkg.mod\n"
            "  - name: my_tool\n"
            "    module: pkg.mod\n"
        )
        _scaffold_toolkit(tmp_path, yaml_text, with_tools_dir=False)
        result = validate_toolkit(tmp_path)
        assert not result.is_valid
        assert any("Duplicate" in e for e in result.errors)

    def test_ingested_layout_no_mcp_dir_passes(self, tmp_path):
        """Ingested toolkits (no ``mcp/`` directory at all) pass validate.

        0.5.1 and earlier hard-errored on missing ``mcp/server_stdio.py`` and
        ``mcp/__init__.py`` even though the serve orchestrator manages MCP
        transport itself. 0.5.2 drops that rule entirely. An explicit-form
        toolkit with no ``mcp/`` dir is a valid ingested layout (e.g.
        HEPTAPOD).
        """
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pkg" / "mod.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    \"\"\"Doc.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        yaml_text = (
            "name: ingested-tk\n"
            "version: 0.1.0\n"
            "description: Ingested-style toolkit with no mcp/ dir.\n"
            "author: A\n"
            "category: other\n"
            "tools:\n"
            "  - name: my_tool\n"
            "    module: pkg.mod\n"
        )
        _scaffold_toolkit(
            tmp_path, yaml_text,
            with_tools_dir=False,
            with_mcp=False,  # explicitly: NO mcp/ dir at all
        )
        # Sanity: the mcp dir really does not exist.
        assert not (tmp_path / "mcp").exists()
        result = validate_toolkit(tmp_path)
        assert result.is_valid, f"errors: {result.errors}"
        # And no error mentions mcp/.
        assert not any("mcp/" in e for e in result.errors)
        assert not any("MCP file" in e for e in result.errors)

    def test_scaffolded_layout_with_mcp_dir_still_passes(self, tmp_path):
        """Regression: toolkits scaffolded by ``toolbase init`` (with the
        ``mcp/`` directory present) must keep validating cleanly. The 0.5.2
        rule drop is purely additive — the presence of the scaffolded
        files is neither required nor objected to.
        """
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "pkg" / "mod.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    \"\"\"Doc.\"\"\"\n"
            "    pass\n",
            encoding="utf-8",
        )
        yaml_text = (
            "name: scaffolded-tk\n"
            "version: 0.1.0\n"
            "description: Toolkit that still has mcp/ scaffolding.\n"
            "author: A\n"
            "category: other\n"
            "tools:\n"
            "  - name: my_tool\n"
            "    module: pkg.mod\n"
        )
        _scaffold_toolkit(
            tmp_path, yaml_text,
            with_tools_dir=False,
            with_mcp=True,  # init-style: mcp/ files present
        )
        result = validate_toolkit(tmp_path)
        assert result.is_valid, f"errors: {result.errors}"

    def test_invalid_module_path_rejected(self, tmp_path):
        yaml_text = (
            "name: bad-mod-tk\n"
            "version: 0.1.0\n"
            "description: Invalid module path.\n"
            "author: A\n"
            "category: other\n"
            "tools:\n"
            "  - name: bad\n"
            "    module: 'pkg.123-not-an-identifier'\n"
        )
        _scaffold_toolkit(tmp_path, yaml_text, with_tools_dir=False)
        result = validate_toolkit(tmp_path)
        assert not result.is_valid
        assert any(
            "not a valid dotted identifier" in e
            for e in result.errors
        )
