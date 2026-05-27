"""
Toolkit creation and management functions.

Handles creating new toolkits from templates, packaging, and file operations.
"""

from pathlib import Path
from typing import Optional
import shutil


def get_template_path(filename: str) -> Path:
    """
    Get the path to a template file.

    Args:
        filename: Template filename

    Returns:
        Path to template file
    """
    # Templates are in the same package
    templates_dir = Path(__file__).parent / "templates"
    return templates_dir / filename


def render_template(template_path: Path, substitutions: dict) -> str:
    """
    Render a template file with substitutions.

    Simple template rendering using {{variable}} syntax.

    Args:
        template_path: Path to template file
        substitutions: Dictionary of variable names to values

    Returns:
        Rendered template content
    """
    with open(template_path, 'r') as f:
        content = f.read()

    # Simple template substitution
    for key, value in substitutions.items():
        placeholder = f"{{{{{key}}}}}"
        content = content.replace(placeholder, str(value))

    return content


def create_toolkit_from_template(
    name: str,
    path: Path,
    with_docker: bool = False,
    with_setup: bool = False,
    author: Optional[str] = None,
    email: Optional[str] = None,
    category: str = "general",
    python_version: str = "3.11",
    registry_metadata: Optional[dict] = None
) -> Path:
    """
    Create a new toolkit from template.

    Creates the standard toolkit directory structure:
    - toolkit.yaml
    - tools/ (with __init__.py and example_tool.py)
    - requirements.txt
    - README.md
    - Dockerfile (optional)
    - setup.py + ``setup_script: true`` flag (when ``with_setup=True``)

    Args:
        name: Toolkit name
        path: Path where toolkit should be created
        with_docker: Whether to include Dockerfile
        with_setup: Whether to drop a Tier-2 ``setup.py`` template and
            flip ``setup_script: true`` in toolkit.yaml. Both must be
            present for the install-time runner to invoke setup.py;
            this flag pairs them so an author who wanted setup.py
            doesn't end up with a silently-disabled scaffold.
        author: Author name (optional)
        email: Author email (optional)
        category: Toolkit category
        python_version: Required Python version

    Returns:
        Path to created toolkit directory

    Raises:
        FileExistsError: If toolkit directory already exists
        IOError: If unable to create files
    """
    # Ensure path is absolute
    toolkit_path = Path(path).resolve()

    # Check if directory already exists
    if toolkit_path.exists():
        raise FileExistsError(f"Directory already exists: {toolkit_path}")

    # Create main directory
    toolkit_path.mkdir(parents=True, exist_ok=False)

    # Create tools subdirectory
    tools_dir = toolkit_path / "tools"
    tools_dir.mkdir()

    # Create mcp subdirectory
    mcp_dir = toolkit_path / "mcp"
    mcp_dir.mkdir()

    # Prepare template substitutions
    # Tool imports and list for MCP registry
    tool_imports = "example_tool, text_processor"
    tool_list = "example_tool, text_processor"

    substitutions = {
        'name': name,
        'author': author or 'Your Name',
        'email': email or 'your.email@example.com',
        'category': category,
        'python_version': python_version,
        'version': '0.1.0',
        'description': 'A brief description of your toolkit',
        'tool_imports': tool_imports,
        'tool_list': tool_list,
    }

    # Create toolkit.yaml
    if registry_metadata:
        # Pre-fill from registry metadata
        version = suggest_next_version(registry_metadata.get('latest_version', '0.1.0'))
        keywords_yaml = format_keywords_yaml(registry_metadata.get('keywords', []))
        homepage = registry_metadata.get('homepage') or ''

        yaml_content = f"""name: {name}
version: {version}
category: {registry_metadata.get('category', 'other')}
description: {registry_metadata.get('description', 'A toolkit for AI agents')}
author: {registry_metadata.get('author', author or 'Your Name')}
license: {registry_metadata.get('license', 'MIT')}
homepage: {homepage}
keywords:
{keywords_yaml}

tools:
  - name: example_tool
    function: tools.example_tool.example_tool
    description: An example tool that demonstrates the basic Orchestral structure
  - name: text_processor
    function: tools.example_tool.text_processor
    description: Another example tool showing text processing

# Optional: Add your skill guides in the skills/ directory
# skills:
#   - name: Getting Started
#     file: skills/getting-started.md
#     description: Learn how to use this toolkit
"""
    else:
        # Use template
        yaml_template = get_template_path("toolkit.yaml.template")
        yaml_content = render_template(yaml_template, substitutions)

    # If --with-setup was passed, the toolkit will ship a setup.py at
    # root. The Tier-2 install-time runner only invokes setup.py when
    # both the file is present AND ``setup_script: true`` is declared
    # in toolkit.yaml. Drop the flag in here to keep the two in sync;
    # an author who wants setup.py without the flag is almost always
    # making a mistake (the file would silently be ignored at install).
    if with_setup:
        yaml_content = _insert_setup_script_flag(yaml_content)

    (toolkit_path / "toolkit.yaml").write_text(yaml_content)

    # Create tools/__init__.py
    init_template = get_template_path("__init__.py.template")
    init_content = render_template(init_template, substitutions)
    (tools_dir / "__init__.py").write_text(init_content)

    # Create tools/example_tool.py
    tool_template = get_template_path("tool_example.py")
    tool_content = render_template(tool_template, substitutions)
    (tools_dir / "example_tool.py").write_text(tool_content)

    # Create README.md
    readme_template = get_template_path("README.md.template")
    readme_content = render_template(readme_template, substitutions)
    (toolkit_path / "README.md").write_text(readme_content)

    # Create requirements.txt
    req_template = get_template_path("requirements.txt.template")
    req_content = render_template(req_template, substitutions)
    (toolkit_path / "requirements.txt").write_text(req_content)

    # Create mcp/__init__.py
    mcp_init_template = get_template_path("mcp/__init__.py.template")
    mcp_init_content = render_template(mcp_init_template, substitutions)
    (mcp_dir / "__init__.py").write_text(mcp_init_content)

    # Create mcp/server_stdio.py
    # Note: This now directly uses tools/__init__.py as the registry
    server_template = get_template_path("mcp/server_stdio.py.template")
    server_content = render_template(server_template, substitutions)
    (mcp_dir / "server_stdio.py").write_text(server_content)

    # Create skills/ directory
    skills_dir = toolkit_path / "skills"
    skills_dir.mkdir(exist_ok=True)

    # Create example skill file
    example_skill_template = get_template_path("skills/example_skill.md")
    if example_skill_template.exists():
        example_skill_content = render_template(example_skill_template, substitutions)
        (skills_dir / "example_skill.md").write_text(example_skill_content)

    # Create Dockerfile if requested
    if with_docker:
        dockerfile_template = get_template_path("Dockerfile.template")
        dockerfile_content = render_template(dockerfile_template, substitutions)
        (toolkit_path / "Dockerfile").write_text(dockerfile_content)

    # Create setup.py if requested. Pairs with the ``setup_script: true``
    # flag we already inserted into toolkit.yaml above. The template is
    # heavily-commented so authors can copy-paste-modify; the body is a
    # no-op (`return True`) until they uncomment what they need.
    if with_setup:
        setup_template = get_template_path("setup.py.template")
        setup_content = render_template(setup_template, substitutions)
        (toolkit_path / "setup.py").write_text(setup_content)

    # Create .gitignore
    gitignore_content = """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
venv/
env/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Testing
.pytest_cache/
.coverage
htmlcov/

# Toolbase
.toolbase/
"""
    (toolkit_path / ".gitignore").write_text(gitignore_content)

    return toolkit_path


