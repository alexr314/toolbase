"""Where a ``toolbase serve`` session's trace goes, and whether there is one.

Precedence, highest first: ``serve --trace [DIR]``; ``TOOLBASE_TRACE``
(1/true/yes/on or 0/false/no/off); ``trace:`` in serve.yaml (project over
user). The directory is ``--trace DIR``, else ``TOOLBASE_TRACE_DIR``, else
``<project>/.toolbase/traces/<session-id>/``.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Mapping, Optional, Tuple

_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off", ""}


def traces_dir(project_root: Path) -> Path:
    """``<project>/.toolbase/traces`` (``<default-project>/traces``)."""
    from ..envs.paths import project_serve_config_path
    return project_serve_config_path(project_root).parent / "traces"


def new_session_id() -> str:
    """Sortable: ``20261115T221803Z-3fa9c2``."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)


def env_switch(environ: Mapping[str, str]) -> Optional[bool]:
    raw = environ.get("TOOLBASE_TRACE")
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in _ON:
        return True
    if v in _OFF:
        return False
    return None


def resolve(
    project_root: Path,
    cli_value: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Tuple[Optional[Path], str]:
    """``(trace_dir or None, reason)``. ``cli_value`` is ``None`` when
    ``--trace`` was not given and ``""`` when given without a directory."""
    env = os.environ if environ is None else environ
    if cli_value is not None:
        on, why = True, "--trace"
    else:
        sw = env_switch(env)
        if sw is not None:
            on, why = sw, "TOOLBASE_TRACE"
        else:
            on, why = bool(yaml_setting(project_root)), "serve.yaml trace"
    if not on:
        return None, why
    if cli_value:
        return Path(cli_value).expanduser().absolute(), why
    if env.get("TOOLBASE_TRACE_DIR"):
        return Path(env["TOOLBASE_TRACE_DIR"]).expanduser().absolute(), why
    return traces_dir(project_root) / new_session_id(), why


def yaml_setting(project_root: Path) -> Optional[bool]:
    from ..serve.config import ServeConfigError
    from ..serve.loadouts import load_merged_serve_config
    try:
        return load_merged_serve_config(project_root).trace
    except ServeConfigError:
        return None
