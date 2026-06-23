"""D-011 contract: every state-mutating skill establishes a log session.

A regression here means a skill mutates passport.json / .awf state without a
log.session(...) wrapper, so its auto-emitted state.change escapes to the
orphan log instead of the project's .awf/log.jsonl. See tools/loglint.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LOGLINT = _REPO / "tools" / "loglint.py"


def _load_loglint():
    spec = importlib.util.spec_from_file_location("loglint", _LOGLINT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register before exec so the @dataclass in loglint can resolve
    # cls.__module__ via sys.modules (else AttributeError on dynamic load).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_all_state_mutating_skills_log_through_a_session():
    loglint = _load_loglint()
    violations = loglint.find_violations(_REPO / "skills")
    assert violations == [], "log-coverage violations:\n" + "\n".join(
        f"  - {v.skill}: {v.reason}" for v in violations
    )
