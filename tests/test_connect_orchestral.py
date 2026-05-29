"""Tests for the orchestral integration (``tb connect orchestral`` backend).

Orchestral is a Python library, not a config-file MCP client, so the
integration is (a) the ``toolbase_tools()`` context manager that reuses the
serve ``Orchestrator`` in-process and (b) a generated runnable agent script.

These tests are network- and subprocess-free: the ``Orchestrator`` is
replaced with a fake so ``toolbase_tools`` exercises only the lifecycle
contract (resolve profile -> start -> yield -> shutdown).
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


def test_agent_script_bakes_profile_when_given():
    assert 'toolbase_tools(profile="paper")' in oc.agent_script("paper")


def test_agent_script_no_profile_arg_when_none():
    assert "toolbase_tools()" in oc.agent_script(None)
    assert "profile=" not in oc.agent_script(None)


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

    def __init__(self, *, console=None, profile=None, call_timeout_s=None):
        self.console = console
        self.profile = profile
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

    def fake_resolve(root=None, *, cli_profile=None, **kw):
        captured["root"] = root
        captured["cli_profile"] = cli_profile
        return "RESOLVED_PROFILE"

    monkeypatch.setattr("toolbase.serve.profiles.resolve_profile", fake_resolve)
    return captured


def test_toolbase_tools_yields_started_tools_and_shuts_down(fake_orch, tmp_path):
    with oc.toolbase_tools(profile="paper", project_root=tmp_path,
                           call_timeout_s=12.0) as tools:
        assert tools == ["TOOL_A", "TOOL_B"]
        inst = _FakeOrch.instances[-1]
        assert inst.started is True
        assert inst.shut is False  # not yet
    # On exit: torn down, and the resolved profile + timeout were threaded in.
    assert inst.shut is True
    assert inst.profile == "RESOLVED_PROFILE"
    assert inst.call_timeout_s == 12.0
    assert fake_orch == {"root": tmp_path, "cli_profile": "paper"}


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


def test_cli_bakes_profile(tmp_path):
    out = tmp_path / "agent.py"
    _run(["connect", "orchestral", "--profile", "paper", "--out", str(out)])
    assert 'toolbase_tools(profile="paper")' in out.read_text()


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
    assert "nothing to remove" in res.output.lower()


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
    _run(["connect", "orchestral"])  # writes .toolbase/orchestral.py
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
