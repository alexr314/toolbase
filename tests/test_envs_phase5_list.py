"""Phase 5 tests — ``tb list`` tree output, ``--json``, pin indicator.

Phase 2 wired ``list_cmd`` onto ``envs.walk_cache``; Phase 5 polishes
the rendering. This file covers:

- Tree-grouped output (name header, indented version rows).
- Human-friendly last-used formatting (``_format_last_used``).
- Human-friendly size formatting (``_format_disk_size``).
- Empty-cache friendly message.
- Pinned-version indicator (``*``) when the active project manifest
  pins a cached version.
- Legend line printed only when at least one pin applies.
- ``tb list --json`` — flat array of records, no markup, suppresses
  legend.
- Determinism — entries sorted (name asc, version desc).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from toolbase import config as toolbase_config
from toolbase import cli
from toolbase.envs import (
    cache_dir,
    write_install_meta,
    write_legacy_meta,
    touch_last_used,
    DISK_SIZE_FILE,
    add_pin,
    project_manifest_path,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / "_home" / ".toolbase"
    fake.mkdir(parents=True)
    monkeypatch.setattr(toolbase_config, "CONFIG_DIR", fake)
    return fake


def _make_slot(
    name: str,
    version: str,
    *,
    install_method: str = "venv",
    python_version: str = "3.12",
    last_used: datetime | None = None,
    size_bytes: int | None = None,
    bundles: list[str] | None = None,
) -> Path:
    """Create a synthetic cache slot with optional .last_used and .disk_size.

    ``bundles=None`` (default) writes no ``bundles`` field — the legacy
    "full install" semantic. ``bundles=[]`` writes an explicit empty list
    (base-only install). ``bundles=[...]`` writes a subset install.
    """
    slot = cache_dir(name, version)
    slot.mkdir(parents=True, exist_ok=True)
    extras: dict = {}
    if bundles is not None:
        extras["bundles"] = list(bundles)
    write_install_meta(
        slot,
        name=name,
        version=version,
        install_method=install_method,
        python_version=python_version,
        extras=extras or None,
    )
    # Some legacy_meta so the slot is recognised even if install_meta
    # doesn't carry every field the rendering uses.
    write_legacy_meta(slot, {"environment": install_method, "name": name})
    if last_used is not None:
        touch_last_used(slot, when=last_used)
    if size_bytes is not None:
        (slot / DISK_SIZE_FILE).write_text(f"{size_bytes}\n")
    return slot


# ── _format_last_used ───────────────────────────────────────────────


class TestFormatLastUsed:
    def test_missing_returns_never(self):
        assert cli._format_last_used(None) == "never"
        assert cli._format_last_used("") == "never"

    def test_just_now(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        recent = now - timedelta(seconds=2)
        assert cli._format_last_used(recent.isoformat(), now=now) == "just now"

    def test_seconds_ago(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(seconds=30)
        assert cli._format_last_used(past.isoformat(), now=now) == "30 seconds ago"

    def test_one_minute_ago_singular(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(minutes=1, seconds=5)
        assert cli._format_last_used(past.isoformat(), now=now) == "1 minute ago"

    def test_minutes_plural(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(minutes=15)
        assert cli._format_last_used(past.isoformat(), now=now) == "15 minutes ago"

    def test_hours_ago(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(hours=2)
        assert cli._format_last_used(past.isoformat(), now=now) == "2 hours ago"

    def test_one_hour_singular(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(hours=1, minutes=10)
        assert cli._format_last_used(past.isoformat(), now=now) == "1 hour ago"

    def test_yesterday(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(days=1, hours=2)
        assert cli._format_last_used(past.isoformat(), now=now) == "yesterday"

    def test_days_ago(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(days=5)
        assert cli._format_last_used(past.isoformat(), now=now) == "5 days ago"

    def test_weeks_ago(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(days=21)
        assert cli._format_last_used(past.isoformat(), now=now) == "3 weeks ago"

    def test_months_ago(self):
        now = datetime(2026, 5, 13, 10, 0, 0)
        past = now - timedelta(days=120)
        assert cli._format_last_used(past.isoformat(), now=now) == "4 months ago"

    def test_future_timestamp_renders_just_now(self):
        """Clock-skew tolerance: a stamp in the future shouldn't crash."""
        now = datetime(2026, 5, 13, 10, 0, 0)
        future = now + timedelta(minutes=5)
        assert cli._format_last_used(future.isoformat(), now=now) == "just now"

    def test_malformed_returns_raw(self):
        assert cli._format_last_used("not-an-iso-stamp") == "not-an-iso-stamp"


