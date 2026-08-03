"""Skills are visible in the read commands, not only the write ones.

Every command that *changed* skills knew about them --- ``tb activate
<toolkit>__<skill>``, ``tb deactivate``, ``tb install --no-skills`` ---
and no command that *showed* anything did. ``tb list``, ``tb list -v``,
``tb list --json`` and ``tb status`` were all silent, so a toolkit's
skills were undiscoverable short of listing its ``skills/`` directory,
and ``tb deactivate <toolkit>__<skill>`` was effectively write-only:
skills default on, nothing displayed their state, so turning one off
left no trace anywhere.

A skill's status is the same three-way question a tool's is --- on, off,
or gated by a bundle whose config requirements are unmet --- so it is
computed once in ``_toolkit_skill_status`` and rendered by both
surfaces, which is what keeps them from drifting apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config
from toolbase.envs import cache_dir, write_install_meta, write_legacy_meta


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    workdir = tmp_path / "_cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return fake


def _toolkit(name="demo-kit", version="1.0.0", *, skills=None, bundles=None,
             tools=("do_thing",)):
    """A cache slot with a real toolkit.yaml and skills/ on disk."""
    slot = cache_dir(name, version)
    (slot / "skills").mkdir(parents=True, exist_ok=True)
    doc = [
        f"name: {name}", f"version: {version}", "description: x",
        "author: x", "license: MIT", "category: general",
        'python_version: "3.12"',
    ]
    if tools:
        doc.append("tools:")
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

    for spec in (skills or []):
        if isinstance(spec, tuple):
            slug, bundle = spec
            body = f"---\nname: {slug}\nbundle: {bundle}\n---\n\nGuide.\n"
        else:
            slug, body = spec, f"---\nname: {spec}\n---\n\nGuide.\n"
        (slot / "skills" / f"{slug}.md").write_text(body)

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
    return CliRunner().invoke(cli.main, list(args))


# ── the shared rule ─────────────────────────────────────────────────────


class TestSkillStatus:
    def test_a_plain_skill_is_on(self, env):
        slot = _toolkit(skills=["searching"])
        assert cli._toolkit_skill_status("demo-kit", slot) == [
            ("searching", "on", None)]

    def test_a_toolkit_with_no_skills_reports_none(self, env):
        slot = _toolkit(skills=[])
        assert cli._toolkit_skill_status("demo-kit", slot) == []

    def test_a_deactivated_skill_is_off(self, env):
        slot = _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        _run("deactivate", "demo-kit__searching")
        assert cli._toolkit_skill_status("demo-kit", slot) == [
            ("searching", "off", None)]

    def test_a_skill_scoped_to_an_unconfigured_bundle_is_gated(self, env):
        """Its bundle's tools aren't served either, so surfacing the
        guide would promise something the agent cannot do."""
        slot = _toolkit(skills=[("heavy_guide", "heavy")],
                        bundles={"heavy": ["heavy_path"]})
        assert cli._toolkit_skill_status("demo-kit", slot) == [
            ("heavy_guide", "gated", "heavy")]

    def test_deactivation_wins_over_gating(self, env):
        """Same order surface_skills applies them in."""
        slot = _toolkit(skills=[("heavy_guide", "heavy")],
                        bundles={"heavy": ["heavy_path"]})
        _run("activate", "demo-kit")
        _run("deactivate", "demo-kit__heavy_guide")
        assert cli._toolkit_skill_status("demo-kit", slot)[0][1] == "off"

    def test_an_unreadable_guide_does_not_hide_the_skill(self, env):
        slot = _toolkit(skills=["searching"])
        (slot / "skills" / "searching.md").write_bytes(b"\xff\xfe\x00bad")
        rows = cli._toolkit_skill_status("demo-kit", slot)
        assert [r[0] for r in rows] == ["searching"]


# ── tb list ─────────────────────────────────────────────────────────────


class TestListVerbose:
    def test_skills_appear_under_their_own_header(self, env):
        _toolkit(skills=["searching"])
        r = _run("list", "-v")
        assert r.exit_code == 0, r.output
        assert "[skills]" in r.output
        assert "searching" in r.output

    def test_an_inactive_toolkit_marks_its_skills_hidden(self, env):
        """It surfaces nothing, so a tick would say the opposite of what
        is true --- the same gate a tool's mark gets."""
        _toolkit(skills=["searching"])
        out = _run("list", "-v").output
        block = out[out.index("[skills]"):]
        assert "✗ searching" in block

    def test_activating_the_toolkit_ticks_its_skills(self, env):
        _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        out = _run("list", "-v").output
        block = out[out.index("[skills]"):]
        assert "✓ searching" in block

    def test_a_deactivated_skill_says_how_to_get_it_back(self, env):
        _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        _run("deactivate", "demo-kit__searching")
        out = " ".join(_run("list", "-v").output.split())
        assert "deactivated" in out
        assert "tb activate demo-kit__searching" in out

    def test_a_gated_skill_names_the_bundle(self, env):
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path"]})
        out = " ".join(_run("list", "-v").output.split())
        assert "needs the heavy bundle" in out

    def test_no_skills_header_when_a_toolkit_ships_none(self, env):
        _toolkit(skills=[])
        assert "[skills]" not in _run("list", "-v").output

    def test_plain_list_stays_terse(self, env):
        """`-v` is the "what does this offer" view; plain list is an
        inventory and shouldn't grow a line per skill."""
        _toolkit(skills=["searching"])
        assert "[skills]" not in _run("list").output


