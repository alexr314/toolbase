"""End-to-end smoke test for ``toolbase install`` against a mocked registry.

Exercises the full install pipeline (download → extract → detect env → set
up venv → save metadata → success message) without depending on the live
registry having any particular toolkit published. Useful as a regression
test when changes to install logic should not depend on network state.

Run from the repo root:

    .venv/bin/python -m tb-package.tests.e2e.run_install_e2e

Or with the test venv activated:

    python tests/e2e/run_install_e2e.py

Side effects: creates a temp install root under ``$TMPDIR/tb-e2e/`` and
populates it with a venv installation of the synthetic toolkit. Cleaned up
on rerun. No network access. No live registry required.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

import requests
from click.testing import CliRunner

from toolbase import cli, config


THIS_DIR = Path(__file__).resolve().parent
TOOLKIT_SRC = THIS_DIR / "test-toolkit"
TOOLKIT_NAME = "tb-e2e-test"
TOOLKIT_VERSION = "0.1.0"

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-e2e"
# In 0.5.0, the install lands at ``<FAKE_HOME>/cache/<name>/<version>/``.
# We point CONFIG_DIR at FAKE_HOME so the entire substrate (cache,
# default-project manifest, etc.) lands under our tmp tree.
FAKE_HOME = WORK_ROOT / "fake-home" / ".toolbase"
# Legacy expected slot for serve e2e back-compat — kept as a symlink
# target so run_serve_e2e.py can point its HOME at WORK_ROOT/fake-home/.
INSTALL_ROOT = FAKE_HOME / "cache"
TARBALL_PATH = WORK_ROOT / "test-toolkit.tar.gz"


def _build_tarball() -> None:
    """Tar up the synthetic toolkit so the mocked download has bytes to serve."""
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    if TARBALL_PATH.exists():
        TARBALL_PATH.unlink()
    with tarfile.open(TARBALL_PATH, "w:gz") as tar:
        for item in TOOLKIT_SRC.rglob("*"):
            if item.is_file():
                tar.add(item, arcname=item.relative_to(TOOLKIT_SRC))


class FakeMetaResponse:
    status_code = 200

    def json(self):
        return {
            "name": TOOLKIT_NAME,
            "latest_version": TOOLKIT_VERSION,
            "versions": [
                {
                    "version": TOOLKIT_VERSION,
                    "tarball_url": "https://example.invalid/fake.tar.gz",
                }
            ],
        }


class FakeTarballResponse:
    status_code = 200

    def __init__(self):
        self.headers = {"content-length": str(TARBALL_PATH.stat().st_size)}

    def iter_content(self, chunk_size=8192):
        with open(TARBALL_PATH, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk


def fake_get(url, *args, **kwargs):
    if url.endswith(f"/api/toolkits/{TOOLKIT_NAME}"):
        return FakeMetaResponse()
    if "fake.tar.gz" in url or "/download/" in url:
        return FakeTarballResponse()
    raise AssertionError(f"unexpected GET {url}")


def main() -> int:
    if not TOOLKIT_SRC.exists():
        print(f"!!! synthetic test toolkit missing at {TOOLKIT_SRC}")
        return 1

    _build_tarball()

    # Clear and recreate the fake home.
    if FAKE_HOME.exists():
        shutil.rmtree(FAKE_HOME)
    FAKE_HOME.mkdir(parents=True)

    # Redirect the skills-surface destination to a tmp dir so we don't
    # write synthetic-toolkit skills into the developer's real
    # ~/.claude/skills/ directory while running the harness.
    fake_claude_skills = WORK_ROOT / "claude-skills"
    if fake_claude_skills.exists():
        shutil.rmtree(fake_claude_skills)

    from toolbase import skills as skills_mod

    with mock.patch.object(config, "CONFIG_DIR", FAKE_HOME), \
         mock.patch.object(skills_mod, "CLAUDE_SKILLS_DIR", fake_claude_skills), \
         mock.patch.object(requests, "get", side_effect=fake_get):
        runner = CliRunner()
        result = runner.invoke(
            cli.main,
            ["install", TOOLKIT_NAME],
            catch_exceptions=False,
        )

    print("--- exit code:", result.exit_code)
    print("--- output ---")
    print(result.output)

    # 0.5.0 install lands at cache/<name>/<version>/. We discover the
    # version dir by walking the name dir.
    name_dir = FAKE_HOME / "cache" / TOOLKIT_NAME
    if not name_dir.exists():
        print(f"!!! no install dir written at {name_dir}")
        return 1
    version_dirs = [p for p in name_dir.iterdir() if p.is_dir()]
    if not version_dirs:
        print(f"!!! no version slot under {name_dir}")
        return 1
    slot = version_dirs[0]
    meta_file = slot / ".tb_meta.json"
    install_meta = slot / ".install_meta.yaml"
    if not meta_file.exists():
        print(f"!!! no .tb_meta.json was written at {slot}")
        return 1
    if not install_meta.exists():
        print(f"!!! no .install_meta.yaml was written at {slot}")
        return 1

    print(f"--- written cache slot: {slot} ---")
    print(json.dumps(json.loads(meta_file.read_text()), indent=2))
    print("--- .install_meta.yaml ---")
    print(install_meta.read_text())

    # Verify the default-project manifest got a pin.
    manifest_path = FAKE_HOME / "default-project" / "manifest.yaml"
    if not manifest_path.exists():
        print(f"!!! no default-project manifest at {manifest_path}")
        return 1
    print("--- default-project manifest ---")
    print(manifest_path.read_text())

    if result.exit_code != 0:
        print(f"!!! install exited non-zero ({result.exit_code})")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
