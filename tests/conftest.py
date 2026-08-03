"""Shared pytest configuration.

Pins the console width for the whole suite. Rich wraps to the terminal
width, and most CLI assertions look for a phrase in captured output --
so where the wrap lands decides whether the phrase is one string or two.
Nothing about the code under test changes; the assertion just stops
seeing what it is looking for.

That made the suite environment-dependent in a way that only showed up
off this machine. ``test_cli_remove_absent_is_friendly_noop`` asserts
"nothing to remove" appears in

    No agent script at <tmp path>; nothing to remove.

At width 80 the wrap point depends on the length of the tmp path, which
differs per platform: macOS's long ``/private/var/folders/...`` pushes
the whole phrase onto its own line and the test passes, while CI's
shorter ``/tmp/pytest-of-runner/...`` leaves room for "nothing" and
spills "to remove" -- splitting the phrase and failing the assertion.
Counterintuitively the shorter path is the one that breaks. It was green
locally and red on both CI Pythons for exactly that reason.

Pinning the width makes the output deterministic everywhere. It is wide
enough that these one-line messages don't wrap at all, so assertions
match content rather than layout. Anything that genuinely needs to test
wrapping should construct its own Console with an explicit width instead
of depending on the ambient one.
"""

from __future__ import annotations

import pytest

# Wide enough that no single-line CLI message wraps, including ones
# carrying a long absolute path. Raise it rather than adding
# whitespace-normalising to individual assertions.
CONSOLE_WIDTH = "200"


@pytest.fixture(autouse=True)
def _pinned_console_width(monkeypatch):
    """Fix Rich's width for every test.

    Autouse because the failure mode is silent: a test that forgets this
    passes here and fails wherever paths are a different length.
    """
    monkeypatch.setenv("COLUMNS", CONSOLE_WIDTH)
    # Rich consults LINES alongside COLUMNS; pin both so a terminal-less
    # environment can't fall back to a different size mid-suite.
    monkeypatch.setenv("LINES", "50")
