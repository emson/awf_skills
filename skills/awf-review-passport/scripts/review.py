#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-review-passport — lint passport.json against template expectations.

Splits problems into errors (block) and warnings (don't block):

- ERRORS: schema/identity mismatches, unsupported stack pins. The
  passport must not be deployed in this state.
- WARNINGS: empty `site_name`, fewer than 3 FAQs, empty `features[0]`.
  These usually mean `awf-generate-content` hasn't run or hasn't been
  reviewed by a human yet.

`Passport.validate()` returns problems prefixed with `warn:` for the
soft cases — we partition on that prefix so this script stays in lock-
step with the schema's rules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
# Repo root first (so `from lib import log` resolves to the SAME lib.log module
# that passport.py uses — shared ContextVars), then lib/ for sibling imports.
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from project import find_project_root, ProjectNotFound  # noqa: E402
from passport import Passport  # noqa: E402


GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def _color(s: str, c: str) -> str:
    return f"{c}{s}{RESET}" if sys.stdout.isatty() else s


def parse_args(argv: list[str]) -> dict:
    args = argv[1:]
    out = {"mark_gate": False}
    bad = [a for a in args if a not in ("--mark-reviewed",)]
    if bad:
        print(f"unknown argument(s): {' '.join(bad)}", file=sys.stderr)
        print("usage: review.py [--mark-reviewed]", file=sys.stderr)
        sys.exit(2)
    if "--mark-reviewed" in args:
        out["mark_gate"] = True
    return out


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    # Resolve project root outside the session (pattern from plan_004 lesson M1).
    try:
        root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    safe_args = {"mark_reviewed": opts["mark_gate"]}

    with log.session(composer="awf-review-passport", target="landing", start=root):
        with log.invoke(skill="awf-review-passport", args=safe_args):
            passport = Passport.load(root)
            problems = passport.validate()

            errors  = [p for p in problems if not p.startswith("warn:")]
            warns   = [p[len("warn:"):].strip() for p in problems if p.startswith("warn:")]

            print(f"awf-review-passport — {root / 'passport.json'}")
            print("─" * 60)
            print(f"  domain          : {passport.domain}")
            print(f"  project_name    : {passport.project_name}")
            print(f"  schema_version  : {passport.schema_version}")
            print(f"  template_version: {passport.template_version}")
            print(f"  fathom_site_id  : {passport.fathom_site_id or '(unset)'}")
            print(f"  faqs            : {len(passport.faqs)} entries")
            print()

            if errors:
                for e in errors:
                    print(_color(f"  [ERR]  {e}", RED))
            if warns:
                for w in warns:
                    print(_color(f"  [WARN] {w}", YELLOW))
            if not errors and not warns:
                print(_color("  no issues found", GREEN))

            if errors:
                print()
                print(_color(f"{len(errors)} error(s); refusing to mark reviewed.", RED))
                return 1

            if opts["mark_gate"]:
                log.gate(
                    name="passport_reviewed",
                    reason="Human confirmed passport.json is ready for launch.",
                    instructions="Run `awf-review-passport --mark-reviewed` after inspecting passport.json.",
                )
                passport.mark_gate("passport_reviewed", meta={"warnings": len(warns)})
                passport.save(root)
                print()
                print(_color("- gate `passport_reviewed` marked complete.", GREEN))
            else:
                print()
                print("Pass --mark-reviewed once you've inspected the file to advance the launch gate.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
