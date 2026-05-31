"""pytest configuration: add repo root to sys.path so lib/ is importable."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure lib/ (and siblings) are importable in all tests
_repo_root = Path(__file__).parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
