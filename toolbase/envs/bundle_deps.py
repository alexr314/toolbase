"""Per-bundle dependency extraction from a parsed ``toolkit.yaml``.

A toolkit author can declare optional dependencies per bundle:

```yaml
bundles:
  basic:
    deps:
      - requests
  scientific:
    deps:
      - numpy>=2.0
      - pandas
```

This module computes the union of pip-specs for a selected subset of
bundles, which the install command pip-installs on top of the
toolkit's always-installed base ``requirements.txt``. Selection
happens via either the extras-style syntax (``tb install foo[a,b]``)
or the ``--bundle`` flag.

The validator in ``toolbase/validation.py::_validate_bundles`` is the
authoritative shape-checker; this module trusts that any ``bundles:``
block reaching it has already passed validation (or will be loose
about malformed entries). Defensive reading mirrors how
``toolbase/serve/orchestrator.py::_read_bundles_and_membership``
handles malformed yaml: skip what's broken, return what's usable.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def deps_for_bundles(
    toolkit_yaml: Optional[Dict[str, Any]],
    selected_bundles: Iterable[str],
) -> List[str]:
    """Return the union of pip-specs declared by the selected bundles.

    Walks ``toolkit_yaml["bundles"][<name>]["deps"]`` for each name in
    ``selected_bundles`` and returns the de-duplicated union in
    first-seen order. Bundles in the selection that aren't declared in
    the yaml contribute nothing. Bundles with no ``deps:`` (or an
    empty list) contribute nothing.

    Returns an empty list when ``toolkit_yaml`` is None or has no
    ``bundles:`` block, when ``selected_bundles`` is empty, or when
    none of the selected bundles declare deps.

    The ordering of the returned list is stable (insertion order)
    because pip resolves transitive requirements regardless, but
    stable order keeps install logs and lockfile diffs readable.
    """
    if not toolkit_yaml or not isinstance(toolkit_yaml, dict):
        return []
    bundles_block = toolkit_yaml.get("bundles")
    if not isinstance(bundles_block, dict):
        return []

    out: List[str] = []
    seen: set = set()
    for name in selected_bundles:
        entry = bundles_block.get(name)
        if not isinstance(entry, dict):
            continue
        deps = entry.get("deps")
        if not isinstance(deps, list):
            continue
        for spec in deps:
            if not isinstance(spec, str):
                continue
            spec = spec.strip()
            if not spec or spec in seen:
                continue
            seen.add(spec)
            out.append(spec)
    return out


def declared_bundle_names(
    toolkit_yaml: Optional[Dict[str, Any]],
) -> List[str]:
    """Return the list of bundle names declared in ``toolkit.yaml``.

    Convenience for the install command (and ``tb list``) to know what
    bundles a toolkit even has — useful when validating ``--bundle X``
    or the extras-form against the toolkit's actual schema. Empty list
    if the yaml has no ``bundles:`` block.
    """
    if not toolkit_yaml or not isinstance(toolkit_yaml, dict):
        return []
    bundles_block = toolkit_yaml.get("bundles")
    if not isinstance(bundles_block, dict):
        return []
    return [name for name in bundles_block.keys() if isinstance(name, str)]