def package_toolkit(toolkit_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    Package a toolkit into a tar.gz file for distribution.

    Args:
        toolkit_path: Path to toolkit directory
        output_path: Where to save the tarball (default: current directory)

    Returns:
        Path to created tarball

    Raises:
        ValueError: If toolkit is invalid
        IOError: If unable to create tarball
    """
    from .validation import validate_toolkit

    # Validate toolkit first
    result = validate_toolkit(toolkit_path)
    if not result.is_valid:
        raise ValueError(f"Toolkit validation failed: {', '.join(result.errors)}")

    # Determine output path
    if output_path is None:
        output_path = Path.cwd()

    toolkit_name = result.metadata.name
    version = result.metadata.version
    tarball_name = f"{toolkit_name}-{version}.tar.gz"
    tarball_path = output_path / tarball_name

    # Create tarball
    import tarfile

    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(toolkit_path, arcname=toolkit_name)

    return tarball_path


def suggest_next_version(current_version: str) -> str:
    """
    Suggest next patch version based on current version.

    Args:
        current_version: Current version string (e.g., "0.2.3")

    Returns:
        Next patch version (e.g., "0.2.4")
    """
    # Handle None or empty version
    if not current_version:
        return "0.1.0"

    try:
        parts = current_version.split('.')
        if len(parts) == 3:
            major, minor, patch = parts
            return f"{major}.{minor}.{int(patch) + 1}"
    except (ValueError, IndexError, AttributeError):
        pass
    return "0.1.0"


def _insert_setup_script_flag(yaml_content: str) -> str:
    """Add ``setup_script: true`` to a rendered toolkit.yaml.

    The flag tells the install-time runner that the toolkit ships a
    Tier-2 ``setup.py``; without it, the install pipeline skips
    ``setup.py`` even if the file exists. Insert just before the
    ``tools:`` block so the line lands near the other toolkit-level
    metadata (name, version, category, ...) and not buried inside
    the tools list.

    Idempotent: if the flag is already declared (e.g., the registry-
    prefilled YAML happens to include it), don't double-insert.
    """
    if "setup_script:" in yaml_content:
        return yaml_content
    marker = "\ntools:"
    if marker not in yaml_content:
        # Defensive: every toolkit.yaml has a tools: block. If we
        # somehow don't find it, append at end.
        return yaml_content + "\nsetup_script: true\n"
    return yaml_content.replace(
        marker,
        "\nsetup_script: true\n" + marker,
        1,
    )


def format_keywords_yaml(keywords: list) -> str:
    """
    Format keywords list as YAML.

    Args:
        keywords: List of keyword strings

    Returns:
        YAML-formatted string
    """
    if not keywords:
        return "  - tools\n  - agents"
    return "\n".join(f"  - {kw}" for kw in keywords)
