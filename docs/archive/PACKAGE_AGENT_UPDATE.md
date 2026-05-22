# Package Agent - Orchestral Integration Update

**Date:** 2026-04-04
**From:** Project Manager
**Priority:** HIGH - Template corrections needed before continuing

---

## Critical Update: Orchestral Tool Format

We've received detailed specifications for the Orchestral tool format from the Orchestral team. The current templates need updating to match the correct format.

## What Needs to Change

### 1. **Tool Return Type (CRITICAL)**

**Current template is WRONG:**
```python
def example_tool(input_text: str) -> dict:  # ❌ Wrong return type
    """Example tool"""
    return {
        "original": input_text,
        "processed": result
    }
```

**Correct Orchestral format:**
```python
from orchestral import define_tool
import json

@define_tool
def example_tool(input_text: str) -> str:  # ✅ Must return str (JSON string)
    """
    Example tool that processes text.

    This tool converts input text to uppercase and returns
    metadata about the processing.

    Args:
        input_text: The text to process

    Returns:
        JSON string with original text, processed result, and length
    """
    try:
        result = input_text.upper()

        return json.dumps({  # ✅ Return JSON string, not dict
            "status": "ok",
            "original": input_text,
            "processed": result,
            "length": len(input_text)
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        })
```

**Key differences:**
- ✅ Add `from orchestral import define_tool` import
- ✅ Add `@define_tool` decorator
- ✅ Return type MUST be `str`, not `dict`
- ✅ Return `json.dumps(result)` instead of returning dict directly
- ✅ All responses should have `"status": "ok"` or `"status": "error"`
- ✅ Try/except with error handling returning JSON

### 2. **Required vs Optional Parameters**

Parameters WITHOUT defaults = Required in schema
Parameters WITH defaults = Optional in schema

```python
@define_tool
def my_tool(
    required_param: str,              # Required (no default)
    optional_param: int = 42,         # Optional (has default)
    optional_list: Optional[list] = None  # Optional (explicit + default)
) -> str:
    """Tool description"""
    # implementation
```

### 3. **Toolkit Directory Structure**

**Current structure is incomplete. Need to add `mcp/` directory:**

```
my-toolkit/
├── toolkit.yaml
├── tools/
│   ├── __init__.py          # CRITICAL: Must export tools!
│   └── tool_example.py
├── mcp/                     # NEW: MCP server integration
│   ├── __init__.py
│   ├── toolkit_registry.py  # Tool discovery and grouping
│   └── server_stdio.py      # STDIO MCP server
├── requirements.txt
├── README.md
└── tests/                   # Recommended
    └── test_tools.py
```

### 4. **Critical: tools/__init__.py Must Export Tools**

**Current template likely has empty `__init__.py`. This is WRONG.**

Orchestral does NOT auto-discover tools. You MUST export them explicitly:

```python
# tools/__init__.py
from tools.tool_example import example_tool

__all__ = ['example_tool']
```

Or for organized toolkits:
```python
# tools/__init__.py
from tools.photometry import aperture_photometry, psf_photometry
from tools.spectroscopy import fit_continuum
from tools.exoplanets import transit_model

__all__ = [
    'aperture_photometry',
    'psf_photometry',
    'fit_continuum',
    'transit_model',
]
```

### 5. **MCP Server Templates (NEW)**

You need to create templates for the `mcp/` directory:

#### mcp/toolkit_registry.py.template
```python
"""Tool registry for {{toolkit_name}} MCP server."""
import sys
from pathlib import Path

# Add toolkit to path
TOOLKIT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(TOOLKIT_ROOT))

def get_all_tools(base_dir: str = ".") -> list:
    """Load all toolkit tools."""
    from tools import {{tool_imports}}
    return [{{tool_list}}]

def get_tools(*group_names: str, base_dir: str = ".") -> list:
    """Get tools from specified groups."""
    if not group_names or "all" in group_names:
        return get_all_tools(base_dir)

    # For now, just return all tools
    # TODO: Implement tool grouping if needed
    return get_all_tools(base_dir)
```

#### mcp/server_stdio.py.template
```python
"""MCP STDIO server for {{toolkit_name}}."""
import argparse
from orchestral.mcp import MCPServer
from mcp.toolkit_registry import get_tools

def main():
    parser = argparse.ArgumentParser(
        description="{{toolkit_name}} MCP Server"
    )
    parser.add_argument(
        "--groups",
        help="Comma-separated tool groups (default: all)",
        default="all"
    )
    parser.add_argument(
        "--workspace",
        help="Working directory for tool operations",
        default="."
    )
    args = parser.parse_args()

    # Load tools
    groups = args.groups.split(",")
    tools = get_tools(*groups, base_dir=args.workspace)

    # Create and run MCP server
    server = MCPServer(
        tools=tools,
        name="{{toolkit_name}}",
        version="{{version}}"
    )
    server.run()

if __name__ == "__main__":
    main()
```

#### mcp/__init__.py.template
```python
"""MCP server for {{toolkit_name}}."""
from mcp.server_stdio import main

__all__ = ['main']
```

### 6. **requirements.txt Template Update**

Add Orchestral to the template:

```
orchestral-ai>=1.0.0
numpy>=1.24.0
# Add your toolkit-specific dependencies below
```

**Note:** Package name is `orchestral-ai`, NOT just `orchestral`!

### 7. **Enhanced Docstrings**

Tool docstrings should be comprehensive - they're what the LLM sees:

