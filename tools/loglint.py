#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""loglint — enforce the logging-coverage contract (D-011).

A skill that mutates project state MUST wrap its work in ``log.session(...)``
so that the auto-emitted ``state.change`` (lib/passport.py, lib/state.py) is
routed to the project's ``.awf/log.jsonl`` and attributed to the skill —
rather than escaping to the orphan log. See docs/08-logging.md and D-011.

The contract, enforced here and by tests/test_log_coverage.py:

    If a skill script mutates project state (it calls ``.save(`` on a state
    model, or ``apply_overlay(``), it must establish a logging session by
    calling ``log.session(``.

Heuristic, deliberately: a text scan is the right weight for a CI guard. It
cannot prove a session wraps the *right* code, but it catches the failure mode
we actually have — skills that mutate state with no logging at all.

Allowlist (skills that legitimately mutate non-project state, so no project
log applies):
    awf-init     — writes ~/.config/awf/.env (global onboarding, no project)
    awf-install  — runs npm install (node_modules, not .awf/ state)

Usage:
    uv run tools/loglint.py            # human report; exit 1 on any violation
    uv run tools/loglint.py --json     # machine-readable
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# State-mutation markers: a script containing any of these writes project state.
# (friendly label, pattern) — the label appears in violation messages.
_MUTATION_MARKERS = (
    ("state .save()", re.compile(r"\.save\(")),        # Passport.save / Infra.save
    ("apply_overlay()", re.compile(r"\bapply_overlay\(")),  # template file writes
)
# The routing+attribution wrapper that the contract requires.
_SESSION_MARKER = re.compile(r"\blog\.session\(")

# Skills exempt from the contract (mutate non-project state only).
ALLOWLIST = frozenset({"awf-init", "awf-install"})


@dataclass(frozen=True)
class Violation:
    skill: str
    script: str
    reason: str


def _skill_name(script: Path) -> str:
    # skills/<skill>/scripts/<file>.py  → <skill>
    for parent in script.parents:
        if parent.parent.name == "skills":
            return parent.name
    return script.parent.parent.name


def find_violations(skills_dir: Path) -> list[Violation]:
    """Return one Violation per skill script that mutates state without a session."""
    out: list[Violation] = []
    for script in sorted(skills_dir.glob("*/scripts/*.py")):
        skill = _skill_name(script)
        if skill in ALLOWLIST:
            continue
        src = script.read_text(encoding="utf-8", errors="replace")
        mutates = [label for label, pat in _MUTATION_MARKERS if pat.search(src)]
        if not mutates:
            continue
        if _SESSION_MARKER.search(src):
            continue
        out.append(
            Violation(
                skill=skill,
                script=str(script),
                reason=(
                    f"mutates project state ({', '.join(mutates)}) but never "
                    "calls log.session(...) — state.change will escape to the "
                    "orphan log instead of .awf/log.jsonl (D-011)"
                ),
            )
        )
    return out


def _repo_skills_dir() -> Path:
    # tools/loglint.py → repo root → skills/
    return Path(__file__).resolve().parent.parent / "skills"


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    violations = find_violations(_repo_skills_dir())
    if as_json:
        print(json.dumps([v.__dict__ for v in violations], indent=2))
    else:
        if not violations:
            print("loglint: OK — all state-mutating skills establish a log session.")
        else:
            print(f"loglint: {len(violations)} violation(s):\n")
            for v in violations:
                print(f"  ✗ {v.skill}")
                print(f"    {v.reason}")
                print(f"    {v.script}\n")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