class TestListJson:
    def test_skills_are_in_the_payload(self, env):
        _toolkit(skills=["searching"])
        rec = json.loads(_run("list", "--json").output)[0]
        assert rec["skills"] == [
            {"slug": "searching", "state": "on", "bundle": None}]

    def test_state_and_bundle_are_reported(self, env):
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path"]})
        rec = json.loads(_run("list", "--json").output)[0]
        assert rec["skills"] == [
            {"slug": "heavy_guide", "state": "gated", "bundle": "heavy"}]

    def test_a_toolkit_with_no_skills_gets_an_empty_list(self, env):
        _toolkit(skills=[])
        assert json.loads(_run("list", "--json").output)[0]["skills"] == []


# ── tb status ───────────────────────────────────────────────────────────


class TestStatus:
    def test_active_toolkit_skills_are_listed_qualified(self, env):
        _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        out = _run("status").output
        assert "Skills" in out
        assert "demo-kit__searching" in " ".join(out.split())

    def test_inactive_toolkits_contribute_nothing(self, env):
        """An inactive toolkit surfaces nothing; listing its skills under
        a heading that says "surfaced to harnesses" would be a lie."""
        _toolkit(skills=["searching"])
        assert "Skills" not in _run("status").output

    def test_a_deactivated_skill_is_shown_as_off(self, env):
        """The whole point: `tb deactivate <toolkit>__<skill>` used to
        leave no visible trace anywhere."""
        _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        _run("deactivate", "demo-kit__searching")
        out = " ".join(_run("status").output.split())
        assert "demo-kit__searching" in out
        assert "off" in out

    def test_unwired_note_appears_when_something_would_surface(self, env):
        _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        # Distinct from the Issues line of similar wording: this one is
        # about skills specifically.
        assert "in front of an agent" in " ".join(_run("status").output.split())

    def test_no_unwired_note_when_every_skill_is_off(self, env):
        """With nothing to surface, an unwired harness is not what stands
        between the agent and these."""
        _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        _run("deactivate", "demo-kit__searching")
        assert "in front of an agent" not in " ".join(_run("status").output.split())

    def test_no_section_at_all_without_skills(self, env):
        _toolkit(skills=[])
        _run("activate", "demo-kit")
        assert "Skills" not in _run("status").output

    def test_state_is_the_skills_own_setting_not_the_net_outcome(self, env):
        """An inactive toolkit surfaces nothing, but its skill's own
        setting is still "on". Collapsing the two would lose the
        difference between a skill you turned off and a toolkit you
        never activated; a consumer wanting the net answer reads
        ``state == "on" and active``."""
        _toolkit(skills=["searching"])
        rec = json.loads(_run("list", "--json").output)[0]
        assert rec["active"] is False
        assert rec["skills"][0]["state"] == "on"


# ── activating a gated skill ────────────────────────────────────────────


class TestActivateGatedSkill:
    """Activating clears a `tb deactivate`; it cannot clear a bundle gate.

    The two are separate filters, so a skill scoped to an unconfigured
    bundle stays unsurfaced no matter how often it is activated. Without
    a word about it the command reports success -- or "already active" --
    on something that will not reach an agent, which is the one case
    where the message and the outcome disagree.
    """

    def test_says_the_skill_will_not_surface(self, env):
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path"]})
        _run("activate", "demo-kit")
        out = " ".join(_run("activate", "demo-kit__heavy_guide").output.split())
        assert "Not surfaced" in out
        assert "heavy bundle" in out

    def test_names_the_config_keys_it_is_waiting_on(self, env):
        """Naming them is what turns a silent no-op into something the
        user can act on."""
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path", "other_key"]})
        _run("activate", "demo-kit")
        out = " ".join(_run("activate", "demo-kit__heavy_guide").output.split())
        assert "heavy_path" in out
        assert "other_key" in out
        assert "tb config set demo-kit" in out

    def test_warns_even_when_it_was_already_active(self, env):
        """Skills default on, so the usual path prints "already active" --
        the exact message that read as success in the reported case."""
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path"]})
        _run("activate", "demo-kit")
        r = _run("activate", "demo-kit__heavy_guide")
        flat = " ".join(r.output.split())
        assert "already active" in flat
        assert "Not surfaced" in flat

    def test_warns_after_a_real_reactivation(self, env):
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path"]})
        _run("activate", "demo-kit")
        _run("deactivate", "demo-kit__heavy_guide")
        out = " ".join(_run("activate", "demo-kit__heavy_guide").output.split())
        assert "Activated skill" in out
        assert "Not surfaced" in out

    def test_an_ungated_skill_gets_no_warning(self, env):
        _toolkit(skills=["searching"])
        _run("activate", "demo-kit")
        assert "Not surfaced" not in _run(
            "activate", "demo-kit__searching").output

    def test_configuring_the_bundle_silences_it(self, env):
        """The warning tracks the gate, not the skill."""
        _toolkit(skills=[("heavy_guide", "heavy")],
                 bundles={"heavy": ["heavy_path"]})
        _run("activate", "demo-kit")
        _run("config", "set", "-u", "demo-kit", "heavy_path", "/opt/heavy")
        assert "Not surfaced" not in _run(
            "activate", "demo-kit__heavy_guide").output

    def test_activating_a_tool_is_unaffected(self, env):
        _toolkit(skills=["searching"], tools=("do_thing",))
        _run("activate", "demo-kit")
        assert "Not surfaced" not in _run(
            "activate", "demo-kit__do_thing").output
