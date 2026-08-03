"""Tests for the orchestral integration (``tb connect orchestral`` backend).

Orchestral is a Python library, not a config-file MCP client, so the
integration is (a) the ``toolbase_tools()`` context manager that reuses the
serve ``Orchestrator`` in-process and (b) a generated runnable agent script.

These tests are network- and subprocess-free: the ``Orchestrator`` is
replaced with a fake so ``toolbase_tools`` exercises only the lifecycle
contract (resolve loadout -> start -> yield -> shutdown).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase.connect import orchestral as oc


# ── agent_script (pure generator) ─────────────────────────────────────


def test_agent_script_parses_as_python():
    ast.parse(oc.agent_script("paper"))
    ast.parse(oc.agent_script(None))


def test_agent_script_carries_marker():
    assert oc.agent_script(None).startswith(oc.GENERATED_MARKER)


def test_agent_script_bakes_loadout_when_given():
    assert 'loadout="paper"' in oc.agent_script("paper")


def test_agent_script_no_loadout_arg_when_none():
    assert "loadout=" not in oc.agent_script(None)


# ── the scaffold must not shadow the package it imports ───────────────
#
# `.toolbase/` heads sys.path when the script is run directly, so a module
# in there named `orchestral.py` makes `from orchestral import Agent` import
# itself -- "cannot import name 'Agent' from partially initialized module".
# That was the default name in 0.8.1 and earlier and it broke every launch.


def test_default_script_name_does_not_shadow_orchestral():
    assert oc.DEFAULT_SCRIPT_NAME != "orchestral.py"
    assert oc.DEFAULT_SCRIPT_RELPATH.endswith(oc.DEFAULT_SCRIPT_NAME)


def test_agent_script_demotes_its_own_directory_on_sys_path():
    body = oc.agent_script(None)
    # The guard must run before the first `from orchestral ...` import.
    assert body.index("sys.path.pop(0)") < body.index("from orchestral import")


def test_generated_script_imports_real_orchestral_despite_shadow(tmp_path):
    """End-to-end: a hostile `orchestral.py` next to the scaffold must lose."""
    import subprocess
    import sys as _sys

    d = tmp_path / ".toolbase"
    d.mkdir()
    (d / "orchestral.py").write_text("raise RuntimeError('shadowed')\n")
    # Keep only the prologue + imports; drop main() so nothing spawns.
    body = oc.agent_script(None).split("def main():")[0]
    script = d / oc.DEFAULT_SCRIPT_NAME
    script.write_text(body + "print('IMPORTS_OK', Agent.__name__)\n")

    p = subprocess.run(
        [_sys.executable, str(script)], capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "IMPORTS_OK" in p.stdout


# ── sandbox scoping ─────────────────────────────────────────────────
#
# Served tools resolve relative paths against the `base_directory` in
# ~/.toolbase/config/<toolkit>.yaml, which has nothing to do with where the
# agent is working. Unless the scaffold overrides it *and* points its own
# file tools at the same root, the agent cannot read back what its tools
# write.


def test_agent_script_scopes_served_tools_to_the_sandbox():
    body = oc.agent_script(None)
    assert 'config_overrides={"base_directory": str(SANDBOX)}' in body


def test_agent_script_gives_file_tools_the_same_sandbox():
    body = oc.agent_script(None)
    for tool in ("ReadFileTool", "WriteFileTool", "EditFileTool",
                 "FindFilesTool", "FileSearchTool", "RunCommandTool",
                 "RunPythonTool"):
        assert f"{tool}(base_directory=str(SANDBOX)" in body, tool


def test_agent_script_file_tools_are_importable():
    # The scaffold's import line has to match orchestral's actual exports.
    from orchestral.tools import (  # noqa: F401
        EditFileTool, FileSearchTool, FindFilesTool, ReadFileTool,
        RunCommandTool, RunPythonTool, WriteFileTool,
    )


def test_agent_script_tui_active_others_commented():
    body = oc.agent_script(None)
    # The terminal-UI launch line is live (not commented).
    assert "\n        run_interactive_session(agent, streaming=True)" in body
    # The headless and GUI launch lines are commented out.
    assert "# print(agent.run(" in body
    assert "# run_server(agent" in body


def test_agent_script_run_hint_points_at_tb_orchestral():
    assert "tb orchestral" in oc.agent_script(None)


def test_all_three_modalities_present():
    body = oc.agent_script(None)
    assert "run_interactive_session" in body  # TUI
    assert "agent.run(" in body               # headless
    assert "run_server(" in body              # GUI


# ── is_orchestral_available ───────────────────────────────────────────


def test_is_orchestral_available_true():
    # orchestral is a hard dependency of toolbase, so it imports here.
    assert oc.is_orchestral_available() is True


# ── toolbase_tools lifecycle (Orchestrator faked) ─────────────────────


class _FakeOrch:
    """Stand-in for serve.Orchestrator that records lifecycle calls."""

    instances: list = []

    def __init__(self, *, console=None, loadout=None, call_timeout_s=None):
        self.console = console
        self.loadout = loadout
        self.call_timeout_s = call_timeout_s
        self.started = False
        self.shut = False
        _FakeOrch.instances.append(self)

    def start(self):
        self.started = True
        return ["TOOL_A", "TOOL_B"]

    def shutdown(self):
        self.shut = True


@pytest.fixture
def fake_orch(monkeypatch):
    _FakeOrch.instances = []
    monkeypatch.setattr(oc, "Orchestrator", _FakeOrch)

    captured = {}

    def fake_resolve(root=None, *, cli_loadout=None, **kw):
        captured["root"] = root
        captured["cli_loadout"] = cli_loadout
        return "RESOLVED_LOADOUT"

    monkeypatch.setattr("toolbase.serve.loadouts.resolve_loadout", fake_resolve)
    return captured


def test_toolbase_tools_yields_started_tools_and_shuts_down(fake_orch, tmp_path):
    with oc.toolbase_tools(loadout="paper", project_root=tmp_path,
                           call_timeout_s=12.0) as tools:
        assert tools == ["TOOL_A", "TOOL_B"]
        inst = _FakeOrch.instances[-1]
        assert inst.started is True
        assert inst.shut is False  # not yet
    # On exit: torn down, and the resolved loadout + timeout were threaded in.
    assert inst.shut is True
    assert inst.loadout == "RESOLVED_LOADOUT"
    assert inst.call_timeout_s == 12.0
    assert fake_orch == {"root": tmp_path, "cli_loadout": "paper"}


def test_toolbase_tools_shuts_down_on_exception(fake_orch, tmp_path):
    with pytest.raises(RuntimeError):
        with oc.toolbase_tools(project_root=tmp_path):
            inst = _FakeOrch.instances[-1]
            raise RuntimeError("boom")
    assert inst.shut is True  # cleanup ran despite the error


def test_toolbase_tools_quiet_suppresses_console(fake_orch, tmp_path):
    with oc.toolbase_tools(project_root=tmp_path, quiet=True):
        pass
    assert _FakeOrch.instances[-1].console is not None  # a null console
    with oc.toolbase_tools(project_root=tmp_path, quiet=False):
        pass
    assert _FakeOrch.instances[-1].console is None  # Orchestrator's own default


def test_toolbase_tools_discovers_project_root_when_omitted(fake_orch, monkeypatch):
    monkeypatch.setattr("toolbase.envs.find_project_root",
                        lambda *a, **k: Path("/discovered/root"))
    with oc.toolbase_tools():
        pass
    assert fake_orch["root"] == Path("/discovered/root")


def test_toolbase_tools_accepts_str_project_root(fake_orch):
    # Regression: the documented public API took `project_root: PATH` but a
    # str crashed downstream path handling (`'str' has no attribute
    # 'resolve'`). A str must be coerced to Path.
    with oc.toolbase_tools(project_root="/some/project"):
        pass
    assert fake_orch["root"] == Path("/some/project")
    assert isinstance(fake_orch["root"], Path)


# ── CLI: tb connect orchestral ────────────────────────────────────────


def _run(args):
    return CliRunner().invoke(cli.main, args)


def _default_script(root):
    """Where `tb connect orchestral` writes when run with cwd=root and no
    --out: <root>/.toolbase/orchestral.py (no project => cwd is the root)."""
    return root / ".toolbase" / oc.DEFAULT_SCRIPT_NAME


def test_cli_writes_scaffold_under_dot_toolbase(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = _run(["connect", "orchestral"])
    assert res.exit_code == 0, res.output
    script = _default_script(tmp_path)
    assert script.exists()
    assert script.read_text().startswith(oc.GENERATED_MARKER)


def test_cli_out_overrides_path(tmp_path):
    out = tmp_path / "custom.py"
    res = _run(["connect", "orchestral", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert out.exists()
    assert not (tmp_path / ".toolbase").exists()  # didn't use the default


def test_cli_bakes_loadout(tmp_path):
    out = tmp_path / "agent.py"
    _run(["connect", "orchestral", "--loadout", "paper", "--out", str(out)])
    assert 'loadout="paper"' in out.read_text()


def test_cli_refuses_overwrite_without_force(tmp_path):
    out = tmp_path / "agent.py"
    out.write_text("MINE\n")
    res = _run(["connect", "orchestral", "--out", str(out)])
    assert res.exit_code == 1
    assert out.read_text() == "MINE\n"  # untouched


def test_cli_force_overwrites(tmp_path):
    out = tmp_path / "agent.py"
    out.write_text("MINE\n")
    res = _run(["connect", "orchestral", "--out", str(out), "--force"])
    assert res.exit_code == 0, res.output
    assert out.read_text().startswith(oc.GENERATED_MARKER)


def test_cli_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "agent.py"
    res = _run(["connect", "orchestral", "--out", str(out), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert not out.exists()
    assert oc.GENERATED_MARKER in res.output


def test_cli_remove_deletes_generated_file(tmp_path):
    out = tmp_path / "agent.py"
    _run(["connect", "orchestral", "--out", str(out)])
    res = _run(["connect", "orchestral", "--out", str(out), "--remove"])
    assert res.exit_code == 0, res.output
    assert not out.exists()


def test_cli_remove_refuses_unmarked_file(tmp_path):
    out = tmp_path / "agent.py"
    out.write_text("print('mine')\n")  # no generated marker
    res = _run(["connect", "orchestral", "--out", str(out), "--remove"])
    assert res.exit_code == 1
    assert out.exists()  # not deleted


def test_cli_remove_absent_is_friendly_noop(tmp_path):
    out = tmp_path / "agent.py"
    res = _run(["connect", "orchestral", "--out", str(out), "--remove"])
    assert res.exit_code == 0, res.output
    # Flattened: the message embeds a tmp path, so the wrap point moves
    # with the path length. conftest pins the width, and this keeps the
    # assertion honest if that message ever grows past it.
    assert "nothing to remove" in " ".join(res.output.split()).lower()


def test_cli_harnesses_lists_orchestral():
    res = _run(["connect", "--harnesses"])
    assert res.exit_code == 0, res.output
    assert "orchestral" in res.output


def test_cli_disconnect_orchestral_removes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _run(["connect", "orchestral"])
    script = _default_script(tmp_path)
    assert script.exists()
    res = _run(["disconnect", "orchestral"])
    assert res.exit_code == 0, res.output
    assert not script.exists()


# ── CLI: tb orchestral (runner) ───────────────────────────────────────


def test_orchestral_runner_errors_without_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = _run(["orchestral"])
    assert res.exit_code == 1
    # Collapse whitespace: Rich wraps the hint across lines at 80 cols.
    assert "tb connect orchestral" in " ".join(res.output.split())


def test_orchestral_runner_invokes_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _run(["connect", "orchestral"])  # writes .toolbase/agent.py
    script = _default_script(tmp_path)

    captured = {}

    class _Done:
        returncode = 0

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["cwd"] = kw.get("cwd")
        return _Done()

    monkeypatch.setattr("subprocess.run", fake_run)
    res = _run(["orchestral"])
    assert res.exit_code == 0, res.output
    # Ran the generated script with the toolbase interpreter, cwd = project root.
    assert captured["argv"][1] == str(script)
    assert captured["cwd"] == str(tmp_path)


# ── migrating off the 0.8.1-and-earlier `.toolbase/orchestral.py` ─────────────


def _legacy_script(root):
    return root / ".toolbase" / oc.LEGACY_SCRIPT_NAME


def test_connect_retires_generated_legacy_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy = _legacy_script(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(oc.GENERATED_MARKER + " old\n")

    res = _run(["connect", "orchestral"])
    assert res.exit_code == 0, res.output
    # The shadowing file is gone; the new one is in its place.
    assert not legacy.exists()
    assert _default_script(tmp_path).exists()


def test_connect_keeps_but_warns_about_hand_written_legacy_script(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    legacy = _legacy_script(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# mine, not toolbase's\n")

    res = _run(["connect", "orchestral"])
    assert res.exit_code == 0, res.output
    assert legacy.read_text() == "# mine, not toolbase's\n"  # untouched
    assert "shadows" in " ".join(res.output.split())


def test_orchestral_runner_refuses_legacy_script_with_migration_hint(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    legacy = _legacy_script(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(oc.GENERATED_MARKER + " old\n")

    # Would otherwise be launched -- fail loudly if it ever is.
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: pytest.fail("must not launch a shadowing script"),
    )
    res = _run(["orchestral"])
    assert res.exit_code == 1
    flat = " ".join(res.output.split())
    assert "shadows" in flat
    assert "tb connect orchestral --force" in flat
