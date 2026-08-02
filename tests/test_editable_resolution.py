"""Editable slots are opt-in, and every surface says so consistently.

An editable slot is a symlink to a source checkout, and the cache that
holds it is user-wide — one ``cache/<name>/editable/`` shared by every
directory on the machine. So it deliberately *loses* the unpinned
fallback: linking a checkout to debug one thing must not change what
every agent session everywhere runs. ``tb use <name>@editable`` opts in,
scoped to wherever you run it.

The cost of that choice is the "my edits do nothing" confusion, so the
three surfaces that can see the situation all have to report it: install
(earliest), ``tb list``, and serve discovery.

These tests cover the rule itself, both directions of each surface's
message, and the interaction with pins.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from toolbase import cli
from toolbase import config as toolbase_config
from toolbase.envs import (
    add_pin,
    cache_dir,
    default_project_root,
    local_manifest_path,
    project_manifest_path,
    write_install_meta,
)
from toolbase.envs.resolve import (
    EDITABLE,
    HIGHEST,
    ONLY,
    PINNED,
    resolve_version,
    sort_versions,
    version_sort_key,
)


# ── the rule itself (pure) ──────────────────────────────────────────────


class TestOrdering:
    def test_editable_sorts_below_every_numbered_version(self):
        assert sort_versions(["editable", "0.0.1"]) == ["0.0.1", "editable"]
        assert sort_versions(["2.10.0", "editable", "1.0.0"]) == [
            "2.10.0", "1.0.0", "editable"]

    def test_editable_scores_the_unparseable_key(self):
        assert version_sort_key("editable") == (0, 0, 0)
        assert version_sort_key("1.2.3") == (1, 2, 3)

    def test_numeric_ordering_is_not_lexicographic(self):
        assert sort_versions(["2.9.0", "2.10.0"]) == ["2.10.0", "2.9.0"]


class TestResolution:
    def test_editable_loses_the_unpinned_fallback(self):
        """The core of the opt-in rule."""
        r = resolve_version(["1.0.0", "2.0.0", "editable"])
        assert r.version == "2.0.0"
        assert r.reason == HIGHEST

    def test_editable_loses_even_to_a_single_numbered_slot(self):
        r = resolve_version(["0.0.1", "editable"])
        assert r.version == "0.0.1"
        assert r.reason == HIGHEST

    def test_lone_editable_slot_serves(self):
        """Nothing to lose to. A toolkit only ever installed editable
        works without any pin — the common authoring case."""
        r = resolve_version(["editable"])
        assert r.version == "editable"
        assert r.reason == ONLY

    def test_pin_opts_in(self):
        r = resolve_version(["1.0.0", "2.0.0", "editable"], pin="editable")
        assert r.version == "editable"
        assert r.reason == PINNED

    def test_pin_to_a_number_keeps_the_checkout_out(self):
        r = resolve_version(["1.0.0", "2.0.0", "editable"], pin="1.0.0")
        assert r.version == "1.0.0"
        assert r.reason == PINNED

    def test_a_pin_naming_a_removed_editable_slot_serves_nothing(self):
        """Same refusal as any dangling pin — it must not fall through
        to a numbered slot the user didn't choose."""
        r = resolve_version(["1.0.0", "2.0.0"], pin="editable")
        assert not r.ok
        assert "editable" in r.describe()


# ── shared fixture for the CLI/serve surfaces ───────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    workdir = tmp_path / "_cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return fake


def _slot(name: str, version: str, source_path: str | None = None) -> Path:
    slot = cache_dir(name, version)
    slot.mkdir(parents=True, exist_ok=True)
    extras: dict = {}
    if version == EDITABLE:
        extras = {"editable": True, "source_path": source_path or "/src/kit"}
    write_install_meta(
        slot, name=name, version=version,
        install_method="venv", python_version="3.12", extras=extras,
    )
    return slot


def _pin(name: str, version: str) -> None:
    add_pin(project_manifest_path(default_project_root()), name, version)


# ── tb list ─────────────────────────────────────────────────────────────


