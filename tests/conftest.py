"""pytest configuration: add repo root to sys.path so lib/ is importable."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure lib/ (and siblings) are importable in all tests
_repo_root = Path(__file__).parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Also add lib/ itself so intra-lib sibling imports (e.g. `from slug import …`
# in lib/passport.py) resolve when running under pytest.
_lib_root = _repo_root / "lib"
if str(_lib_root) not in sys.path:
    sys.path.insert(0, str(_lib_root))


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_config_dir():
    """Redirect cross-project state to a temp dir for the whole test session.

    Without this, any test that exercises lib/log without a session writes to
    the user's real ~/.config/awf/orphan-log.jsonl (and sessions.jsonl /
    shared.json) — the cause of the historical orphan-log bloat (D-011).
    AWF_CONFIG_DIR (see lib/awf_home.user_config_dir) redirects it.
    """
    prev = os.environ.get("AWF_CONFIG_DIR")
    with tempfile.TemporaryDirectory(prefix="awf-test-config-") as d:
        os.environ["AWF_CONFIG_DIR"] = d
        try:
            yield d
        finally:
            if prev is None:
                os.environ.pop("AWF_CONFIG_DIR", None)
            else:
                os.environ["AWF_CONFIG_DIR"] = prev
