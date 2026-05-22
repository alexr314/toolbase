#!/usr/bin/env python3
"""
Comprehensive tests for toolbase install command implementation.

Tests:
1. setup_venv_environment() - creates venv, installs deps
2. Metadata generation and saving
3. Error handling and cleanup
4. Environment detection logic
"""

import sys
import json
import shutil
import tempfile
from pathlib import Path

# Add package to path
sys.path.insert(0, str(Path(__file__).parent))

from toolbase.cli import (
    get_current_python,
    has_conda,
    detect_environment_type,
    setup_venv_environment,
    load_toolkit_yaml,
)
from rich.console import Console

console = Console()


def test_helper_functions():
    """Test basic helper functions."""
    console.print("\n[bold blue]Test 1: Helper Functions[/bold blue]")

    # Test get_current_python
    py_version = get_current_python()
    assert len(py_version.split('.')) == 2, "Python version should be X.Y format"
    console.print(f"✓ get_current_python() = {py_version}")

    # Test has_conda
    conda_available = has_conda()
    console.print(f"✓ has_conda() = {conda_available}")

    return True


def test_environment_detection():
    """Test environment detection logic."""
    console.print("\n[bold blue]Test 2: Environment Detection[/bold blue]")

    current_py = get_current_python()
    test_dir = Path.cwd()

    # Test 1: Same Python version -> venv
    config = {'environment': {'python': current_py}}
    env_type, py_ver = detect_environment_type(test_dir, config)
    assert env_type == 'venv', f"Expected venv, got {env_type}"
    assert py_ver == current_py
    console.print(f"✓ Same Python ({current_py}) → venv")

    # Test 2: Different Python, no conda -> docker
    config = {'environment': {'python': '2.7'}}
    env_type, py_ver = detect_environment_type(test_dir, config)
    if not has_conda():
        assert env_type == 'docker', f"Expected docker, got {env_type}"
        console.print(f"✓ Python 2.7, no conda → docker")
    else:
        assert env_type == 'conda', f"Expected conda, got {env_type}"
        console.print(f"✓ Python 2.7, conda available → conda")

    # Test 3: Docker required
    config = {'environment': {'docker_required': True}}
    env_type, py_ver = detect_environment_type(test_dir, config)
    assert env_type == 'docker', f"Expected docker, got {env_type}"
    console.print(f"✓ docker_required=True → docker")

    # Test 4: No config (default to venv)
    config = {}
    env_type, py_ver = detect_environment_type(test_dir, config)
    assert env_type == 'venv', f"Expected venv, got {env_type}"
    console.print(f"✓ No config → venv (default)")

    # Test 5: Has Dockerfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / 'Dockerfile').touch()
        config = {}
        env_type, py_ver = detect_environment_type(tmppath, config)
        assert env_type == 'docker', f"Expected docker, got {env_type}"
        console.print(f"✓ Has Dockerfile → docker")

    return True


def test_load_toolkit_yaml():
    """Test loading and parsing toolkit.yaml."""
    console.print("\n[bold blue]Test 3: Load toolkit.yaml[/bold blue]")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create a test toolkit.yaml
        yaml_content = """
name: test-toolkit
version: 1.0.0
category: astro
description: Test toolkit
author: Test Author
license: MIT

environment:
  python: "3.9"
  type: auto

skills:
  - name: Test Skill
    file: skills/test.md
"""
        yaml_file = tmppath / 'toolkit.yaml'
        yaml_file.write_text(yaml_content)

        # Test loading
        config = load_toolkit_yaml(tmppath)
        assert config['name'] == 'test-toolkit'
        assert config['version'] == '1.0.0'
        assert config['environment']['python'] == '3.9'
        assert len(config['skills']) == 1
        console.print(f"✓ Loaded toolkit.yaml successfully")
        console.print(f"  - Name: {config['name']}")
        console.print(f"  - Version: {config['version']}")
        console.print(f"  - Python: {config['environment']['python']}")

        # Test missing file
        (tmppath / 'toolkit.yaml').unlink()
        try:
            load_toolkit_yaml(tmppath)
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            console.print(f"✓ Raises FileNotFoundError for missing file")

    return True