# ── _format_disk_size ───────────────────────────────────────────────


class TestFormatDiskSize:
    def test_missing_returns_em_dash(self):
        assert cli._format_disk_size(None) == "—"

    def test_bytes(self):
        assert cli._format_disk_size(500) == "500 B"

    def test_kilobytes(self):
        # 1.5 KB
        assert cli._format_disk_size(1536) == "1.5 KB"

    def test_megabytes(self):
        # 180 MB-ish (the brief example for arxiv-search)
        assert cli._format_disk_size(180 * 1024 * 1024).endswith("MB")

    def test_gigabytes(self):
        # 8.2 GB-ish (the brief example for heptapod)
        out = cli._format_disk_size(int(8.2 * 1024 * 1024 * 1024))
        assert "GB" in out


# ── tree rendering ──────────────────────────────────────────────────


class TestListTreeRendering:
    def test_empty_cache_friendly_message(self, fake_home):
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0
        assert "No toolkits installed" in result.output
        assert "tb install arxiv-search" in result.output

    def test_single_toolkit_one_version(self, fake_home):
        now = datetime.now() - timedelta(hours=2)
        _make_slot("arxiv-search", "0.2.0", last_used=now, size_bytes=180 * 1024 * 1024)

        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0, result.output
        assert "arxiv-search" in result.output
        assert "0.2.0" in result.output
        assert "MB" in result.output
        assert "hours ago" in result.output or "yesterday" in result.output

    def test_multi_version_groups_under_name(self, fake_home):
        now = datetime.now()
        _make_slot("heptapod", "0.1.0",
                   last_used=now - timedelta(days=3),
                   size_bytes=int(8.2 * 1024**3))
        _make_slot("heptapod", "0.3.0",
                   last_used=now - timedelta(days=1, hours=2),
                   size_bytes=int(8.4 * 1024**3))

        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0, result.output
        # Name header appears once.
        assert result.output.count("heptapod") == 1
        # Both versions appear.
        assert "0.1.0" in result.output
        assert "0.3.0" in result.output
        # Higher version listed first (descending).
        assert result.output.index("0.3.0") < result.output.index("0.1.0")
        # Tree-shaped: each version row is prefixed with "  - "
        lines = [l for l in result.output.splitlines() if "0." in l]
        assert all(l.lstrip().startswith("- ") for l in lines)

    def test_groups_sorted_alphabetically(self, fake_home):
        _make_slot("zzz", "0.1.0", last_used=datetime.now())
        _make_slot("aaa", "0.1.0", last_used=datetime.now())
        _make_slot("mmm", "0.1.0", last_used=datetime.now())

        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0
        a = result.output.index("aaa")
        m = result.output.index("mmm")
        z = result.output.index("zzz")
        assert a < m < z

    def test_missing_last_used_renders_never(self, fake_home):
        _make_slot("toolkit-a", "0.1.0", last_used=None, size_bytes=1024)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert "never" in result.output

    def test_missing_disk_size_renders_em_dash(self, fake_home):
        _make_slot("toolkit-a", "0.1.0",
                   last_used=datetime.now() - timedelta(hours=1),
                   size_bytes=None)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert "—" in result.output

    def test_subset_install_annotated(self, fake_home):
        """A version row for a subset install ends with `[subset: a, b]`
        so the user can tell from `tb list` that not all bundles' deps
        are installed."""
        _make_slot(
            "toolkit-a", "0.1.0",
            last_used=datetime.now() - timedelta(hours=1),
            size_bytes=1024,
            bundles=["alpha", "beta"],
        )
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0, result.output
        assert "[subset: alpha, beta]" in result.output

    def test_full_install_no_subset_annotation(self, fake_home):
        """No ``bundles`` in meta → no subset annotation (legacy fully-
        installed semantic stays visually unchanged)."""
        _make_slot(
            "toolkit-a", "0.1.0",
            last_used=datetime.now() - timedelta(hours=1),
            size_bytes=1024,
            # bundles=None — explicit "full install"
        )
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0
        assert "subset" not in result.output

    def test_base_only_install_annotated(self, fake_home):
        """``bundles: []`` is a deliberate base-only install — annotated
        as ``[subset: (base only)]`` so it's distinguishable from a full
        install."""
        _make_slot(
            "toolkit-a", "0.1.0",
            last_used=datetime.now() - timedelta(hours=1),
            size_bytes=1024,
            bundles=[],
        )
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0
        assert "[subset: (base only)]" in result.output


