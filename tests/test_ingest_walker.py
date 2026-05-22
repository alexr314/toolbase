"""Unit tests for ``toolbase.ingest.walk_python_files``.

The walker is the input to the AST detector — anything that escapes
the walker gets parsed, anything filtered never gets a chance. So
the test surface here is "what gets through" rather than "is the AST
right." Cover:

- Hardcoded skip dirs (``__pycache__``, ``.venv``, ``.git``, ``node_modules``).
- Test-file filtering (``test_*.py``, ``*_test.py``, ``tests/`` dir).
- Hidden files and dirs (anything starting with ``.``).
- ``.gitignore`` patterns at the root.
- Non-``.py`` files filtered out.
- Output is deterministic (sorted).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from toolbase.ingest import walk_python_files


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestSkipDirs:
    def test_skips_pycache(self, tmp_path):
        _touch(tmp_path / "real.py")
        _touch(tmp_path / "__pycache__" / "cached.py")
        files = list(walk_python_files(tmp_path))
        names = [f.name for f in files]
        assert "real.py" in names
        assert "cached.py" not in names

    def test_skips_venv_dirs(self, tmp_path):
        _touch(tmp_path / "src.py")
        _touch(tmp_path / ".venv" / "lib" / "vendored.py")
        _touch(tmp_path / "venv" / "vendored2.py")
        _touch(tmp_path / "env" / "vendored3.py")
        files = list(walk_python_files(tmp_path))
        names = [f.name for f in files]
        assert "src.py" in names
        assert "vendored.py" not in names
        assert "vendored2.py" not in names
        assert "vendored3.py" not in names

    def test_skips_git_dir(self, tmp_path):
        _touch(tmp_path / "real.py")
        _touch(tmp_path / ".git" / "hooks" / "fake.py")
        files = list(walk_python_files(tmp_path))
        assert all(".git" not in p.parts for p in files)

    def test_skips_node_modules(self, tmp_path):
        _touch(tmp_path / "ok.py")
        _touch(tmp_path / "node_modules" / "weird.py")
        files = list(walk_python_files(tmp_path))
        assert "weird.py" not in [f.name for f in files]

    def test_skips_build_artifacts(self, tmp_path):
        _touch(tmp_path / "src.py")
        _touch(tmp_path / "dist" / "wheel.py")
        _touch(tmp_path / "build" / "tmp.py")
        _touch(tmp_path / "myproj.egg-info" / "PKG-INFO.py")
        files = list(walk_python_files(tmp_path))
        names = [f.name for f in files]
        assert "src.py" in names
        assert "wheel.py" not in names
        assert "tmp.py" not in names
        assert "PKG-INFO.py" not in names


class TestTestFileFiltering:
    def test_test_prefix_files_skipped(self, tmp_path):
        _touch(tmp_path / "real.py")
        _touch(tmp_path / "test_foo.py")
        _touch(tmp_path / "test_bar.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "real.py" in files
        assert "test_foo.py" not in files
        assert "test_bar.py" not in files

    def test_test_suffix_files_skipped(self, tmp_path):
        _touch(tmp_path / "module.py")
        _touch(tmp_path / "module_test.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "module.py" in files
        assert "module_test.py" not in files

    def test_tests_dir_skipped_at_root(self, tmp_path):
        _touch(tmp_path / "src.py")
        _touch(tmp_path / "tests" / "fixture.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "src.py" in files
        assert "fixture.py" not in files

    def test_tests_dir_skipped_when_nested(self, tmp_path):
        _touch(tmp_path / "pkg" / "__init__.py")
        _touch(tmp_path / "pkg" / "module.py")
        _touch(tmp_path / "pkg" / "tests" / "fixture.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "module.py" in files
        assert "fixture.py" not in files


class TestHiddenFiltering:
    def test_hidden_dirs_skipped(self, tmp_path):
        _touch(tmp_path / "ok.py")
        _touch(tmp_path / ".hidden" / "skipped.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "ok.py" in files
        assert "skipped.py" not in files

    def test_hidden_files_skipped(self, tmp_path):
        _touch(tmp_path / "ok.py")
        _touch(tmp_path / ".secret.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "ok.py" in files
        assert ".secret.py" not in files


class TestNonPyFiltering:
    def test_non_py_files_skipped(self, tmp_path):
        _touch(tmp_path / "real.py")
        _touch(tmp_path / "README.md")
        _touch(tmp_path / "config.yaml")
        _touch(tmp_path / "script.sh")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert files == ["real.py"]


class TestGitignore:
    def test_simple_pattern_skipped(self, tmp_path):
        _touch(tmp_path / ".gitignore", "secret_dir/\n")
        _touch(tmp_path / "real.py")
        _touch(tmp_path / "secret_dir" / "hidden.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "real.py" in files
        assert "hidden.py" not in files

    def test_glob_pattern_skipped(self, tmp_path):
        _touch(tmp_path / ".gitignore", "generated_*.py\n")
        _touch(tmp_path / "real.py")
        _touch(tmp_path / "generated_a.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "real.py" in files
        assert "generated_a.py" not in files

    def test_comment_lines_ignored(self, tmp_path):
        _touch(tmp_path / ".gitignore", "# a comment\n\nskipme.py\n")
        _touch(tmp_path / "real.py")
        _touch(tmp_path / "skipme.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert "real.py" in files
        assert "skipme.py" not in files

    def test_missing_gitignore_is_fine(self, tmp_path):
        _touch(tmp_path / "real.py")
        files = [f.name for f in walk_python_files(tmp_path)]
        assert files == ["real.py"]


class TestDeterminism:
    def test_output_is_sorted(self, tmp_path):
        _touch(tmp_path / "z.py")
        _touch(tmp_path / "a.py")
        _touch(tmp_path / "m" / "__init__.py")
        _touch(tmp_path / "m" / "b.py")
        files = [f.relative_to(tmp_path) for f in walk_python_files(tmp_path)]
        assert files == sorted(files)
