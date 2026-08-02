"""Pre-0.12 ``profiles/`` state keeps working after the rename.

Loadouts were called profiles. The rename moved the directory
(``profiles/`` -> ``loadouts/``) and the serve.yaml key
(``default.profile`` -> ``default.loadout``), either of which would
silently un-serve every toolkit on a machine that hasn't migrated —
the agent would just stop having tools, with nothing to explain it.

So both are read in their old spelling when the new one is absent.
Nothing writes back to the old names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase.envs.paths import (
    legacy_project_profiles_dir,
    legacy_user_profiles_dir,
    project_loadouts_dir,
    user_loadouts_dir,
)
from toolbase.serve.config import load_serve_config
from toolbase.serve.loadouts import discover_loadouts


def _write_loadout(directory: Path, name: str, toolkit: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(
        f"toolkits:\n  {toolkit}: {{}}\n"
    )


class TestLegacyDirectory:
    def test_user_profiles_dir_is_read_when_loadouts_is_absent(self, tmp_path):
        _write_loadout(legacy_user_profiles_dir(base=tmp_path),
                       "default", "heptapod")
        found = discover_loadouts(user_base=tmp_path)
        assert "default" in found
        assert "heptapod" in found["default"].toolkits
        assert found["default"].scope == "user"

    def test_current_directory_wins_when_both_exist(self, tmp_path):
        """A migrated machine must not resurrect the stale copy."""
        _write_loadout(legacy_user_profiles_dir(base=tmp_path),
                       "default", "old-toolkit")
        _write_loadout(user_loadouts_dir(base=tmp_path),
                       "default", "new-toolkit")
        found = discover_loadouts(user_base=tmp_path)
        assert "new-toolkit" in found["default"].toolkits
        assert "old-toolkit" not in found["default"].toolkits

    def test_project_profiles_dir_is_read_too(self, tmp_path):
        project = tmp_path / "repo"
        (project / ".toolbase").mkdir(parents=True)
        _write_loadout(legacy_project_profiles_dir(project),
                       "paper", "arxiv-search")
        found = discover_loadouts(project, user_base=tmp_path)
        assert "paper" in found
        assert found["paper"].scope == "project"

    def test_project_and_user_legacy_dirs_coexist(self, tmp_path):
        project = tmp_path / "repo"
        (project / ".toolbase").mkdir(parents=True)
        _write_loadout(legacy_user_profiles_dir(base=tmp_path),
                       "mine", "heptapod")
        _write_loadout(legacy_project_profiles_dir(project),
                       "paper", "arxiv-search")
        found = discover_loadouts(project, user_base=tmp_path)
        assert set(found) == {"mine", "paper"}

    def test_no_directories_at_all_is_empty_not_an_error(self, tmp_path):
        assert discover_loadouts(user_base=tmp_path) == {}


class TestLegacyServeConfigKey:
    def test_default_profile_key_is_read(self, tmp_path):
        path = tmp_path / "serve.yaml"
        path.write_text("default:\n  profile: paper\n")
        cfg = load_serve_config(path)
        assert cfg.default.loadout == "paper"

    def test_current_key_wins_when_both_present(self, tmp_path):
        path = tmp_path / "serve.yaml"
        path.write_text("default:\n  profile: old\n  loadout: new\n")
        cfg = load_serve_config(path)
        assert cfg.default.loadout == "new"

    def test_neither_key_leaves_it_unset(self, tmp_path):
        path = tmp_path / "serve.yaml"
        path.write_text("default:\n  bare: true\n")
        cfg = load_serve_config(path)
        assert cfg.default.loadout is None

    def test_writing_back_uses_the_current_spelling(self, tmp_path):
        """The file converts itself the first time anything sets the
        active loadout — no separate migration step for serve.yaml."""
        from toolbase.serve.config import save_serve_config
        path = tmp_path / "serve.yaml"
        path.write_text("default:\n  profile: paper\n")
        cfg = load_serve_config(path)
        save_serve_config(cfg, path)

        text = path.read_text()
        assert "loadout: paper" in text
        assert "profile:" not in text
        # And it still reads back the same.
        assert load_serve_config(path).default.loadout == "paper"
