"""
Which installed version of a toolkit is the one that serves.

Three callers need that answer and must not disagree: the serve
orchestrator (spawns the slot), ``tb setup`` (configures the slot), and
``tb list`` (tells the user which slot it is). Each had grown its own
copy of the rule, and they had already drifted — ``tb setup`` read only
the committed manifest while serve read the merged view, so an editable
pin in ``manifest.local.yaml`` sent the two commands at different slots.
This module is the one implementation.

The rule, in order:

1. The version pinned for this toolkit in the active project's manifest
   (committed ``manifest.yaml`` with ``manifest.local.yaml`` merged over
   it, local winning per name).
2. A pin naming a version that isn't in the cache resolves to *nothing*
   — we refuse rather than quietly serving a version nobody asked for.
3. No pin and one installed version: that one.
4. No pin and several: the highest. Nobody chose it, so the reason is
   reported as such and callers surface it.

Ordering is ``versioning.parse_version``, which yields ``(0, 0, 0)`` for
non-numeric slot names — so an ``editable`` slot sorts below every
numbered one and loses rule 4. An editable checkout serves only when a
pin names it.

That is deliberate. The cache is user-wide: one ``cache/<name>/editable/``
slot shared by every directory on the machine. If linking a checkout won
the fallback, one ``tb install -e`` would change what every agent session
everywhere runs, and confining it again would mean pinning numbered
versions in every *other* project — opt-out, across a scope you can't
see. Losing by default makes it opt-in instead: ``tb use <name>@editable``
selects it exactly where you say, and nowhere else.

It also keeps one rule whole. ``tb install`` never changes what serves —
that is what makes ``tb install foo@1.2.0`` leave 1.4.0 serving — and
``-e`` is an install like any other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..versioning import parse_version


# The cache-slot name for an editable install (a symlink to a source
# checkout). Kept in sync with ``cli.EDITABLE_VERSION``.
EDITABLE = "editable"

# Why a version was (or wasn't) chosen. Callers branch on these, so they
# are constants rather than inline strings.
PINNED = "pinned"
ONLY = "only"
HIGHEST = "highest"
PIN_MISSING = "pin-missing"
NOT_INSTALLED = "not-installed"


@dataclass
class Resolution:
    """The outcome of picking a version for one toolkit.

    ``version`` is None exactly when the toolkit can't be served:
    nothing installed, or a pin pointing at a slot that isn't there.
    """

    version: Optional[str]
    reason: str
    pin: Optional[str] = None
    available: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.version is not None

    @property
    def is_ambiguous(self) -> bool:
        """Several installed, nothing said which — we guessed the highest."""
        return self.reason == HIGHEST

    def describe(self) -> str:
        """One clause explaining the choice, for user-facing output."""
        if self.reason == PINNED:
            return f"pinned to {self.version}"
        if self.reason == ONLY:
            return "only version installed"
        if self.reason == HIGHEST:
            return "highest installed, no pin"
        if self.reason == PIN_MISSING:
            return (
                f"pinned version {self.pin} is not installed "
                f"(have: {', '.join(self.available)})"
            )
        return "not installed"


def version_sort_key(version: str) -> tuple:
    """Ordering key for cache-slot names; higher sorts later.

    Unparseable names — ``editable`` above all — score ``(0, 0, 0)`` and
    sort last, so they never win the unpinned fallback. See the module
    docstring for why an editable slot losing by default is the point.
    """
    return parse_version(version) or (0, 0, 0)


def sort_versions(versions: Sequence[str]) -> List[str]:
    """Installed versions, highest first. Unparseable names sort last."""
    return sorted(versions, key=version_sort_key, reverse=True)


def resolve_version(
    available: Sequence[str],
    *,
    pin: Optional[str] = None,
) -> Resolution:
    """Pick the serving version from ``available`` given an optional ``pin``.

    Pure: callers supply the installed version list and the pin, so this
    is testable without a cache or a manifest on disk.
    """
    versions = list(available)
    if not versions:
        return Resolution(
            version=None, reason=NOT_INSTALLED, pin=pin, available=[],
        )

    ordered = sort_versions(versions)

    if pin is not None:
        if pin in versions:
            return Resolution(
                version=pin, reason=PINNED, pin=pin, available=ordered,
            )
        return Resolution(
            version=None, reason=PIN_MISSING, pin=pin, available=ordered,
        )

    if len(versions) == 1:
        return Resolution(
            version=versions[0], reason=ONLY, pin=None, available=ordered,
        )

    return Resolution(
        version=ordered[0], reason=HIGHEST, pin=None, available=ordered,
    )


def active_pins(project_root=None) -> Dict[str, str]:
    """``{name: version}`` chosen for the active context.

    Versions live in the active loadout's ``versions:`` block; a toolkit
    without an entry takes the cache fallback. Keeping versions and tool
    selection in one file is what makes a loadout a complete
    specification — share it and it resolves the same way elsewhere,
    which is the whole point for a benchmark condition.

    Falls back to the pre-0.12 project manifest for any toolkit the
    loadout doesn't pin, so existing ``manifest.yaml`` /
    ``manifest.local.yaml`` pins keep working until they're migrated.

    Resolves the active project itself when ``project_root`` is None.
    Best-effort by design: every caller treats unreadable state as "no
    pins" and falls back to cache-only resolution, so a malformed file
    degrades the answer instead of breaking the command.
    """
    # Read through the package rather than the defining modules: tests
    # redirect the substrate by monkeypatching ``toolbase.envs.*``, and a
    # function-level ``from . import`` picks that up at call time.
    from . import load_merged_pins, project_manifest_path

    if project_root is None:
        try:
            from ..cli import _resolve_active_project_root
            project_root, _source = _resolve_active_project_root()
        except Exception:
            return {}

    # Layered lowest-first, each overriding the last per toolkit:
    #
    #   legacy manifest  ->  user loadout  ->  project loadout
    #
    # The user layer matters because a project's loadout shadows the
    # user's *whole* — right for curation, since a half-merged tool
    # selection is one nobody designed, but wrong for versions. Without
    # this, `tb activate` in a plain directory makes it a project and
    # silently drops every version you had chosen machine-wide: the
    # toolkit would jump from your pinned build to newest-installed with
    # nothing said. Versions layer; curation shadows.
    from ..serve.loadouts import resolve_loadout

    pins: Dict[str, str] = {}
    for root in _version_layers(project_root):
        try:
            pins.update(load_merged_pins(project_manifest_path(
                root if root is not None else _default_root()
            )))
        except Exception:
            pass
        try:
            pins.update(resolve_loadout(root).versions)
        except Exception:
            pass  # absent or malformed: lower layers still apply

    return pins


def _default_root():
    from . import default_project_root
    return default_project_root()


def _version_layers(project_root):
    """Roots to read versions from, lowest priority first.

    ``None`` is the user layer (the default-project's manifest and the
    user loadout); ``project_root`` then layers over it. Outside a
    project the two resolve identically, so the merge is a no-op rather
    than a special case.
    """
    try:
        if project_root is None or Path(project_root).resolve() == _default_root().resolve():
            return [None]
    except Exception:
        pass
    return [None, project_root]
