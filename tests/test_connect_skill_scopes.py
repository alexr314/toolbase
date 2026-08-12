"""Skill surfacing is scoped, and a connect is a sync.

Two things were true of skill surfacing and shouldn't have been.

**It ignored scope.** ``skill_target()`` took none, so every adapter wrote
to a user-global directory while the MCP entry beside it went wherever
``-u``/``-p`` said. ``tb connect codex -p`` put the server in this repo's
``.codex/config.toml`` and the guides for its tools in front of every
project's agent. All four harnesses read a project skill directory as well
as a global one, which is the split ``config_path`` already models, so
``skill_target`` now takes the same ``(scope, project_root)``.

**It only ever added.** Everything that stops a skill from being surfaced
-- its toolkit dropping out of the loadout, ``tb deactivate
<toolkit>__<skill>``, a bundle gate closing, a new version deleting the
guide -- left the last copy on disk, still read by the harness and
contradicting every read command. A connect now prunes the toolbase-owned
entries it didn't just write, so the surface converges on the current
answer instead of accumulating every answer it has ever given.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config
from toolbase.envs import cache_dir, write_install_meta, write_legacy_meta


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated toolbase home, codex home, XDG root, and project cwd.

    Both harnesses used here resolve their roots from the environment
    (``$CODEX_HOME``, ``$XDG_CONFIG_HOME``), which keeps the real ones
    untouched without monkeypatching ``Path.home``.
    """
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    class Env:
        root = tmp_path
        codex_user = tmp_path / "codex-home" / "skills"
        codex_project = project / ".codex" / "skills"
        opencode_user = tmp_path / "xdg" / "opencode" / "skills"
        opencode_project = project / ".opencode" / "skills"
        proj = project

    return Env()


def _toolkit(name="demo-kit", version="1.0.0", *, skills=(), bundles=None,
             tools=("do_thing",)):
    """A cache slot that ``discover_toolkits`` will accept, with skills/."""
    slot = cache_dir(name, version)
    (slot / "skills").mkdir(parents=True, exist_ok=True)
    doc = [
        f"name: {name}", f"version: {version}", "description: x",
        "author: x", "license: MIT", "category: general",
        'python_version: "3.12"', "tools:",
    ]
    for t in tools:
        doc += [f"  - name: {t}", f"    function: tools.{t}",
                "    description: x"]
    if bundles:
        doc.append("bundles:")
        for b, reqs in bundles.items():
            doc.append(f"  {b}:")
            doc.append("    requires:")
            for r in reqs:
                doc.append(f"      - {r}")
    (slot / "toolkit.yaml").write_text("\n".join(doc) + "\n")

    for spec in skills:
        slug, bundle = spec if isinstance(spec, tuple) else (spec, None)
        fm = f"---\nname: {slug}\ndescription: d.\n"
        fm += f"bundle: {bundle}\n" if bundle else ""
        (slot / "skills" / f"{slug}.md").write_text(fm + "---\n\nGuide.\n")

    py = str(slot / ".venv" / "bin" / "python")
    Path(py).parent.mkdir(parents=True, exist_ok=True)
    Path(py).write_text("#!/bin/sh\n")
    Path(py).chmod(0o755)
    write_install_meta(slot, name=name, version=version, install_method="venv",
                       python_version="3.12", extras={"python_path": py})
    write_legacy_meta(slot, {"name": name, "version": version,
                             "environment": "venv", "python_path": py,
                             "python_version": "3.12"})
    return slot


def _run(*args):
    """Invoke the CLI and insist it succeeded.

    Asserting the exit code here rather than at each call site is what
    stops a mistyped flag from turning a real assertion into a vacuous
    one — a usage error exits 2 and changes nothing, which reads exactly
    like the behaviour under test being broken.
    """
    r = CliRunner().invoke(cli.main, list(args), catch_exceptions=False)
    assert r.exit_code == 0, f"`tb {' '.join(args)}` exited {r.exit_code}:\n{r.output}"
    return r


def _names(root: Path):
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


# ── part 1: the surface follows the scope you wired ─────────────────────


class TestScope:
    def test_project_connect_surfaces_into_the_project(self, env):
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-p")

        assert _names(env.codex_project) == ["demo-kit__guide"]
        assert _names(env.codex_user) == []

    def test_user_connect_surfaces_into_the_user_home(self, env):
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-u")

        assert _names(env.codex_user) == ["demo-kit__guide"]
        assert _names(env.codex_project) == []

    def test_the_skill_surface_sits_beside_the_config_it_was_wired_with(self, env):
        """The point of the change: server entry and guides land in the
        same scope, so a tool and its guide reach the same agents."""
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-p")

        assert (env.proj / ".codex" / "config.toml").exists()
        assert (env.proj / ".codex" / "skills" / "demo-kit__guide").exists()

    def test_a_second_harness_scopes_the_same_way(self, env):
        """The scope map is the adapter contract, not a Codex special case."""
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "opencode", "-p")

        assert _names(env.opencode_project) == ["demo-kit__guide"]
        assert _names(env.opencode_user) == []

    def test_disconnecting_one_scope_leaves_the_other(self, env):
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-u")
        _run("connect", "codex", "-p")
        assert _names(env.codex_user) == ["demo-kit__guide"]

        _run("disconnect", "codex", "-p")
        assert _names(env.codex_project) == []
        assert _names(env.codex_user) == ["demo-kit__guide"]

    def test_disconnect_all_clears_both(self, env):
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-u")
        _run("connect", "codex", "-p")

        _run("disconnect", "codex", "--all")
        assert _names(env.codex_user) == []
        assert _names(env.codex_project) == []

    def test_a_project_connect_names_the_other_scope_holding_skills(self, env):
        """Both scopes are read at once, so a leftover user-scope copy is
        the same guide twice — and a `tb deactivate` here won't reach it."""
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-u")

        out = " ".join(_run("connect", "codex", "-p").output.split())
        assert "also surfaced at user scope" in out
        assert "tb disconnect codex -u" in out

    def test_no_note_when_the_other_scope_is_clean(self, env):
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        assert "also surfaced at" not in _run("connect", "codex", "-p").output

    def test_no_skills_flag_still_writes_the_server_entry(self, env):
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-p", "--no-skills")

        assert (env.proj / ".codex" / "config.toml").exists()
        assert _names(env.codex_project) == []


