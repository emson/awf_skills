"""pytest configuration for tests/lib/.

Provides a fixture that keeps lib.__dict__["log"] and sys.modules["lib.log"]
in sync around each test. This is necessary because:

  - lib/log.py is importable; once imported it is cached as an attribute on
    the ``lib`` package object (lib.__dict__["log"] = <real module>).
  - Some tests in test_state.py and test_project.py inject a FakeLog via
    ``monkeypatch.setitem(sys.modules, "lib.log", fake)``. Before lib/log.py
    existed, that was sufficient — lib.__dict__ had no "log" attribute, so
    ``from lib import log`` fell back to sys.modules["lib.log"].
  - Now that lib/log.py exists, ``from lib import log`` resolves via the
    package attribute first, bypassing the sys.modules patch.

The fix: before each test, snapshot and remove lib.__dict__["log"] so that
``from lib import log`` always goes through sys.modules. After the test,
restore it (monkeypatch handles sys.modules cleanup automatically).

This fixture is autouse=True and scoped to the tests/lib/ directory only.
"""

from __future__ import annotations

import sys
from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def _sync_lib_log_attr() -> Iterator[None]:
    """Ensure lib.__dict__["log"] is consistent with sys.modules["lib.log"].

    Removes the attribute before each test so fresh ``from lib import log``
    lookups always go through sys.modules (where monkeypatch operates).
    Restores on teardown.
    """
    lib_pkg = sys.modules.get("lib")
    if lib_pkg is None:
        yield
        return

    # Snapshot the current "log" attribute on the lib package
    real_log = getattr(lib_pkg, "log", None)
    had_attr = "log" in lib_pkg.__dict__

    # Remove so ``from lib import log`` falls back to sys.modules["lib.log"]
    if had_attr:
        del lib_pkg.__dict__["log"]

    try:
        yield
    finally:
        # Restore the original attribute state
        if had_attr and real_log is not None:
            lib_pkg.__dict__["log"] = real_log
        elif "log" in lib_pkg.__dict__:
            # Test may have set it; restore to the real module if it's in sys.modules
            real = sys.modules.get("lib.log")
            if real is not None:
                lib_pkg.__dict__["log"] = real
            else:
                del lib_pkg.__dict__["log"]