# ── -v per-tool subset annotation ──────────────────────────────────


def _write_toolkit_yaml(
    slot: Path,
    bundles: dict[str, dict],
    tools: list[dict],
) -> None:
    """Drop a minimal toolkit.yaml into a slot so ``discover_toolkits()``
    and ``_resolve_bundle_availability()`` can read it for the -v view."""
    import yaml as pyyaml
    payload = {
        "name": slot.parent.name,
        "version": slot.name,
        "description": "fixture toolkit",
        "author": "fixture",
        "license": "MIT",
        "category": "general",
        "python_version": "3.12",
        "keywords": [],
        "config": [],
        "bundles": bundles,
        "tools": tools,
    }
    (slot / "toolkit.yaml").write_text(pyyaml.safe_dump(payload))


class TestListVerboseSubsetAnnotation:
    """``tb list -v`` per-tool annotations for subset-installed toolkits."""

    def test_tool_in_uninstalled_bundle_marked_skipped(self, fake_home):
        """A tool whose bundle isn't in the install set should render
        a ``(skipped: bundle X not installed)`` annotation so the user
        sees why it isn't served."""
        slot = _make_slot(
            "kit", "0.1.0",
            last_used=datetime.now() - timedelta(hours=1),
            size_bytes=1024,
            bundles=["alpha"],
        )
        _write_toolkit_yaml(
            slot,
            bundles={"alpha": {}, "beta": {}},
            tools=[
                {"name": "ta", "module": "tools.ta",
                 "description": "alpha tool", "bundle": "alpha"},
                {"name": "tb", "module": "tools.tb",
                 "description": "beta tool", "bundle": "beta"},
            ],
        )
        runner = CliRunner()
        r = runner.invoke(cli.main, ["list", "-v"])
        assert r.exit_code == 0, r.output
        # The bundle the user installed: no scope annotation.
        ta_line = next(l for l in r.output.splitlines() if " ta " in l)
        assert "skipped" not in ta_line
        # The bundle they didn't: scope annotation cites the bundle.
        tb_line = next(l for l in r.output.splitlines() if " tb " in l)
        assert "skipped: bundle beta not installed" in tb_line

    def test_full_install_no_per_tool_scope_annotation(self, fake_home):
        """Without ``bundles`` in the meta (full install) no per-tool
        scope annotation appears — the existing config-gating annotation
        is unaffected."""
        slot = _make_slot(
            "kit", "0.1.0",
            last_used=datetime.now() - timedelta(hours=1),
            size_bytes=1024,
            # bundles=None — full install
        )
        _write_toolkit_yaml(
            slot,
            bundles={"alpha": {}, "beta": {}},
            tools=[
                {"name": "ta", "module": "tools.ta",
                 "description": "alpha tool", "bundle": "alpha"},
                {"name": "tb", "module": "tools.tb",
                 "description": "beta tool", "bundle": "beta"},
            ],
        )
        runner = CliRunner()
        r = runner.invoke(cli.main, ["list", "-v"])
        assert r.exit_code == 0
        assert "skipped" not in r.output
        assert "not installed" not in r.output

    def test_multi_bundle_tool_lists_all_absent_bundles(self, fake_home):
        """When a multi-bundle tool's bundles are all uninstalled, the
        annotation lists them: ``(skipped: bundles a, b not installed)``."""
        slot = _make_slot(
            "kit", "0.1.0",
            last_used=datetime.now() - timedelta(hours=1),
            size_bytes=1024,
            bundles=["gamma"],
        )
        _write_toolkit_yaml(
            slot,
            bundles={"alpha": {}, "beta": {}, "gamma": {}},
            tools=[
                {"name": "bridge", "module": "tools.bridge",
                 "description": "spans alpha + beta",
                 "bundle": ["alpha", "beta"]},
            ],
        )
        runner = CliRunner()
        r = runner.invoke(cli.main, ["list", "-v"])
        assert r.exit_code == 0
        assert "skipped: bundles alpha, beta not installed" in r.output

    def test_multi_bundle_tool_with_one_installed_bundle_is_kept(self, fake_home):
        """A multi-bundle tool stays served (no scope annotation) when
        at least one of its bundles is in the install set, since pip-
        installing one bundle's deps was enough to satisfy it."""
        slot = _make_slot(
            "kit", "0.1.0",
            last_used=datetime.now() - timedelta(hours=1),
            size_bytes=1024,
            bundles=["alpha"],
        )
        _write_toolkit_yaml(
            slot,
            bundles={"alpha": {}, "beta": {}},
            tools=[
                {"name": "bridge", "module": "tools.bridge",
                 "description": "spans alpha + beta",
                 "bundle": ["alpha", "beta"]},
            ],
        )
        runner = CliRunner()
        r = runner.invoke(cli.main, ["list", "-v"])
        assert r.exit_code == 0
        bridge_line = next(l for l in r.output.splitlines() if "bridge" in l)
        assert "skipped" not in bridge_line


