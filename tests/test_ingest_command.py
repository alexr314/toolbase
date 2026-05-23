"""Unit tests for the ``toolbase ingest`` Click command.

Exercises the CLI surface: flag handling, prompt-mode integration,
existing-yaml behavior, dry-run, output-path override, requirements.txt
warning. The actual discovery + emission is unit-tested elsewhere; here
we only care about the command wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase.cli import main


def _make_repo_with_one_tool(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(
        "from orchestral import define_tool\n"
        "@define_tool\n"
        "def my_tool():\n"
        "    \"\"\"Does the thing.\"\"\"\n"
        "    pass\n",
        encoding="utf-8",
    )


class TestBareInvocation:
    def test_writes_toolkit_yaml(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "toolkit.yaml").exists()
        assert "Found 1 tools" in result.output
        assert "pkg.mod.my_tool" in result.output

    def test_warns_on_missing_requirements(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert "requirements.txt not found" in result.output.lower() or \
               "WARNING" in result.output

    def test_no_warning_when_requirements_exists(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "requirements.txt").write_text(
            "orchestral-ai\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert "requirements.txt not found" not in result.output.lower()


class TestDryRun:
    def test_dry_run_does_not_write(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--dry-run"]
        )
        assert result.exit_code == 0
        assert not (tmp_path / "toolkit.yaml").exists()
        assert "dry-run" in result.output.lower()


class TestExistingYamlBehavior:
    def test_merges_in_no_input_mode(self, tmp_path):
        # 0.6.1: an existing toolkit.yaml without --force is no longer a
        # consequential overwrite — it's a non-destructive MERGE, so
        # --no-input proceeds (no prompt to abort on). Metadata is
        # preserved and the discovered tool is appended.
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "toolkit.yaml").write_text(
            "name: existing\nversion: 1.0.0\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0, result.output
        content = (tmp_path / "toolkit.yaml").read_text()
        assert "existing" in content       # metadata preserved
        assert "my_tool" in content        # tool merged in
        assert "Merge complete" in result.output

    def test_force_overwrites(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "toolkit.yaml").write_text(
            "name: existing\nversion: 9.9.9\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--force", "--no-input"]
        )
        assert result.exit_code == 0
        content = (tmp_path / "toolkit.yaml").read_text()
        # Original metadata preserved, but tools block now present.
        assert "existing" in content
        assert "tools:" in content
        assert "my_tool" in content

    def test_yes_flag_overwrites(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "toolkit.yaml").write_text(
            "name: existing\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--yes"]
        )
        assert result.exit_code == 0
        assert "my_tool" in (tmp_path / "toolkit.yaml").read_text()


class TestOutputOption:
    def test_custom_output_path(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        out = tmp_path / "elsewhere" / "manifest.yaml"
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "ingest", str(tmp_path),
                "--output", str(out),
                "--no-input",
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert not (tmp_path / "toolkit.yaml").exists()


class TestEmptyRepo:
    def test_no_tools_still_writes(self, tmp_path):
        # No tools at all — ingest still emits a yaml skeleton (with
        # empty tools list) so the author has somewhere to start.
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0
        assert (tmp_path / "toolkit.yaml").exists()
        assert "Found 0 tools" in result.output


class TestSummaryOutput:
    def test_lists_decorated_functions_separately(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        # Add a class-based tool too.
        (tmp_path / "pkg" / "klass.py").write_text(
            "from orchestral.tools import BaseTool\n"
            "class MyClassTool(BaseTool):\n"
            "    pass\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0
        assert "Decorated functions" in result.output
        assert "BaseTool subclasses" in result.output
        assert "my_tool" in result.output
        assert "MyClassTool" in result.output


class TestNextStepsBanner:
    """Issue #4 / #5: the Next-steps banner should orient the author on
    registration without commanding it as a required step. After 0.5.5's
    publish auto-register, the banner is validate -> login -> publish,
    with create/web-UI offered as an optional "reserve the name first"
    parenthetical (not a commanded step that implies publish 404s).
    """

    def test_mentions_optional_registration(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0, result.output
        # The optional name-reservation paths are still mentioned.
        assert "toolbase create" in result.output
        assert "toolbase-ai.com" in result.output
        # The three plain steps.
        assert "toolbase validate" in result.output
        assert "toolbase login" in result.output
        assert "toolbase publish" in result.output
        # No longer a commanded "Register the toolkit" step.
        assert "Register the toolkit" not in result.output

    def test_publish_appears_after_validate_and_login(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0
        # In the Next-steps banner: validate -> login -> publish.
        # Anchor on the "Next steps:" header to skip earlier mentions in
        # the requirements warning.
        out = result.output
        nxt = out.find("Next steps:")
        assert nxt >= 0, out
        tail = out[nxt:]
        i_validate = tail.find("toolbase validate")
        i_login = tail.find("toolbase login")
        i_publish = tail.find("toolbase publish")
        assert i_validate >= 0
        assert i_login > i_validate
        assert i_publish > i_login


class TestDroppedFileWarning:
    """Regression test for issue #1: a .py file with tool-shaped
    definitions whose dotted module path can't be resolved (typically
    because of a missing intermediate __init__.py) must be reported as
    a stderr warning, not silently dropped.
    """

    def _make_repo_with_missing_init(self, root):
        # pkg/__init__.py exists; pkg/subdir/__init__.py is MISSING.
        pkg = root / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        sub = pkg / "subdir"
        sub.mkdir()
        # No __init__.py here on purpose.
        (sub / "mod.py").write_text(
            "from orchestral.tools import BaseTool\n"
            "class DroppedTool(BaseTool):\n"
            "    pass\n",
            encoding="utf-8",
        )

    def test_warns_when_file_has_tool_pattern_but_no_module_path(
        self, tmp_path
    ):
        self._make_repo_with_missing_init(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0, result.output
        # The warning text and filename must appear; the missing-init
        # hint should call out the right directory.
        assert "WARNING" in result.output
        assert "mod.py" in result.output
        assert "pkg/subdir" in result.output or "pkg\\subdir" in result.output

    def test_no_warning_for_plain_files_without_tool_patterns(self, tmp_path):
        # Same shape but the file under the missing-init dir has no
        # tools — silent skip is correct here.
        pkg = tmp_path / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "tool.py").write_text(
            "from orchestral import define_tool\n"
            "@define_tool\n"
            "def my_tool():\n"
            "    pass\n",
            encoding="utf-8",
        )
        sub = pkg / "subdir"
        sub.mkdir()
        # No __init__.py, no tools either — should not warn.
        (sub / "plain.py").write_text(
            "def just_a_helper():\n"
            "    return 42\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0, result.output
        # No dropped-file warning. (The requirements.txt warning is
        # unrelated — match on the dropped-file phrasing specifically.)
        assert "could not be resolved" not in result.output
        # And the legitimate tool was still discovered.
        assert "my_tool" in result.output

    def test_no_warning_for_well_formed_repo(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0
        assert "could not be resolved" not in result.output
