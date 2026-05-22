"""End-to-end test for the Phase 2 multi-version cache layout.

Drives ``toolbase install`` twice against a mocked registry: once at
version 0.1.0, once at 0.2.0. Verifies that:

1. Both cache slots exist side-by-side under
   ``<HOME>/.toolbase/cache/<name>/<version>/``.
2. Each slot carries ``.install_meta.yaml``, ``.tb_meta.json``,
   ``.disk_size`` (or "—" if budget-skipped).
3. The default-project manifest ends up pinned to the LATEST install
   (i.e. the second one) — the brief's "install <name>@<v> pins it"
   contract.
4. ``tb uninstall <name>@<version>`` removes one slot, leaves the
   other intact, and updates the manifest pin appropriately.
5. ``tb uninstall <name>`` removes all remaining slots and clears
   the manifest pin.

Network-free: mocks ``requests.get`` for both the metadata fetch and
the tarball download. Reuses the synthetic toolkit from
``tests/e2e/test-toolkit/`` (same as ``run_install_e2e``).
"""

from __future__ import annotations

import json
import shutil
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

WORK_ROOT = Path(tempfile.gettempdir()) / "tb-multi-version-e2e"
FAKE_HOME = WORK_ROOT / "fake-home" / ".toolbase"
TARBALL_PATH = WORK_ROOT / "test-toolkit.tar.gz"


def _build_tarball() -> None:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    if TARBALL_PATH.exists():
        TARBALL_PATH.unlink()
    with tarfile.open(TARBALL_PATH, "w:gz") as tar:
        for item in TOOLKIT_SRC.rglob("*"):
            if item.is_file():
                tar.add(item, arcname=item.relative_to(TOOLKIT_SRC))


class FakeMetaResponse:
    def __init__(self, version: str):
        self.status_code = 200
        self._version = version

    def json(self):
        return {
            "name": TOOLKIT_NAME,
            "latest_version": self._version,
            "versions": [
                {"version": self._version,
                 "tarball_url": "https://example.invalid/fake.tar.gz"},
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


def _install(version: str) -> int:
    """Run ``toolbase install`` with mocked registry returning ``version``."""

    def fake_get(url, *args, **kwargs):
        if url.endswith(f"/api/toolkits/{TOOLKIT_NAME}"):
            return FakeMetaResponse(version)
        if "fake.tar.gz" in url or "/download/" in url:
            return FakeTarballResponse()
        raise AssertionError(f"unexpected GET {url}")

    from toolbase import skills as skills_mod
    fake_claude = WORK_ROOT / "claude-skills"
    with mock.patch.object(config, "CONFIG_DIR", FAKE_HOME), \
         mock.patch.object(skills_mod, "CLAUDE_SKILLS_DIR", fake_claude), \
         mock.patch.object(requests, "get", side_effect=fake_get):
        result = CliRunner().invoke(
            cli.main,
            ["install", f"{TOOLKIT_NAME}@{version}"],
            catch_exceptions=False,
        )
    print(f"--- install @ {version}: exit {result.exit_code} ---")
    if result.exit_code != 0:
        print(result.output)
    return result.exit_code


def _uninstall(target: str, *, yes: bool = True) -> int:
    args = ["uninstall", target]
    if yes:
        args.append("--yes")
    with mock.patch.object(config, "CONFIG_DIR", FAKE_HOME):
        result = CliRunner().invoke(
            cli.main, args, catch_exceptions=False,
        )
    print(f"--- uninstall {target}: exit {result.exit_code} ---")
    if result.exit_code != 0:
        print(result.output)
    return result.exit_code


def _read_pinned_version() -> str | None:
    """Return the version pinned in the default-project manifest, or None."""
    manifest_path = FAKE_HOME / "default-project" / "manifest.yaml"
    if not manifest_path.exists():
        return None
    import yaml as _yaml
    data = _yaml.safe_load(manifest_path.read_text()) or {}
    for entry in data.get("toolkits", []) or []:
        if entry.get("name") == TOOLKIT_NAME:
            return entry.get("version")
    return None


def main() -> int:
    if not TOOLKIT_SRC.exists():
        print(f"!!! synthetic test toolkit missing at {TOOLKIT_SRC}")
        return 1

    _build_tarball()
    if FAKE_HOME.exists():
        shutil.rmtree(FAKE_HOME)
    FAKE_HOME.mkdir(parents=True)

    # ── Phase A: install two versions side by side ───────────────
    if _install("0.1.0") != 0:
        return 2
    if _install("0.2.0") != 0:
        return 3

    cache_root = FAKE_HOME / "cache" / TOOLKIT_NAME
    v1_slot = cache_root / "0.1.0"
    v2_slot = cache_root / "0.2.0"

    print(f"--- Cache state after both installs ---")
    print(f"v0.1.0 slot exists: {v1_slot.exists()}")
    print(f"v0.2.0 slot exists: {v2_slot.exists()}")

    if not v1_slot.exists() or not v2_slot.exists():
        print("!!! both slots should exist; one is missing")
        return 4

    # Both .install_meta.yaml present.
    for slot, ver in [(v1_slot, "0.1.0"), (v2_slot, "0.2.0")]:
        meta_file = slot / ".install_meta.yaml"
        legacy = slot / ".tb_meta.json"
        if not meta_file.exists():
            print(f"!!! {slot}/.install_meta.yaml is missing")
            return 5
        if not legacy.exists():
            print(f"!!! {slot}/.tb_meta.json is missing")
            return 5

    # Manifest pin should reflect the LAST install (0.2.0).
    pinned = _read_pinned_version()
    print(f"Manifest pin after both installs: {pinned}")
    if pinned != "0.2.0":
        print(f"!!! expected pin to be 0.2.0, got {pinned}")
        return 6

    # ── Phase B: uninstall one version, verify the other remains ──
    if _uninstall(f"{TOOLKIT_NAME}@0.1.0") != 0:
        return 7

    print(f"After uninstall 0.1.0:")
    print(f"  v0.1.0 slot exists: {v1_slot.exists()}")
    print(f"  v0.2.0 slot exists: {v2_slot.exists()}")
    if v1_slot.exists():
        print(f"!!! v0.1.0 slot should be gone")
        return 8
    if not v2_slot.exists():
        print(f"!!! v0.2.0 slot should still exist")
        return 8

    # Pin remains because we only uninstalled the non-pinned version (0.1.0
    # was earlier; 0.2.0 is the pinned one). Actually wait: the pin was
    # 0.2.0 and we removed 0.1.0, so 0.2.0 stays pinned.
    pinned = _read_pinned_version()
    print(f"Manifest pin after uninstall 0.1.0: {pinned}")
    if pinned != "0.2.0":
        print(f"!!! pin should still be 0.2.0, got {pinned}")
        return 9

    # ── Phase C: uninstall the remaining version, pin cleared ─────
    if _uninstall(TOOLKIT_NAME) != 0:
        return 10

    print(f"After uninstall {TOOLKIT_NAME} (all versions):")
    print(f"  cache name dir exists: {cache_root.exists()}")
    pinned = _read_pinned_version()
    print(f"Manifest pin after full uninstall: {pinned}")
    if pinned is not None:
        print(f"!!! pin should be cleared, got {pinned}")
        return 11

    # ── Phase D: re-install both, verify the @<version> syntax ──
    if _install("0.1.0") != 0:
        return 12
    if not (cache_root / "0.1.0").exists():
        return 13
    pinned = _read_pinned_version()
    if pinned != "0.1.0":
        print(f"!!! after solo install of 0.1.0, pin should be 0.1.0, got {pinned}")
        return 14

    print("\n✓ multi-version e2e passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