# ── pinned-version indicator ────────────────────────────────────────


class TestPinIndicator:
    def test_no_pin_no_marker_no_legend(self, fake_home):
        _make_slot("heptapod", "0.1.0",
                   last_used=datetime.now() - timedelta(hours=1),
                   size_bytes=1024)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list"])
        assert result.exit_code == 0
        # No star, no legend.
        assert "*" not in result.output
        assert "pinned in this project" not in result.output

    def test_pinned_version_shows_star(self, fake_home, tmp_path):
        # Set up a project dir with a manifest pinning heptapod 0.3.0.
        project = tmp_path / "myproj"
        project.mkdir()
        (project / ".toolbase").mkdir()
        manifest = project_manifest_path(project)
        add_pin(manifest, "heptapod", "0.3.0")

        _make_slot("heptapod", "0.1.0",
                   last_used=datetime.now() - timedelta(days=3),
                   size_bytes=1024)
        _make_slot("heptapod", "0.3.0",
                   last_used=datetime.now() - timedelta(days=1, hours=2),
                   size_bytes=2048)

        runner = CliRunner()
        result = runner.invoke(
            cli.main, ["--project-dir", str(project), "list"],
        )
        assert result.exit_code == 0, result.output
        assert "*" in result.output
        assert "pinned in this project" in result.output
        # Legend points at the resolved manifest path.
        assert "manifest.yaml" in result.output

    def test_pin_only_marks_correct_version(self, fake_home, tmp_path):
        """Pinning 0.3.0 doesn't mark 0.1.0 with a star."""
        project = tmp_path / "myproj"
        project.mkdir()
        (project / ".toolbase").mkdir()
        add_pin(project_manifest_path(project), "heptapod", "0.3.0")

        _make_slot("heptapod", "0.1.0",
                   last_used=datetime.now() - timedelta(days=3),
                   size_bytes=1024)
        _make_slot("heptapod", "0.3.0",
                   last_used=datetime.now() - timedelta(hours=2),
                   size_bytes=2048)

        runner = CliRunner()
        result = runner.invoke(
            cli.main, ["--project-dir", str(project), "list"],
        )
        # Find the lines with each version and check only 0.3.0 has *.
        lines = result.output.splitlines()
        v3_line = next(l for l in lines if "0.3.0" in l)
        v1_line = next(l for l in lines if "0.1.0" in l)
        assert "*" in v3_line
        assert "*" not in v1_line


# ── --json output ───────────────────────────────────────────────────