# ── part 2: a connect is a sync, not an append ──────────────────────────


class TestPrune:
    def _connected(self, env, **kw):
        _toolkit(skills=kw.pop("skills", ["guide"]), **kw)
        _run("activate", "demo-kit")
        _run("connect", "codex", "-p")
        return env.codex_project

    def test_deactivating_the_toolkit_removes_its_skills_on_reconnect(self, env):
        out = self._connected(env)
        assert _names(out) == ["demo-kit__guide"]

        _run("deactivate", "demo-kit")
        _run("connect", "codex", "-p")
        assert _names(out) == []

    def test_deactivating_one_skill_removes_just_that_one(self, env):
        out = self._connected(env, skills=["keep", "drop"])
        assert _names(out) == ["demo-kit__drop", "demo-kit__keep"]

        _run("deactivate", "demo-kit__drop")
        _run("connect", "codex", "-p")
        assert _names(out) == ["demo-kit__keep"]

    def test_a_closing_bundle_gate_removes_its_skill(self, env):
        """The gate exists so a guide never promises tools that aren't
        served. It has to apply on the way out, not only on the way in."""
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path"]})
        _run("activate", "demo-kit")
        _run("config", "set", "-u", "demo-kit", "heavy_path", "/opt/heavy")
        _run("connect", "codex", "-p")
        assert _names(env.codex_project) == ["demo-kit__heavy-guide"]

        _run("config", "unset", "-u", "demo-kit", "heavy_path")
        _run("connect", "codex", "-p")
        assert _names(env.codex_project) == []

    def test_a_guide_dropped_by_a_new_version_is_reaped(self, env):
        out = self._connected(env, skills=["guide", "legacy"])
        assert "demo-kit__legacy" in _names(out)

        (cache_dir("demo-kit", "1.0.0") / "skills" / "legacy.md").unlink()
        _run("connect", "codex", "-p")
        assert _names(out) == ["demo-kit__guide"]

    def test_the_prune_says_what_it_removed(self, env):
        """Silently deleting what the user can see in `tb list` is worse
        than leaving it."""
        self._connected(env, skills=["keep", "drop"])
        _run("deactivate", "demo-kit__drop")

        out = " ".join(_run("connect", "codex", "-p").output.split())
        assert "no longer active, removed: demo-kit__drop" in out

    def test_it_never_touches_a_skill_toolbase_did_not_write(self, env):
        out = self._connected(env)
        (out / "hand-written").mkdir()
        (out / "hand-written" / "SKILL.md").write_text("mine\n")

        _run("deactivate", "demo-kit")
        _run("connect", "codex", "-p")
        assert _names(out) == ["hand-written"]

    def test_no_skills_prunes_nothing(self, env):
        """`--no-skills` means don't touch the skill surface — in either
        direction."""
        out = self._connected(env)
        _run("deactivate", "demo-kit")
        _run("connect", "codex", "-p", "--no-skills")
        assert _names(out) == ["demo-kit__guide"]

    def test_pruning_is_scoped_to_the_surface_being_written(self, env):
        """A project connect must not reap the user scope's skills — that
        scope has its own connect, and its own answer."""
        _toolkit(skills=["guide"])
        _run("activate", "demo-kit")
        _run("connect", "codex", "-u")

        _run("deactivate", "demo-kit")
        _run("connect", "codex", "-p")
        assert _names(env.codex_user) == ["demo-kit__guide"]

    def test_a_second_harness_prunes_too(self, env):
        _toolkit(skills=["keep", "drop"])
        _run("activate", "demo-kit")
        _run("connect", "opencode", "-p")

        _run("deactivate", "demo-kit__drop")
        _run("connect", "opencode", "-p")
        assert _names(env.opencode_project) == ["demo-kit__keep"]


# ── the failure path: one broken toolkit can't cost another its skills ──


class TestSurfacingFailureIsContained:
    def test_a_toolkit_that_failed_to_surface_is_not_pruned(self, env, monkeypatch):
        _toolkit("kit-a", skills=["guide"])
        _toolkit("kit-b", skills=["guide"])
        _run("activate", "kit-a")
        _run("activate", "kit-b")
        _run("connect", "codex", "-p")
        assert _names(env.codex_project) == ["kit-a__guide", "kit-b__guide"]

        from toolbase import skills as skills_mod
        original = skills_mod.surface_skills

        def flaky(name, slot, target, **kw):
            if name == "kit-b":
                raise RuntimeError("boom")
            return original(name, slot, target, **kw)

        monkeypatch.setattr(skills_mod, "surface_skills", flaky)
        out = _run("connect", "codex", "-p").output

        assert "Could not surface kit-b" in out
        assert _names(env.codex_project) == ["kit-a__guide", "kit-b__guide"]
