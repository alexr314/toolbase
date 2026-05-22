"""End-to-end test for Phase 6 legacy-layout cutover.

Exercises the full Phase-6 cutover flow:

1. Seed a fake ``~/.toolbase/toolkits/<name>/`` directory with
   placeholder content (simulating a 0.4.x install on disk).
2. Run an arbitrary ``tb`` command (``tb list``); observe the
   heads-up message on stderr pointing at ``tb reset``.
3. Confirm ``tb list --json`` keeps stdout clean (parseable JSON),
   even with the heads-up firing on stderr.
4. Run ``tb reset --dry-run``; observe the path is listed and
   nothing is deleted.
5. Run ``tb reset --yes``; observe the legacy dir is removed.
6. Re-run ``tb list``; observe no heads-up (legacy cleaned).
7. Confirm ``tb reset --all --yes`` is idempotent (no-op when
   nothing exists).

Network-free. No registry calls. No subprocess spawning. Pure CLI
invocation via Click's CliRunner against a fake-home tmpdir.

Run from the repo root:

    .venv/bin/python tests/e2e/run_legacy_layout_e2e.py

Side effect: creates a temp tree under ``$TMPDIR/tb-legacy-e2e/``
(re-created per run).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from click.testing import CliRunner

from toolbase import cli, config as toolbase_config


WORK_ROOT = Path(tempfile.gettempdir()) / "tb-legacy-e2e"
FAKE_HOME = WORK_ROOT / "fake-home" / ".toolbase"


def _reset_work_root() -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    FAKE_HOME.mkdir(parents=True)


def _seed_legacy_install() -> Path:
    """Drop a synthetic ``toolkits/foo/`` to look like a 0.4.x install."""
    legacy = FAKE_HOME / "toolkits"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "foo").mkdir()
    (legacy / "foo" / ".stk_meta.json").write_text(
        '{"name": "foo", "version": "0.1.0", "environment": "venv"}'
    )
    (legacy / "foo" / "tools").mkdir()
    (legacy / "foo" / "tools" / "__init__.py").write_text("# legacy\n")
    return legacy


def _seed_unrelated_state() -> None:
    """Files we expect ``tb reset`` (cutover mode) to PRESERVE."""
    cfg = FAKE_HOME / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "user.yaml").write_text("schema_version: 1\napi_key: real-secret\n")
    (FAKE_HOME / "config.json").write_text('{"token": "real-login"}')
    logs = FAKE_HOME / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "serve.log").write_text("...\n")


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def main() -> int:
    _reset_work_root()
    # Redirect the CLI's storage root.
    toolbase_config.CONFIG_DIR = FAKE_HOME
    # Make sure no stale suppression sticks from a prior invocation.
    os.environ.pop("TOOLBASE_SUPPRESS_LEGACY_WARNING", None)

    legacy_dir = _seed_legacy_install()
    _seed_unrelated_state()

    runner = CliRunner()

    # 1. Heads-up fires when legacy exists.
    step("tb list (heads-up should appear on stderr)")
    result = runner.invoke(cli.main, ["list"])
    print("stdout:", result.stdout.rstrip())
    print("stderr:", (result.stderr or "").rstrip())
    assert result.exit_code == 0, "tb list failed"
    assert "Heads up: 0.5.0 adds multi-version installs" in (
        result.stderr or ""
    ), "Heads-up not on stderr"
    assert "tb reset" in (result.stderr or "")
    # stdout must NOT carry the heads-up (clean for parsing).
    assert "Heads up" not in result.stdout

    # 2. --json output stays parseable on stdout even with heads-up firing.
    step("tb list --json (stdout still parseable JSON)")
    result = runner.invoke(cli.main, ["list", "--json"])
    assert result.exit_code == 0
    # No installs exist in the cache, but the JSON shape is correct.
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert "Heads up" in (result.stderr or ""), \
        "Heads-up should still surface to stderr in --json mode"
    print("stdout (JSON):", json.dumps(payload))
    print("stderr:", (result.stderr or "").rstrip()[:80], "...")

    # 3. Dry-run lists target path; deletes nothing.
    step("tb reset --dry-run (lists target, deletes nothing)")
    result = runner.invoke(cli.main, ["reset", "--dry-run"])
    print(result.stdout.rstrip())
    assert result.exit_code == 0
    # Rich may wrap the path across lines in narrow terminals; check the
    # leaf name (``toolkits``) is named in the output, and that "Dry-run"
    # tag was printed.
    assert "toolkits" in result.stdout, "Dry-run should name the legacy dir"
    assert "Dry-run" in result.stdout
    assert legacy_dir.exists(), "Dry-run must not delete"
    assert (legacy_dir / "foo").exists()

    # 4. Real reset, --yes (CI-friendly).
    step("tb reset --yes (cutover cleanup)")
    result = runner.invoke(cli.main, ["reset", "--yes"])
    print(result.stdout.rstrip())
    assert result.exit_code == 0, result.output
    # Legacy gone.
    assert not legacy_dir.exists(), "Legacy dir should be removed"
    # Preserved.
    assert (FAKE_HOME / "config" / "user.yaml").exists(), \
        "config/ must be preserved by default reset"
    assert (FAKE_HOME / "config.json").exists(), \
        "config.json (login state) must always be preserved"
    assert (FAKE_HOME / "logs" / "serve.log").exists(), \
        "logs/ must be preserved"

    # 5. Subsequent tb list shows no heads-up.
    step("tb list after cleanup (heads-up should be gone)")
    # Clear the env var the previous reset set, to verify the detection
    # truly no longer fires (not just because of suppression).
    os.environ.pop("TOOLBASE_SUPPRESS_LEGACY_WARNING", None)
    result = runner.invoke(cli.main, ["list"])
    print("stdout:", result.stdout.rstrip())
    print("stderr:", (result.stderr or "").rstrip())
    assert "Heads up" not in (result.stderr or ""), \
        "Heads-up should not fire when legacy dir is gone"

    # 6. Idempotency: reset --all --yes when nothing exists is a no-op.
    step("tb reset --all --yes (idempotent when nothing to clean)")
    # First clean out the unrelated state so even --all has nothing.
    shutil.rmtree(FAKE_HOME / "config", ignore_errors=True)
    result = runner.invoke(cli.main, ["reset", "--all", "--yes"])
    print(result.stdout.rstrip())
    assert result.exit_code == 0
    assert "Nothing to reset" in result.stdout, \
        "Empty --all should report nothing to do"

    # 7. UsageError: --include-config without --all.
    step("tb reset --include-config (without --all) is a UsageError")
    result = runner.invoke(cli.main, ["reset", "--include-config", "--yes"])
    print(result.stdout.rstrip())
    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
    assert "--all" in combined, "Error should mention --all"

    print("\nAll legacy-layout cutover assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
