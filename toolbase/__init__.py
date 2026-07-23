"""
Toolbase - The community registry and CLI for AI agent toolkits

Toolbase provides a centralized platform for discovering, sharing, and using
tools for AI agents. Think of it as an app store for agent toolkits across any
domain - from general-purpose utilities to specialized fields like astrophysics,
high-energy physics, and quantum computing.

Features:
- Easy tool creation and sharing
- Curated categories across domains
- MCP (Model Context Protocol) compatibility
- Integration with Orchestral AI and other agent frameworks
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("toolbase")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__author__ = "Alex Roman"

# The canonical tool-naming rule, public so external integrators (e.g.
# toolbench's `python:` bridge) reproduce served names without re-deriving them.
from .naming import mcp_tool_name, namespaced_tool_name, strip_tool_suffix

# Placeholder imports for future toolkit categories
# from .astro import aster
# from .hep import heptapod
# from .quantum import quantum_toolkit