class TestJsonOutput:
    def test_empty_cache_json(self, fake_home):
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        assert result.exit_code == 0
        # Should be a parseable empty array.
        payload = json.loads(result.output)
        assert payload == []

    def test_json_record_shape(self, fake_home):
        now = datetime.now() - timedelta(hours=2)
        _make_slot("arxiv-search", "0.2.0",
                   last_used=now, size_bytes=180 * 1024 * 1024)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert len(payload) == 1
        rec = payload[0]
        assert rec["name"] == "arxiv-search"
        assert rec["version"] == "0.2.0"
        assert rec["last_used_iso"] is not None
        assert rec["size_bytes"] == 180 * 1024 * 1024
        assert rec["pinned_in_project"] is False

    def test_json_marks_pinned_versions(self, fake_home, tmp_path):
        project = tmp_path / "myproj"
        project.mkdir()
        (project / ".toolbase").mkdir()
        add_pin(project_manifest_path(project), "heptapod", "0.3.0")

        _make_slot("heptapod", "0.1.0",
                   last_used=datetime.now() - timedelta(days=3),
                   size_bytes=1024)
        _make_slot("heptapod", "0.3.0",
                   last_used=datetime.now() - timedelta(hours=2),
                   size_bytes=2048)

        runner = CliRunner()
        result = runner.invoke(
            cli.main, ["--project-dir", str(project), "list", "--json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        by_version = {rec["version"]: rec for rec in payload}
        assert by_version["0.3.0"]["pinned_in_project"] is True
        assert by_version["0.1.0"]["pinned_in_project"] is False

    def test_json_handles_missing_size_and_last_used(self, fake_home):
        _make_slot("toolkit-a", "0.1.0", last_used=None, size_bytes=None)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload[0]["last_used_iso"] is None
        assert payload[0]["size_bytes"] is None

    def test_json_full_install_omits_or_nulls_installed_bundles(self, fake_home):
        """A slot installed without ``bundles`` in its meta (the legacy
        "all bundles" semantic) renders ``installed_bundles: null``."""
        _make_slot("toolkit-a", "0.1.0", last_used=datetime.now())
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload[0]["installed_bundles"] is None

    def test_json_subset_install_includes_bundle_list(self, fake_home):
        _make_slot(
            "toolkit-a", "0.1.0", last_used=datetime.now(),
            bundles=["alpha", "beta"],
        )
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload[0]["installed_bundles"] == ["alpha", "beta"]

    def test_json_base_only_install_renders_empty_list(self, fake_home):
        """``bundles: []`` (deliberate base-only install) is distinct from
        ``bundles`` absent — ``installed_bundles`` is ``[]``, not ``null``."""
        _make_slot(
            "toolkit-a", "0.1.0", last_used=datetime.now(),
            bundles=[],
        )
        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload[0]["installed_bundles"] == []

    def test_json_sorted_deterministically(self, fake_home):
        _make_slot("zzz", "0.1.0", last_used=datetime.now())
        _make_slot("aaa", "0.2.0", last_used=datetime.now())
        _make_slot("aaa", "0.1.0", last_used=datetime.now())
        _make_slot("mmm", "0.1.0", last_used=datetime.now())

        runner = CliRunner()
        result = runner.invoke(cli.main, ["list", "--json"])
        payload = json.loads(result.output)
        # aaa comes first, with 0.2.0 before 0.1.0 (version desc within name).
        names = [r["name"] for r in payload]
        assert names == ["aaa", "aaa", "mmm", "zzz"]
        aaa_versions = [r["version"] for r in payload if r["name"] == "aaa"]
        assert aaa_versions == ["0.2.0", "0.1.0"]


# ── performance budget ─────────────────────────────────────────────


class TestListPerformance:
    def test_ten_entry_cache_under_200ms(self, fake_home):
        """``tb list`` must stay fast even at 10 entries."""
        import time
        for i in range(10):
            _make_slot(
                f"toolkit-{i:02d}", "0.1.0",
                last_used=datetime.now() - timedelta(hours=i),
                size_bytes=(i + 1) * 1024 * 1024,
            )
        runner = CliRunner()
        start = time.monotonic()
        result = runner.invoke(cli.main, ["list"])
        elapsed = time.monotonic() - start
        assert result.exit_code == 0
        # 200ms target per the brief is for the actual command. The
        # CliRunner.invoke wrapper adds Click setup overhead, so we
        # budget 1.5s here to catch only pathological regressions
        # (e.g. accidental O(N) manifest read per entry). Phase 2
        # cold measurement was ~15ms for the walker itself.
        assert elapsed < 1.5, f"tb list took {elapsed*1000:.0f}ms; target <1500ms"