class TestListSurface:
    def test_warns_when_the_checkout_is_not_serving(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", EDITABLE, "/src/kit")
        r = CliRunner().invoke(cli.main, ["list"])
        assert r.exit_code == 0, r.output
        assert "editable checkout is NOT what serves" in r.output
        # The fix is one command, and it's spelled out.
        assert "tb use kit@editable" in r.output

    def test_no_warning_once_the_checkout_is_pinned(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", EDITABLE, "/src/kit")
        _pin("kit", EDITABLE)
        r = CliRunner().invoke(cli.main, ["list"])
        assert r.exit_code == 0, r.output
        assert "NOT what serves" not in r.output

    def test_no_warning_for_a_lone_editable_slot(self, env):
        """It serves; there's nothing to warn about."""
        _slot("kit", EDITABLE, "/src/kit")
        r = CliRunner().invoke(cli.main, ["list"])
        assert r.exit_code == 0, r.output
        assert "NOT what serves" not in r.output

    def test_no_warning_without_an_editable_slot(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", "2.0.0")
        r = CliRunner().invoke(cli.main, ["list"])
        assert r.exit_code == 0, r.output
        assert "editable" not in r.output

    def test_serving_marker_lands_on_the_numbered_slot(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", EDITABLE, "/src/kit")
        r = CliRunner().invoke(cli.main, ["list"])
        lines = r.output.splitlines()
        numbered = next(l for l in lines if "1.0.0" in l and "serving" not in l)
        editable_row = next(l for l in lines if "editable" in l and "->" in l)
        assert "<-" in numbered
        assert "<-" not in editable_row

    def test_editable_row_sorts_last(self, env):
        _slot("kit", "1.0.0")
        _slot("kit", EDITABLE, "/src/kit")
        r = CliRunner().invoke(cli.main, ["list"])
        # Display order must match resolution order, or the rows tell a
        # different story from the marker.
        assert r.output.index("1.0.0") < r.output.index("-> /src/kit")

    def test_json_reports_the_numbered_slot_as_serving(self, env):
        import json
        _slot("kit", "1.0.0")
        _slot("kit", EDITABLE, "/src/kit")
        r = CliRunner().invoke(cli.main, ["list", "--json"])
        payload = {rec["version"]: rec for rec in json.loads(r.output)}
        assert payload["1.0.0"]["serving"] is True
        assert payload[EDITABLE]["serving"] is False


# ── serve discovery ─────────────────────────────────────────────────────


def _cache_entry(name, version, source_path=None):
    return SimpleNamespace(
        name=name, version=version,
        legacy_meta={"environment": "venv", "python_path": "x",
                     "python_version": "3.12"},
        install_meta=(
            {"editable": True, "source_path": source_path,
             "install_method": "venv"}
            if version == EDITABLE else {"install_method": "venv"}
        ),
        path=Path(f"/cache/{name}/{version}"),
    )


@pytest.fixture
def discovered(tmp_path, monkeypatch):
    from toolbase.serve import orchestrator as orch

    def run(entries):
        monkeypatch.setattr("toolbase.envs.walk_cache", lambda: entries)
        monkeypatch.setattr(
            "toolbase.cli._resolve_active_project_root",
            lambda: (tmp_path, "test"))
        monkeypatch.setattr(
            "toolbase.envs.project_manifest_path",
            lambda root: tmp_path / "manifest.yaml")
        return {d.name: d for d in orch.discover_toolkits()}

    return run


class TestServeSurface:
    def test_numbered_slot_serves_and_the_note_explains(
        self, discovered, tmp_path,
    ):
        d = discovered([_cache_entry("kit", "2.3.0"),
                        _cache_entry("kit", EDITABLE, "/src/kit")])
        assert d["kit"].path.name == "2.3.0"
        note = d["kit"].meta.get("shadow_note", "")
        assert "NOT what serves" in note
        assert "/src/kit" in note
        assert "tb use kit@editable" in note

    def test_pinned_checkout_serves_with_no_note(self, discovered, tmp_path):
        add_pin(tmp_path / "manifest.yaml", "kit", EDITABLE)
        d = discovered([_cache_entry("kit", "2.3.0"),
                        _cache_entry("kit", EDITABLE, "/src/kit")])
        assert d["kit"].path.name == EDITABLE
        assert "shadow_note" not in d["kit"].meta

    def test_private_layer_pin_also_opts_in(self, discovered, tmp_path):
        """`tb use --private` writes manifest.local.yaml, which wins per
        name over the committed layer."""
        add_pin(tmp_path / "manifest.yaml", "kit", "2.3.0")
        add_pin(local_manifest_path(tmp_path / "manifest.yaml"),
                "kit", EDITABLE)
        d = discovered([_cache_entry("kit", "2.3.0"),
                        _cache_entry("kit", EDITABLE, "/src/kit")])
        assert d["kit"].path.name == EDITABLE

    def test_lone_editable_slot_serves_with_no_note(self, discovered, tmp_path):
        d = discovered([_cache_entry("kit", EDITABLE, "/src/kit")])
        assert d["kit"].path.name == EDITABLE
        assert "shadow_note" not in d["kit"].meta

    def test_no_note_without_an_editable_slot(self, discovered, tmp_path):
        d = discovered([_cache_entry("kit", "2.2.0"),
                        _cache_entry("kit", "2.3.0")])
        assert "shadow_note" not in d["kit"].meta


# ── the two views agree ─────────────────────────────────────────────────


class TestSurfacesAgree:
    def test_list_and_serve_pick_the_same_slot(self, env):
        """Both go through resolve_version; this pins that they can't
        drift apart again."""
        from toolbase.serve.orchestrator import discover_toolkits
        _slot("kit", "1.0.0")
        _slot("kit", EDITABLE, "/src/kit")
        for slot in (cache_dir("kit", "1.0.0"), cache_dir("kit", EDITABLE)):
            (slot / "toolkit.yaml").write_text(
                "name: kit\nversion: 1.0.0\ndescription: x\nauthor: x\n"
                "license: MIT\ncategory: general\npython_version: '3.12'\n"
            )
        disc = {d.name: d for d in discover_toolkits()}
        assert disc["kit"].path.name == "1.0.0"

        r = CliRunner().invoke(cli.main, ["list", "--json"])
        import json
        serving = [rec["version"] for rec in json.loads(r.output)
                   if rec["serving"]]
        assert serving == ["1.0.0"]

        # And after opting in, both move together.
        CliRunner().invoke(cli.main, ["use", f"kit@{EDITABLE}"])
        disc = {d.name: d for d in discover_toolkits()}
        assert disc["kit"].path.name == EDITABLE
        r = CliRunner().invoke(cli.main, ["list", "--json"])
        serving = [rec["version"] for rec in json.loads(r.output)
                   if rec["serving"]]
        assert serving == [EDITABLE]