```python
@define_tool
def calculate_transit(
    period: float,
    radius_ratio: float,
    impact_param: float = 0.5,
    time_resolution: int = 1000
) -> str:
    """
    Calculate exoplanet transit light curve.

    This tool computes the transit light curve for an exoplanet
    crossing its host star. Useful for planning observations,
    fitting transit data, and parameter sensitivity studies.

    Args:
        period: Orbital period in days (must be positive)
        radius_ratio: Planet-to-star radius ratio R_p/R_star (0 < r < 1)
        impact_param: Impact parameter (0 = central, 1 = grazing transit)
        time_resolution: Number of time points to compute (default: 1000)

    Returns:
        JSON string with:
        - status: "ok" or "error"
        - time: Time array in hours from mid-transit
        - flux: Normalized flux (1.0 = no transit)
        - transit_depth: Maximum flux decrease

    Example:
        For Jupiter-like planet: period=3.5, radius_ratio=0.1, impact_param=0.0

    Errors:
        Returns error if period <= 0 or radius_ratio not in (0, 1)
    """
    # implementation
```

---

## Tasks for Package Agent

### **Priority 1: Update Templates (Do This First)**

1. **Update `toolbase/templates/tool_example.py`**
   - Add imports: `from orchestral import define_tool` and `import json`
   - Add `@define_tool` decorator
   - Change return type from `dict` to `str`
   - Return `json.dumps({...})` instead of dict
   - Add try/except error handling
   - Include `"status": "ok"/"error"` in all responses

2. **Update `toolbase/templates/__init__.py.template`**
   - Export the example tool:
     ```python
     from tools.tool_example import example_tool

     __all__ = ['example_tool']
     ```

3. **Create `toolbase/templates/mcp/` directory with:**
   - `toolkit_registry.py.template`
   - `server_stdio.py.template`
   - `__init__.py.template`
   - Use the templates provided above

4. **Update `toolbase/templates/requirements.txt.template`**
   - Add `orchestral-ai>=1.0.0` at the top

5. **Update `toolbase/toolkit.py`**
   - Modify `create_toolkit_from_template()` to:
     - Create `mcp/` directory
     - Generate MCP server files from templates
     - Populate tool imports in `toolkit_registry.py`

### **Priority 2: Update Validation**

Update `toolbase/validation.py` to check:

```python
def validate_toolkit_structure(toolkit_path: Path) -> ValidationResult:
    """Validate toolkit has correct structure for Orchestral."""
    errors = []
    warnings = []

    # Check required files
    required_files = [
        'toolkit.yaml',
        'tools/__init__.py',
        'requirements.txt',
        'README.md',
        'mcp/toolkit_registry.py',
        'mcp/server_stdio.py',
    ]

    for file_path in required_files:
        if not (toolkit_path / file_path).exists():
            errors.append(f"Missing required file: {file_path}")

    # Check tools/__init__.py exports tools
    tools_init = toolkit_path / 'tools' / '__init__.py'
    if tools_init.exists():
        content = tools_init.read_text()
        if '__all__' not in content and 'import' not in content:
            warnings.append(
                "tools/__init__.py should export tools. "
                "Add: from tools.your_tool import your_tool"
            )

    # Check for orchestral-ai in requirements.txt
    req_file = toolkit_path / 'requirements.txt'
    if req_file.exists():
        content = req_file.read_text()
        if 'orchestral-ai' not in content.lower():
            errors.append(
                "requirements.txt must include 'orchestral-ai>=1.0.0'"
            )

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )
```

### **Priority 3: Update `toolbase init` Command**

The `init` command should now:

1. Create full directory structure including `mcp/`
2. Generate MCP server files from templates
3. Create example tool with correct Orchestral format
4. Export tool in `tools/__init__.py`
5. Include `orchestral-ai` in `requirements.txt`

**Template variables to support:**
- `{{toolkit_name}}` - Toolkit name (e.g., "my-toolkit")
- `{{version}}` - Version (default: "0.1.0")
- `{{author}}` - Author name
- `{{description}}` - Toolkit description
- `{{tool_imports}}` - Generated tool import list for registry
- `{{tool_list}}` - Generated tool list for registry

---

## Reference Materials

See the Orchestral documentation provided in the project manager's message for:
- Complete example tools (simple and complex)
- MCP server implementation patterns
- Testing strategies
- Error handling conventions

**Key file to reference:** The HEPTAPOD toolkit at `/Users/adroman/research/agents/hep/heptapod/` is a complete working example of this structure.

---

## Testing Your Changes

After updating templates, test by:

```bash
# Create a test toolkit
toolbase init test-toolkit

# Check structure
ls -R test-toolkit/
# Should show: toolkit.yaml, tools/, mcp/, requirements.txt, README.md

# Validate it
toolbase validate test-toolkit/

# Should pass with no errors
```

Then manually verify:
1. `tools/tool_example.py` has `@define_tool` decorator and returns JSON string
2. `tools/__init__.py` exports the example tool
3. `requirements.txt` includes `orchestral-ai>=1.0.0`
4. `mcp/server_stdio.py` exists and looks correct
5. `mcp/toolkit_registry.py` imports and exposes tools

---

## Questions?

If anything is unclear about the Orchestral format, refer back to the comprehensive response from the Orchestral agent in the project manager's message.

**Do NOT proceed with `publish`, `install`, or `serve` commands yet.** Focus on getting the templates and `init`/`validate` commands correct first.

---

## Timeline

Please complete template updates within 1-2 hours if possible. Once templates are correct, we can proceed with:
- Backend toolkit endpoints
- `toolbase publish` implementation
- Container integration
- Full end-to-end testing

**Status:** Waiting for your confirmation that templates have been updated and tested.
