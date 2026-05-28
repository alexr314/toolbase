"""
Conditional bundle availability for ``toolbase serve``.

A toolkit may declare a ``bundles:`` block in its ``toolkit.yaml``:

    bundles:
      pdg: {}
      mg5: {requires: [mg5_path]}
      feynrules: {requires: [wolframscript_path, feynrules_path]}

Each bundle can declare ``requires: [<config_key>, ...]`` referencing
keys in the same toolkit's ``config:`` block. At serve startup, after
the two-layer (user -> project) toolkit config has been resolved, each
bundle is evaluated:

- If every key in its ``requires:`` list is set to a non-empty,
  non-``<NEEDS VALUE>`` value in the resolved config, the bundle is
  *available*.
- Otherwise, the bundle is *unavailable* and tools whose ``bundle:``
  field names it are silently dropped from the served tool list.

Tools without a ``bundle:`` field are always served -- the gating only
applies to tools explicitly opted into a bundle. Toolkits without a
``bundles:`` block keep working exactly as before (no gating).

The validation rule that every ``requires:`` key is declared in the
toolkit's ``config:`` block is enforced at publish time
(``validation.ToolkitMetadata._validate_bundles``), not here --
this module trusts that what it sees has already been validated.

This module is pure (no I/O); the orchestrator wires it into
``_launch_one`` after ``_resolve_state_config``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


# Sentinel string used by the setup system to mark a required field the
# user hasn't filled in yet. Treated as "unset" for bundle
# availability evaluation: a config key whose stored value is the
# sentinel does NOT satisfy a ``requires:`` clause referencing it.
NEEDS_VALUE_SENTINEL = "<NEEDS VALUE>"


@dataclass
class BundleAvailability:
    """The outcome of evaluating one toolkit's ``bundles:`` block.

    - ``available_bundles``: bundles whose ``requires:`` keys are all
      satisfied in the resolved config. Tools that name one of these
      via their ``bundle:`` field are served.
    - ``dropped_bundles``: maps bundle name -> list of missing config
      keys. Tools that name one of these are dropped from the served
      list. One stderr log line per entry is emitted at serve startup.
    - ``has_bundles_block``: True if the toolkit declared any
      ``bundles:`` entries. False means "no gating active for this
      toolkit" -- tools with ``bundle:`` fields are still served, but
      no logging fires.
    """

    available_bundles: List[str] = field(default_factory=list)
    dropped_bundles: Dict[str, List[str]] = field(default_factory=dict)
    has_bundles_block: bool = False

    def is_bundle_available(self, bundle: Optional[str]) -> bool:
        """True iff the tool's ``bundle:`` field permits serving it.

        - ``bundle is None`` -> always available (no gating).
        - No ``bundles:`` block declared -> always available
          (backward compat).
        - Otherwise: the bundle must be in ``available_bundles``.
        """
        if bundle is None:
            return True
        if not self.has_bundles_block:
            return True
        return bundle in self.available_bundles


def _is_config_key_set(resolved_config: Mapping[str, Any], key: str) -> bool:
    """Return True iff ``key`` carries a usable value in the resolved config.

    A key is considered set when its value is:
      - present in the mapping,
      - not None,
      - not the ``<NEEDS VALUE>`` sentinel,
      - not an empty string after stripping whitespace,
      - not an empty list / tuple / dict.

    Anything else (a real path string, a number, a boolean -- including
    ``False``) counts as set. The intent is "the user provided a value
    for this key"; falsy-but-meaningful values (False, 0) qualify.
    """
    if key not in resolved_config:
        return False
    value = resolved_config[key]
    if value is None:
        return False
    if value == NEEDS_VALUE_SENTINEL:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple, dict)) and not value:
        return False
    return True


def evaluate_bundles(
    bundles_block: Optional[Mapping[str, Mapping[str, Any]]],
    resolved_config: Mapping[str, Any],
) -> BundleAvailability:
    """Evaluate which bundles are available given the resolved config.

    Args:
        bundles_block: the ``bundles:`` mapping from ``toolkit.yaml``.
            ``None`` or empty means no gating; the returned
            ``BundleAvailability`` has ``has_bundles_block == False``
            and the orchestrator will not drop any tools.
        resolved_config: the two-layer-merged toolkit config (output of
            ``envs.config.resolve_toolkit_config``). Keys whose values
            are unset / ``<NEEDS VALUE>`` / empty don't satisfy a
            ``requires:`` clause.

    Returns:
        ``BundleAvailability``. ``available_bundles`` lists every bundle
        whose required keys are all set; ``dropped_bundles`` maps each
        unavailable bundle's name -> the list of missing keys that
        caused the drop. Bundles with no ``requires:`` clause (or an
        empty list) are always available.
    """
    out = BundleAvailability()
    if not bundles_block:
        return out

    out.has_bundles_block = True

    for bundle_name, bundle_entry in bundles_block.items():
        if not isinstance(bundle_entry, Mapping):
            # Defensive: validation should have rejected this; treat
            # as available so a bad shape doesn't silently drop tools.
            out.available_bundles.append(bundle_name)
            continue
        requires = bundle_entry.get("requires") or []
        if not isinstance(requires, list):
            out.available_bundles.append(bundle_name)
            continue

        missing = [
            key for key in requires
            if not _is_config_key_set(resolved_config, key)
        ]
        if missing:
            out.dropped_bundles[bundle_name] = missing
        else:
            out.available_bundles.append(bundle_name)

    return out


def format_skip_log_line(toolkit_name: str, bundle_name: str, missing_keys: List[str]) -> str:
    """Compose the one-line greppable stderr message for a dropped bundle.

    Format (locked, tests assert on it):

        [toolbase.serve] bundle_skipped toolkit=<tk> name=<bundle> \\
            reason=missing_config keys=<csv>

    Designed so a user wondering "where are my MadGraph tools?" can
    ``grep bundle_skipped ~/.toolbase/logs/serve.log`` (or read the
    stderr stream Claude Code captures) and see the reason without
    digging.
    """
    keys_csv = ",".join(missing_keys)
    return (
        f"[toolbase.serve] bundle_skipped "
        f"toolkit={toolkit_name} name={bundle_name} "
        f"reason=missing_config keys={keys_csv}"
    )


__all__ = [
    "BundleAvailability",
    "NEEDS_VALUE_SENTINEL",
    "evaluate_bundles",
    "format_skip_log_line",
]
