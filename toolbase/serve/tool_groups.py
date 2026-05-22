"""
Conditional tool-group availability for ``toolbase serve``.

A toolkit may declare a ``tool_groups:`` block in its ``toolkit.yaml``:

    tool_groups:
      pdg: {}
      mg5: {requires: [mg5_path]}
      feynrules: {requires: [wolframscript_path, feynrules_path]}

Each group can declare ``requires: [<config_key>, ...]`` referencing
keys in the same toolkit's ``config:`` block. At serve startup, after
the two-layer (user → project) toolkit config has been resolved, each
group is evaluated:

- If every key in its ``requires:`` list is set to a non-empty,
  non-``<NEEDS VALUE>`` value in the resolved config, the group is
  *available*.
- Otherwise, the group is *unavailable* and tools whose ``group:``
  field names it are silently dropped from the served tool list.

Tools without a ``group:`` field are always served — the gating only
applies to tools explicitly opted into a group. Toolkits without a
``tool_groups:`` block keep working exactly as in 0.5.0 (no gating).

The validation rule that every ``requires:`` key is declared in the
toolkit's ``config:`` block is enforced at publish time
(``validation.ToolkitMetadata._validate_tool_groups``), not here —
this module trusts that what it sees has already been validated.

This module is pure (no I/O); the orchestrator wires it into
``_launch_one`` after ``_resolve_state_config``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


# Sentinel string used by the setup system to mark a required field the
# user hasn't filled in yet. Treated as "unset" for tool-group
# availability evaluation: a config key whose stored value is the
# sentinel does NOT satisfy a ``requires:`` clause referencing it.
NEEDS_VALUE_SENTINEL = "<NEEDS VALUE>"


@dataclass
class GroupAvailability:
    """The outcome of evaluating one toolkit's ``tool_groups:`` block.

    - ``available_groups``: groups whose ``requires:`` keys are all
      satisfied in the resolved config. Tools that name one of these
      via their ``group:`` field are served.
    - ``dropped_groups``: maps group name → list of missing config
      keys. Tools that name one of these are dropped from the served
      list. One stderr log line per entry is emitted at serve startup.
    - ``has_tool_groups_block``: True if the toolkit declared any
      ``tool_groups:`` entries. False means "no gating active for this
      toolkit" — tools with ``group:`` fields are still served, but
      no logging fires.
    """

    available_groups: List[str] = field(default_factory=list)
    dropped_groups: Dict[str, List[str]] = field(default_factory=dict)
    has_tool_groups_block: bool = False

    def is_group_available(self, group: Optional[str]) -> bool:
        """True iff the tool's ``group:`` field permits serving it.

        - ``group is None`` → always available (no gating).
        - No ``tool_groups:`` block declared → always available
          (backward compat).
        - Otherwise: the group must be in ``available_groups``.
        """
        if group is None:
            return True
        if not self.has_tool_groups_block:
            return True
        return group in self.available_groups


def _is_config_key_set(resolved_config: Mapping[str, Any], key: str) -> bool:
    """Return True iff ``key`` carries a usable value in the resolved config.

    A key is considered set when its value is:
      - present in the mapping,
      - not None,
      - not the ``<NEEDS VALUE>`` sentinel,
      - not an empty string after stripping whitespace,
      - not an empty list / tuple / dict.

    Anything else (a real path string, a number, a boolean — including
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


def evaluate_tool_groups(
    tool_groups_block: Optional[Mapping[str, Mapping[str, Any]]],
    resolved_config: Mapping[str, Any],
) -> GroupAvailability:
    """Evaluate which tool groups are available given the resolved config.

    Args:
        tool_groups_block: the ``tool_groups:`` mapping from
            ``toolkit.yaml``. ``None`` or empty means no gating; the
            returned ``GroupAvailability`` has ``has_tool_groups_block
            == False`` and the orchestrator will not drop any tools.
        resolved_config: the two-layer-merged toolkit config (output of
            ``envs.config.resolve_toolkit_config``). Keys whose values
            are unset / ``<NEEDS VALUE>`` / empty don't satisfy a
            ``requires:`` clause.

    Returns:
        ``GroupAvailability``. ``available_groups`` lists every group
        whose required keys are all set; ``dropped_groups`` maps each
        unavailable group's name → the list of missing keys that
        caused the drop. Groups with no ``requires:`` clause (or an
        empty list) are always available.
    """
    out = GroupAvailability()
    if not tool_groups_block:
        return out

    out.has_tool_groups_block = True

    for group_name, group_entry in tool_groups_block.items():
        if not isinstance(group_entry, Mapping):
            # Defensive: validation should have rejected this; treat
            # as available so a bad shape doesn't silently drop tools.
            out.available_groups.append(group_name)
            continue
        requires = group_entry.get("requires") or []
        if not isinstance(requires, list):
            out.available_groups.append(group_name)
            continue

        missing = [
            key for key in requires
            if not _is_config_key_set(resolved_config, key)
        ]
        if missing:
            out.dropped_groups[group_name] = missing
        else:
            out.available_groups.append(group_name)

    return out


def format_skip_log_line(toolkit_name: str, group_name: str, missing_keys: List[str]) -> str:
    """Compose the one-line greppable stderr message for a dropped group.

    Format (locked, tests assert on it):

        [toolbase.serve] group_skipped toolkit=<tk> name=<group> \\
            reason=missing_config keys=<csv>

    Designed so a user wondering "where are my MadGraph tools?" can
    ``grep group_skipped ~/.toolbase/logs/serve.log`` (or read the
    stderr stream Claude Code captures) and see the reason without
    digging.
    """
    keys_csv = ",".join(missing_keys)
    return (
        f"[toolbase.serve] group_skipped "
        f"toolkit={toolkit_name} name={group_name} "
        f"reason=missing_config keys={keys_csv}"
    )


__all__ = [
    "GroupAvailability",
    "NEEDS_VALUE_SENTINEL",
    "evaluate_tool_groups",
    "format_skip_log_line",
]
