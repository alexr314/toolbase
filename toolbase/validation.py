"""
Toolkit validation using Pydantic schemas.

Defines the schema for toolkit.yaml and provides validation functions
to ensure toolkits meet the required structure and format.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator, EmailStr
import yaml

from .config import _api_url


# Hardcoded fallback for the category whitelist when the registry is
# unreachable. Must stay in sync with tb-website/lib/categories.ts
# (FALLBACK_CATEGORIES). The registry is the source of truth at runtime —
# this list is only used when the network is down or the endpoint is
# pre-deployment.
FALLBACK_CATEGORIES = [
    'astro',
    'hep',
    'quantum',
    'neutrino',
    'bio',
    'chem',
    'materials',
    'utils',
    'other',
]


_categories_cache: Optional[List[str]] = None


def get_allowed_categories() -> List[str]:
    """Return the canonical category id whitelist.

    Tries ``GET {API}/api/categories`` first; falls back to
    ``FALLBACK_CATEGORIES`` on any error (network, non-200, malformed JSON,
    timeout). Cached for the duration of a single CLI invocation to avoid
    re-fetching across multiple validations in the same run.

    Why fall back instead of failing: ``toolbase validate`` runs
    pre-commit and in offline CI; the registry being down must not break
    those flows. The backend re-validates on upload anyway.
    """
    global _categories_cache
    if _categories_cache is not None:
        return _categories_cache

    api_url = _api_url()
    try:
        # Lazy import so plain Pydantic validation (used as a library) doesn't
        # pull in requests just to read a yaml file.
        import requests
        resp = requests.get(f"{api_url}/api/categories", timeout=5)
        if resp.status_code != 200:
            raise RuntimeError(f"status {resp.status_code}")
        data = resp.json()
        # Accept either {"categories": [...]} or a bare list.
        raw = data.get("categories") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise RuntimeError("unexpected response shape")
        ids: List[str] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.append(item["id"])
            elif isinstance(item, str):
                ids.append(item)
        if not ids:
            raise RuntimeError("empty category list")
        _categories_cache = ids
        return ids
    except Exception:
        print(
            "[toolbase] could not reach registry to verify category; "
            "using built-in list",
            file=sys.stderr,
        )
        _categories_cache = list(FALLBACK_CATEGORIES)
        return _categories_cache


def _check_skill_frontmatter(skill_path: Path) -> Optional[str]:
    """Return a human-readable warning if a skill's frontmatter is missing
    or incomplete; ``None`` if it's fine.

    Claude Code's skill discovery expects YAML frontmatter at the top of
    each ``SKILL.md`` with at least ``name`` and ``description``. We
    warn rather than error here because (a) older toolkits predate this
    requirement and (b) the install-time surfacer synthesizes frontmatter
    when missing, so the toolkit still works.
    """
    # Lazy import; we don't want validation.py to depend on the skills
    # module at import time (creates a circular if skills.py ever
    # imports validation in the future).
    try:
        from .skills import parse_frontmatter
    except Exception:
        return None
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, _body = parse_frontmatter(text)
    docs_url = "https://toolbase-ai.com/docs/authoring#skills"
    if fm is None:
        return (
            f"skills/{skill_path.name}: missing YAML frontmatter. "
            f"Add a `---`-delimited block at the top with `name:` and "
            f"`description:` fields. See {docs_url} for the format."
        )
    if not fm.is_complete():
        missing = [
            k for k in ("name", "description")
            if not getattr(fm, k)
        ]
        return (
            f"skills/{skill_path.name}: frontmatter missing required "
            f"field{'s' if len(missing) != 1 else ''}: "
            f"{', '.join(missing)}. See {docs_url} for the format."
        )
    return None


class ToolDefinition(BaseModel):
    """Definition of a single tool in the toolkit.

    Two mutually-exclusive forms (locked 2026-05-07; see
    docs/INGEST_DESIGN.md and the ingest sketch sign-off):

    - **Implicit form** (default for ``toolbase init``): ``function``
      is a dotted path INTO the toolkit's ``tools/`` package
      (e.g. ``tools.my_tool.my_tool``). Tools are discovered through
      the ``tools/__init__.py`` package import. ``description`` is
      required.

    - **Explicit form** (emitted by ``toolbase ingest``): ``module``
      is an arbitrary dotted import path resolved against the toolkit
      root (e.g. ``heptapod.scattering.amplitudes``). The host imports
      the module, looks up the named attribute, and registers it.
      ``description`` is optional and falls back to the function or
      class docstring at serve time.

    Validation enforces ``function`` xor ``module`` — exactly one. Both
    forms can coexist within the same yaml in principle; in practice
    each toolkit picks one and stays consistent.
    """
    name: str = Field(..., description="Tool name (alphanumeric and underscores only)")
    function: Optional[str] = Field(
        default=None,
        description=(
            "Implicit form: dotted path into the toolkit's tools/ "
            "package (e.g. 'tools.my_tool')."
        ),
    )
    module: Optional[str] = Field(
        default=None,
        description=(
            "Explicit form: dotted import path resolved against the "
            "toolkit root (e.g. 'heptapod.scattering.amplitudes'). "
            "Mutually exclusive with 'function'. Emitted by "
            "toolbase ingest."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Brief description of the tool. Required for the implicit "
            "form. Optional for the explicit form (falls back to "
            "docstring at serve time)."
        ),
    )
    bundle: Optional[str] = Field(
        default=None,
        description=(
            "Optional bundle name. When the toolkit declares a "
            "``bundles:`` block (0.5.1+), this tool participates in "
            "the named bundle's conditional-availability evaluation. A "
            "tool whose bundle's ``requires:`` config keys are not all "
            "set is silently dropped from serve's tool list at startup. "
            "Tools without a ``bundle:`` field are always served, "
            "regardless of any ``bundles:`` declarations."
        ),
    )

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate tool name format."""
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Tool name must be alphanumeric (underscores and hyphens allowed)')
        return v

    @field_validator('bundle')
    @classmethod
    def validate_bundle(cls, v):
        """Validate bundle identifier format (same shape as tool names)."""
        if v is None:
            return v
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError(
                "Bundle name must be alphanumeric (underscores and "
                "hyphens allowed)"
            )
        return v

    @model_validator(mode="after")
    def _exactly_one_form(self):
        """Enforce ``function`` xor ``module`` and per-form description rules."""
        has_function = self.function is not None and self.function != ""
        has_module = self.module is not None and self.module != ""
        if has_function and has_module:
            raise ValueError(
                f"tool '{self.name}' declares both 'function' and 'module'; "
                "pick one (function for implicit form, module for explicit form)"
            )
        if not has_function and not has_module:
            raise ValueError(
                f"tool '{self.name}' needs either 'function' (implicit form) "
                "or 'module' (explicit form)"
            )
        if has_function and not self.description:
            raise ValueError(
                f"tool '{self.name}' uses the implicit form ('function'); "
                "'description' is required"
            )
        return self