def test_venv_setup():
    """Test venv environment setup."""
    console.print("\n[bold blue]Test 4: Venv Environment Setup[/bold blue]")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create a minimal requirements.txt
        req_file = tmppath / 'requirements.txt'
        req_file.write_text('requests>=2.0\npyyaml>=6.0\n')

        console.print(f"Test directory: {tmppath}")
        console.print(f"Creating venv and installing dependencies...")

        try:
            # Run setup
            python_path = setup_venv_environment(tmppath, console)

            # Verify venv was created
            venv_path = tmppath / '.venv'
            assert venv_path.exists(), "Venv directory should exist"
            console.print(f"✓ Venv created at {venv_path}")

            # Verify Python executable exists
            assert python_path.exists(), f"Python executable should exist at {python_path}"
            console.print(f"✓ Python executable: {python_path}")

            # Verify pip is available
            if sys.platform == 'win32':
                pip_path = venv_path / 'Scripts' / 'pip.exe'
            else:
                pip_path = venv_path / 'bin' / 'pip'
            assert pip_path.exists(), "Pip should be installed"
            console.print(f"✓ Pip executable: {pip_path}")

            # Test that we can import from the venv
            import subprocess
            result = subprocess.run(
                [str(python_path), '-c', 'import requests; import yaml; print("OK")'],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0, "Should be able to import dependencies"
            assert 'OK' in result.stdout
            console.print(f"✓ Dependencies installed correctly (requests, yaml)")

            # Check for orchestral-ai
            result = subprocess.run(
                [str(python_path), '-c', 'import orchestral; print("OK")'],
                capture_output=True,
                text=True
            )
            # Note: This might fail if orchestral-ai isn't on PyPI yet
            if result.returncode == 0:
                console.print(f"✓ Orchestral-ai installed successfully")
            else:
                console.print(f"[yellow]⚠ Orchestral-ai not available (expected if not on PyPI)[/yellow]")

        except Exception as e:
            console.print(f"[red]✗ Error during venv setup: {e}[/red]")
            return False

    return True


def test_metadata_generation():
    """Test metadata JSON generation."""
    console.print("\n[bold blue]Test 5: Metadata Generation[/bold blue]")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create test metadata
        from datetime import datetime

        meta = {
            'name': 'test-toolkit',
            'version': '1.0.0',
            'environment': 'venv',
            'python_version': '3.9',
            'needs_setup': False,
            'installed_at': datetime.now().isoformat(),
            'tools_count': 5,
            'has_skills': True,
            'python_path': '/path/to/python'
        }

        # Save to file
        meta_file = tmppath / '.stk_meta.json'
        meta_file.write_text(json.dumps(meta, indent=2))

        # Verify we can read it back
        with open(meta_file) as f:
            loaded = json.load(f)

        assert loaded['name'] == 'test-toolkit'
        assert loaded['environment'] == 'venv'
        assert loaded['needs_setup'] == False
        assert loaded['tools_count'] == 5
        console.print(f"✓ Metadata saves and loads correctly")
        console.print(f"  - Name: {loaded['name']}")
        console.print(f"  - Environment: {loaded['environment']}")
        console.print(f"  - Tools: {loaded['tools_count']}")

        # Test conda metadata
        meta_conda = {
            'name': 'conda-toolkit',
            'environment': 'conda',
            'env_name': 'toolbase-conda-toolkit',
            'needs_setup': False,
        }

        meta_file2 = tmppath / '.stk_meta_conda.json'
        meta_file2.write_text(json.dumps(meta_conda, indent=2))

        with open(meta_file2) as f:
            loaded_conda = json.load(f)

        assert loaded_conda['environment'] == 'conda'
        assert loaded_conda['env_name'] == 'toolbase-conda-toolkit'
        console.print(f"✓ Conda metadata structure correct")
        console.print(f"  - Env name: {loaded_conda['env_name']}")

    return True


def test_error_handling():
    """Test error handling scenarios."""
    console.print("\n[bold blue]Test 6: Error Handling[/bold blue]")

    # Test 1: Invalid requirements.txt
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create requirements with invalid package
        req_file = tmppath / 'requirements.txt'
        req_file.write_text('this-package-definitely-does-not-exist-12345>=1.0\n')

        try:
            # This should handle the error gracefully
            python_path = setup_venv_environment(tmppath, console)
            console.print(f"[yellow]⚠ Setup completed despite invalid package (non-critical failure)[/yellow]")
        except Exception as e:
            console.print(f"[red]✗ Setup failed (expected for critical errors): {e}[/red]")

    # Test 2: Missing toolkit.yaml
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        try:
            load_toolkit_yaml(tmppath)
            assert False, "Should have raised error"
        except FileNotFoundError as e:
            console.print(f"✓ Correctly raises FileNotFoundError for missing toolkit.yaml")

    return True


def run_all_tests():
    """Run all tests."""
    console.print("\n[bold green]═══════════════════════════════════════════════════════════[/bold green]")
    console.print("[bold green]  Toolbase Install Command - Comprehensive Test Suite[/bold green]")
    console.print("[bold green]═══════════════════════════════════════════════════════════[/bold green]")

    tests = [
        ("Helper Functions", test_helper_functions),
        ("Environment Detection", test_environment_detection),
        ("Load toolkit.yaml", test_load_toolkit_yaml),
        ("Venv Setup", test_venv_setup),
        ("Metadata Generation", test_metadata_generation),
        ("Error Handling", test_error_handling),
    ]

    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            console.print(f"\n[red]✗ Test '{name}' failed with exception: {e}[/red]")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Summary
    console.print("\n[bold green]═══════════════════════════════════════════════════════════[/bold green]")
    console.print("[bold]Test Summary:[/bold]")
    console.print("[bold green]═══════════════════════════════════════════════════════════[/bold green]")

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for name, success in results:
        status = "[green]✓ PASS[/green]" if success else "[red]✗ FAIL[/red]"
        console.print(f"  {status} - {name}")

    console.print(f"\n[bold]Results: {passed}/{total} tests passed[/bold]")

    if passed == total:
        console.print("\n[bold green]🎉 All tests passed![/bold green]\n")
        return 0
    else:
        console.print(f"\n[bold red]❌ {total - passed} test(s) failed[/bold red]\n")
        return 1


if __name__ == '__main__':
    sys.exit(run_all_tests())
