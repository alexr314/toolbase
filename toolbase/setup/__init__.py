"""
Toolkit setup system (Phase 3C).

Public surface used by the rest of the CLI:

- ``ConfigField``, ``ConfigSchema`` — Pydantic models for the
  ``config:`` block in ``toolkit.yaml`` (Tier 1 declarative).
- ``parse_config_block(raw)`` — turn a raw list-of-dicts into a
  ``ConfigSchema`` with per-field validation.
- ``load_config(name)`` / ``save_config(name, data)`` /
  ``config_path(name)`` — file-canonical YAML storage at
  ``~/.toolbase/config/<toolkit>.yaml``. Round-trips comments.
- ``run_install_setup(name, schema, mode)`` — Tier 1 prompt runner
  invoked from ``toolbase install``.
- ``load_state_config(name, schema)`` — invoked at serve startup.
  Returns ``(state_config_dict, missing_required_list)``.
- ``NEEDS_VALUE_SENTINEL`` — the literal string written into the YAML
  for required fields the user hasn't filled yet. Detected at serve
  startup the same as a missing field.

Tier 2 (``setup.py`` runner, ``SetupContext``, downloads) is being
filled in across Phase 3C-2:

- Day 1 (here): ``runner.py`` + ``context.py`` + RPC scaffolding +
  log routing (``ctx.info`` / ``warn`` / ``error`` / ``hint`` /
  ``success``). ``run_setup_script`` and ``validate_setup_script``
  are the public entry points.
- Day 2: prompt RPCs + ``ctx.set_config`` write-through.
- Day 3-4: ``downloads.py`` (resumable + SHA256 + auto-extract).
- Day 5: ``--check`` validate cache, ``--reset`` flow.

See ``tb-package/docs/SETUP_SYSTEM_SPEC.md``.
"""

from __future__ import annotations

from .schema import (
    ConfigField,
    ConfigSchema,
    ConfigError,
    parse_config_block,
    coerce_value,
    NEEDS_VALUE_SENTINEL,
)
from .storage import (
    config_path,
    config_dir,
    load_config,
    save_config,
    delete_config,
    set_config_value,
    unset_config_value,
)
from .declarative import (
    run_install_setup,
    load_state_config,
)
from .runner import (
    SetupResult,
    run_setup_script,
    validate_setup_script,
    validate_setup_script_cached,
)
from .context import SetupContext

__all__ = [
    # Schema
    "ConfigField",
    "ConfigSchema",
    "ConfigError",
    "parse_config_block",
    "coerce_value",
    "NEEDS_VALUE_SENTINEL",
    # Storage
    "config_path",
    "config_dir",
    "load_config",
    "save_config",
    "delete_config",
    "set_config_value",
    "unset_config_value",
    # Declarative tier (3C-1)
    "run_install_setup",
    "load_state_config",
    # Tier-2 setup.py runner (3C-2, in flight)
    "SetupContext",
    "SetupResult",
    "run_setup_script",
    "validate_setup_script",
    "validate_setup_script_cached",
]