class ToolkitMetadata(BaseModel):
    """Schema for toolkit.yaml metadata file."""
    name: str = Field(..., description="Toolkit name")
    version: str = Field(..., description="Version (semantic versioning recommended)")
    description: str = Field(..., description="Brief description of the toolkit")
    author: str = Field(..., description="Author name")
    email: Optional[EmailStr] = Field(None, description="Author email")
    license: Optional[str] = Field("MIT", description="License type")
    homepage: Optional[str] = Field(None, description="Homepage or repository URL")
    category: Optional[str] = Field(None, description="Category (astro, hep, quantum, etc.)")
    keywords: Optional[List[str]] = Field(default_factory=list, description="Keywords for search")
    python_version: Optional[str] = Field("3.11", description="Required Python version")
    expected_toolkits: Optional[List[str]] = Field(
        default_factory=list,
        description=(
            "Other toolkits this one is designed to work alongside. "
            "Surfaced on install (with offer to install them too) and "
            "rendered on the website's detail page. No runtime coupling — "
            "each runs as its own serve subprocess; the agent picks which to call."
        ),
    )
    # ── Phase 3C: setup system ────────────────────────────────────────
    #
    # ``config:`` is the Tier-1 declarative block. List of mappings, one
    # per state field a tool wants injected. We don't parse it into
    # ``ConfigSchema`` here because doing so would create an import cycle
    # (validation → setup → validation). Instead we keep it as a raw
    # list and run ``setup.parse_config_block()`` against it from the
    # validate function below.
    config: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description=(
            "Tier-1 declarative config schema for this toolkit. List of "
            "field definitions (name, type, required, etc.) describing "
            "the values that need to be filled into "
            "~/.toolbase/config/<name>.yaml before the toolkit can serve. "
            "See docs/SETUP_SYSTEM_SPEC.md."
        ),
    )
    setup_script: Optional[bool] = Field(
        default=False,
        description=(
            "Set to true if the toolkit ships a setup.py at its root "
            "(Tier-2). Currently a forward-compat marker; Tier-2 ships "
            "in Phase 3C-2."
        ),
    )
    # ── 0.5.1: optional conditional bundle availability ───────────────
    #
    # Each entry maps a bundle name → a mapping that currently supports
    # one key: ``requires: [<config_key>, ...]``. A bundle whose
    # ``requires:`` config keys are not all set in the resolved two-layer
    # toolkit config is silently dropped at serve startup, and tools
    # belonging to it via their ``bundle:`` field are removed from the
    # exposed tool list. Tools without a ``bundle:`` field are always
    # served. Toolkits without a ``bundles:`` block keep working
    # exactly as in 0.5.0 (no gating, all bundles loaded).
    bundles: Optional[Dict[str, Dict[str, Any]]] = Field(
        default=None,
        description=(
            "Optional named bundle declarations. Each bundle may "
            "declare ``requires: [<config_key>, ...]`` referencing keys "
            "in the toolkit's own ``config:`` block. At serve startup, "
            "bundles whose required keys are not set in the resolved "
            "two-layer toolkit config are silently dropped; tools whose "
            "``bundle:`` field names a dropped bundle are removed from "
            "the served set."
        ),
    )
    tools: List[ToolDefinition] = Field(..., description="List of tools in this toolkit")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate toolkit name format."""
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Toolkit name must be alphanumeric (underscores and hyphens allowed)')
        if len(v) < 3:
            raise ValueError('Toolkit name must be at least 3 characters')
        return v.lower()

    @field_validator('version')
    @classmethod
    def validate_version(cls, v):
        """Validate version format (semantic versioning)."""
        parts = v.split('.')
        if len(parts) < 2:
            raise ValueError('Version should be in format: major.minor or major.minor.patch')
        return v

    @field_validator('expected_toolkits')
    @classmethod
    def validate_expected_toolkits(cls, v):
        """Each entry must be a valid toolkit name (the registry will
        verify existence at upload time; we just check shape here)."""
        if not v:
            return v
        for entry in v:
            if not isinstance(entry, str):
                raise ValueError(
                    f"expected_toolkits entries must be strings, got {type(entry).__name__}"
                )
            if not entry.replace('_', '').replace('-', '').isalnum():
                raise ValueError(
                    f"expected_toolkits entry '{entry}' must be alphanumeric "
                    "(underscores and hyphens allowed)"
                )
            if len(entry) < 3:
                raise ValueError(
                    f"expected_toolkits entry '{entry}' is too short (min 3 chars)"
                )
        return [e.lower() for e in v]

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        """Validate category is one of the allowed values."""
        if v is None:
            return v

        allowed_categories = get_allowed_categories()

        if v.lower() not in allowed_categories:
            raise ValueError(f'Category must be one of: {", ".join(allowed_categories)}')

        return v.lower()

    @model_validator(mode="after")
    def _validate_bundles(self):
        """Validate ``bundles:`` shape and cross-references.

        Checks (run at validate / publish time so authoring mistakes
        surface before users see them):

        1. Each entry under ``bundles`` is a mapping (yaml shape).
        2. The only recognized key inside a bundle entry is ``requires``;
           unknown keys are rejected so typos like ``require:`` don't
           silently no-op.
        3. ``requires:`` is a list of strings.
        4. Every key listed under ``requires:`` is also declared in this
           toolkit's ``config:`` block. Catches typos at publish, not at
           serve.
        5. Every tool's ``bundle:`` field (when set) names a bundle that
           exists in ``bundles``.
        """
        if not self.bundles:
            # Backward compat: no bundles block → nothing to check.
            # Per-tool ``bundle:`` fields without a bundles block are
            # harmless metadata (bundles have no semantic gate). If a
            # toolkit author wants those to start gating, they add the
            # bundles block at the same time.
            return self

        # Build the set of config-block keys to check requires references.
        config_keys = set()
        if self.config:
            for entry in self.config:
                if isinstance(entry, dict):
                    key = entry.get("name") or entry.get("key")
                    if isinstance(key, str):
                        config_keys.add(key)

        for bundle_name, bundle_entry in self.bundles.items():
            if not isinstance(bundle_name, str) or not bundle_name:
                raise ValueError(
                    f"bundles: bundle name must be a non-empty string, "
                    f"got {bundle_name!r}"
                )
            if not bundle_name.replace('_', '').replace('-', '').isalnum():
                raise ValueError(
                    f"bundles: bundle name '{bundle_name}' must be "
                    "alphanumeric (underscores and hyphens allowed)"
                )
            if not isinstance(bundle_entry, dict):
                raise ValueError(
                    f"bundles['{bundle_name}']: must be a mapping "
                    f"(got {type(bundle_entry).__name__})"
                )
            unknown = set(bundle_entry.keys()) - {"requires"}
            if unknown:
                raise ValueError(
                    f"bundles['{bundle_name}']: unknown key(s) "
                    f"{sorted(unknown)}. Only 'requires:' is recognized "
                    "in this version."
                )
            requires = bundle_entry.get("requires", [])
            if not isinstance(requires, list):
                raise ValueError(
                    f"bundles['{bundle_name}'].requires: must be a list "
                    f"of config-key names, got {type(requires).__name__}"
                )
            for entry in requires:
                if not isinstance(entry, str) or not entry:
                    raise ValueError(
                        f"bundles['{bundle_name}'].requires: each "
                        f"entry must be a non-empty string, got {entry!r}"
                    )
                if entry not in config_keys:
                    raise ValueError(
                        f"bundles['{bundle_name}'].requires references "
                        f"config key '{entry}' which is not declared in "
                        f"this toolkit's config: block. Declare the key "
                        "in config: or remove it from requires:."
                    )

        # Each tool's ``bundle:`` must reference a declared bundle.
        declared = set(self.bundles.keys())
        for tool in self.tools:
            if tool.bundle is not None and tool.bundle not in declared:
                raise ValueError(
                    f"tool '{tool.name}' has bundle: '{tool.bundle}' but "
                    f"that bundle is not declared in bundles. "
                    f"Declared bundles: {sorted(declared) or '(none)'}."
                )

        return self


class ValidationResult(BaseModel):
    """Result of toolkit validation."""
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Optional[ToolkitMetadata] = None


def validate_toolkit(toolkit_path: Path) -> ValidationResult:
    """
    Validate a toolkit's structure and configuration.

    Checks:
    1. toolkit.yaml exists and is valid
    2. Required files are present
    3. Tools directory exists
    4. Tool files are present
    5. requirements.txt is parseable

    Args:
        toolkit_path: Path to toolkit directory

    Returns:
        ValidationResult with validation status and details
    """
    result = ValidationResult(is_valid=True)
    toolkit_path = Path(toolkit_path)

    # Check if path exists
    if not toolkit_path.exists():
        result.is_valid = False
        result.errors.append(f"Toolkit path does not exist: {toolkit_path}")
        return result

    if not toolkit_path.is_dir():
        result.is_valid = False
        result.errors.append(f"Toolkit path is not a directory: {toolkit_path}")
        return result

    # Check for toolkit.yaml
    yaml_file = toolkit_path / "toolkit.yaml"
    if not yaml_file.exists():
        result.is_valid = False
        result.errors.append("Missing required file: toolkit.yaml")

        # Check if subdirectories contain toolkit.yaml (helpful hint)
        subdirs_with_toolkit = []
        try:
            for item in toolkit_path.iterdir():
                if item.is_dir() and (item / "toolkit.yaml").exists():
                    subdirs_with_toolkit.append(item.name)
        except PermissionError:
            pass

        if subdirs_with_toolkit:
            result.warnings.append(
                f"Found toolkit(s) in subdirectories: {', '.join(subdirs_with_toolkit)}\n"
                f"  Hint: cd into one of these directories and run 'toolbase validate' again"
            )

        return result

    # Parse and validate toolkit.yaml
    try:
        with open(yaml_file, 'r') as f:
            yaml_data = yaml.safe_load(f)

        metadata = ToolkitMetadata(**yaml_data)
        result.metadata = metadata

        # Phase 3C-1: validate the declarative `config:` block (Tier 1).
        # ToolkitMetadata holds it as a raw list-of-dicts to avoid an
        # import cycle. Parse it through setup.parse_config_block now
        # to surface authoring errors at validate / publish time rather
        # than at install time.
        if metadata.config:
            try:
                # Local import: setup → schema → pydantic chain is heavy
                # and we only need it when a config: block is present.
                from .setup import parse_config_block
                parse_config_block(metadata.config)
            except Exception as e:
                result.is_valid = False
                result.errors.append(
                    f"Invalid config: block in toolkit.yaml — {e}"
                )

        # If setup_script: true, look for the file at the toolkit root.
        # Don't fail on its absence (some authors set the flag in
        # advance of writing the script); just warn.
        if metadata.setup_script:
            setup_py = toolkit_path / "setup.py"
            if not setup_py.exists():
                result.warnings.append(
                    "setup_script: true is set but setup.py is missing "
                    "from the toolkit root. The install pipeline will "
                    "skip the Tier-2 setup runner; either drop the "
                    "flag or add a setup.py with `def setup(ctx)`."
                )

    except yaml.YAMLError as e:
        result.is_valid = False
        result.errors.append(f"Invalid YAML in toolkit.yaml: {e}")
        return result

    except Exception as e:
        result.is_valid = False
        result.errors.append(f"Invalid toolkit.yaml: {e}")
        return result

    # Check for required files
    required_files = ['README.md', 'requirements.txt']
    for filename in required_files:
        file_path = toolkit_path / filename
        if not file_path.exists():
            result.warnings.append(f"Missing recommended file: {filename}")

    # Check for tools/ directory.
    #
    # The implicit form (``function:`` field, current ``init`` template
    # default) requires ``tools/__init__.py``. The explicit form
    # (``module:`` field, emitted by ``toolbase ingest``) imports
    # arbitrary dotted paths against the toolkit root and does not need
    # ``tools/`` to exist. If ALL tools are explicit-form, skip the
    # ``tools/`` requirement; otherwise require it.
    has_implicit_tools = any(
        getattr(t, 'function', None) for t in metadata.tools
    )
    has_explicit_tools = any(
        getattr(t, 'module', None) for t in metadata.tools
    )
    tools_dir = toolkit_path / "tools"
    if has_implicit_tools:
        if not tools_dir.exists():
            result.is_valid = False
            result.errors.append("Missing required directory: tools/")
            return result
        # Check that tools directory has __init__.py
        init_file = tools_dir / "__init__.py"
        if not init_file.exists():
            result.is_valid = False
            result.errors.append("Missing required file: tools/__init__.py (Orchestral requires explicit tool exports)")
        else:
            # Check that tools/__init__.py exports tools
            try:
                content = init_file.read_text()
                if '__all__' not in content and 'import' not in content:
                    result.warnings.append(
                        "tools/__init__.py should export tools. "
                        "Add: from tools.your_tool import your_tool"
                    )
            except Exception:
                pass

    # MCP transport is managed by the Toolbase serve orchestrator — the
    # per-toolkit subprocess imports tools directly via `_toolkit_host`, no
    # toolkit-side MCP server is required. The `mcp/` files emitted by
    # `toolbase init` are scaffolding kept for forward-compat with a future
    # bring-your-own-server path; their presence is not validated here.
    # An ingested toolkit (e.g. via `toolbase ingest`) typically has no
    # `mcp/` directory at all and that is correct.
    #
    # Historical: 0.5.1 and earlier hard-errored on missing
    # `mcp/server_stdio.py` and `mcp/__init__.py`. Dropped in 0.5.2 — the
    # rule was vestigial template enforcement that broke ingested toolkits.

    # Per-tool existence checks. Branches per form (function: vs module:).
    seen_keys: set = set()
    requirements_text = ""
    requirements_file_for_check = toolkit_path / "requirements.txt"
    if requirements_file_for_check.exists():
        try:
            requirements_text = requirements_file_for_check.read_text().lower()
        except Exception:
            requirements_text = ""

    for tool in metadata.tools:
        # Duplicate check: implicit by (function, name); explicit by
        # (module, name). Cross-form ``name`` collisions are also flagged.
        key = (tool.function or tool.module, tool.name)
        if key in seen_keys:
            result.is_valid = False
            result.errors.append(
                f"Duplicate tool entry: name='{tool.name}' "
                f"({'function' if tool.function else 'module'}="
                f"'{tool.function or tool.module}')"
            )
            continue
        seen_keys.add(key)

        if tool.function:
            # Implicit form: dotted path into tools/ package; check the
            # corresponding source file exists.
            function_parts = tool.function.split('.')
            if len(function_parts) < 2:
                result.errors.append(
                    f"Invalid function path for tool '{tool.name}': {tool.function}"
                )
                result.is_valid = False
                continue
            module_path = toolkit_path / f"{function_parts[0]}.py"
            if not module_path.exists():
                module_path = toolkit_path / function_parts[0] / f"{function_parts[1]}.py"
                if not module_path.exists():
                    result.warnings.append(
                        f"Tool file not found for '{tool.name}': "
                        f"{function_parts[0]}/{function_parts[1]}.py"
                    )
        elif tool.module:
            # Explicit form: dotted import path. Verify the module
            # resolves to a file inside the toolkit root, OR the top-level
            # package is declared as a dep in requirements.txt.
            #
            # We don't use ``importlib.util.find_spec`` here — that runs
            # parent-package ``__init__.py`` code as a side effect of
            # locating the spec. Walking the filesystem ourselves is
            # cheap and side-effect-free.
            parts = tool.module.split('.')
            if not all(p.isidentifier() for p in parts):
                result.is_valid = False
                result.errors.append(
                    f"Invalid module path for tool '{tool.name}': "
                    f"'{tool.module}' is not a valid dotted identifier"
                )
                continue
            # Try resolving against the toolkit root.
            candidate_dir_init = toolkit_path
            candidate_file = toolkit_path
            resolved_inside = False
            cur = toolkit_path
            for i, part in enumerate(parts):
                pkg = cur / part
                pyfile = cur / f"{part}.py"
                if i == len(parts) - 1:
                    if pyfile.is_file() or (pkg / "__init__.py").is_file():
                        resolved_inside = True
                        break
                    # Last part not found → unresolved.
                    break
                else:
                    if (pkg / "__init__.py").is_file():
                        cur = pkg
                        continue
                    # No further package; can't resolve under root.
                    break
            if not resolved_inside:
                top_level = parts[0].lower()
                # Cheap requirements.txt presence check; matches "name",
                # "name>=...", "name==...", etc. as a substring.
                if top_level not in requirements_text:
                    result.is_valid = False
                    result.errors.append(
                        f"Tool '{tool.name}' references module "
                        f"'{tool.module}' which is not under the toolkit "
                        f"root and the top-level package '{parts[0]}' "
                        "is not declared in requirements.txt. The tarball "
                        "wouldn't include this code; declare the dep or "
                        "move the source under the toolkit root."
                    )

    # Validate requirements.txt if it exists
    requirements_file = toolkit_path / "requirements.txt"
    if requirements_file.exists():
        try:
            with open(requirements_file, 'r') as f:
                requirements = f.read()

            # Basic validation - just check it's readable
            if not requirements.strip():
                result.warnings.append("requirements.txt is empty")

            # Check for orchestral-ai dependency (required for Orchestral tools)
            if 'orchestral-ai' not in requirements.lower() and 'orchestral' not in requirements.lower():
                result.is_valid = False
                result.errors.append(
                    "requirements.txt must include 'orchestral-ai>=1.0.0' "
                    "(required for Orchestral tool framework)"
                )

        except Exception as e:
            result.warnings.append(f"Could not read requirements.txt: {e}")

    # Validate skills/ directory (optional)
    skills_dir = toolkit_path / 'skills'
    if skills_dir.exists():
        if not skills_dir.is_dir():
            result.errors.append("skills/ exists but is not a directory")
            result.is_valid = False
        else:
            # Check for markdown files. Filter out macOS AppleDouble files.
            skill_files = [
                p for p in skills_dir.glob('*.md')
                if not p.name.startswith('._')
            ]
            if not skill_files:
                result.warnings.append("skills/ directory exists but is empty (consider adding skill guides)")

            # Frontmatter check: each skill should carry name + description
            # at the top so Claude Code (when surfaced into ~/.claude/skills/)
            # can index it. Warning-only — backward compat with toolkits
            # that predate the requirement; the install-time surfacer
            # synthesizes frontmatter when missing.
            for sf in skill_files:
                fm_problem = _check_skill_frontmatter(sf)
                if fm_problem:
                    result.warnings.append(fm_problem)

            # Validate skills metadata in toolkit.yaml if present
            if metadata and hasattr(metadata, 'skills') and metadata.skills:
                for skill in metadata.skills:
                    if isinstance(skill, dict):
                        skill_file_path = skill.get('file', '')
                        skill_name = skill.get('name', 'unknown')
                    else:
                        # If skills is just a list of strings
                        skill_file_path = str(skill)
                        skill_name = skill_file_path

                    full_skill_path = toolkit_path / skill_file_path
                    if not full_skill_path.exists():
                        result.errors.append(f"Skill file referenced in toolkit.yaml not found: {skill_file_path}")
                        result.is_valid = False

    return result


def load_toolkit_metadata(toolkit_path: Path) -> Optional[ToolkitMetadata]:
    """
    Load toolkit metadata from toolkit.yaml.

    Args:
        toolkit_path: Path to toolkit directory

    Returns:
        ToolkitMetadata object or None if invalid
    """
    yaml_file = toolkit_path / "toolkit.yaml"

    if not yaml_file.exists():
        return None

    try:
        with open(yaml_file, 'r') as f:
            yaml_data = yaml.safe_load(f)

        return ToolkitMetadata(**yaml_data)

    except Exception:
        return None