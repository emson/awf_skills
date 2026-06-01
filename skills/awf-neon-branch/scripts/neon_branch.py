#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "httpx>=0.27"]
# ///

"""awf-neon-branch — provision one Neon branch on a project.

Writes neon.branch_id and neon.branch_name into .awf/infra.json.
Idempotent: re-running with the same --name is a no-op.

Exit codes:
    0  — success (created or skipped)
    1  — project not found (no .awf/project.json) or no Neon project_id
    2  — credentials missing (NEON_API_KEY not in any config layer)
    3  — remote API error (NeonError)
    4  — state validation failure (StateValidationError; rare)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-neon-branch/scripts/neon_branch.py
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.neon import NeonClient  # noqa: E402
from lib.neon.errors import NeonError  # noqa: E402
from lib.state import Infra, StateValidationError  # noqa: E402
from project import ProjectNotFound, find_project_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-neon-branch",
        description="Provision one Neon branch and record it in .awf/infra.json.",
    )
    parser.add_argument("--name", required=True, help="Branch name.")
    parser.add_argument("--project-id", dest="project_id", default=None, help="Neon project ID (default: from infra.json).")
    parser.add_argument("--parent-id", dest="parent_id", default=None, help="Parent branch ID to fork from.")
    parser.add_argument("--json", action="store_true", default=False, help="Emit JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Step 1: locate project root outside session (plan_004 lesson M1).
    try:
        root = find_project_root()
    except ProjectNotFound as exc:
        print(f"awf-neon-branch: project not found: {exc}", file=sys.stderr)
        return 1

    safe_args = {
        "name": args.name,
        "project_id": args.project_id,
        "parent_id": args.parent_id,
    }

    # Pre-load infra to resolve project_id (needed before session for early exit).
    infra = Infra.load_or_create(start=root)
    project_id = args.project_id or infra.neon.project_id
    if not project_id:
        print(
            "awf-neon-branch: no Neon project_id "
            "(set via --project-id or run awf-neon-project first)",
            file=sys.stderr,
        )
        return 1

    try:
        with log.session(composer="awf-neon-branch", target="neon-branch"):
            with log.invoke(skill="awf-neon-branch", args=safe_args):
                # Resolve credentials first so missing creds exit 2, not 3.
                try:
                    nc = NeonClient.from_env()
                except RuntimeError as exc:
                    print(f"awf-neon-branch: {exc}", file=sys.stderr)
                    return 2

                br = nc.branches.get_or_create(
                    project_id,
                    name=args.name,
                    parent_id=args.parent_id,
                )

                if infra.neon.branch_id == br.id and infra.neon.branch_name == br.name:
                    action = "skip"
                else:
                    infra.neon.branch_id = br.id
                    infra.neon.branch_name = br.name
                    # Ensure project_id invariant holds.
                    infra.neon.project_id = project_id
                    try:
                        infra.save()
                    except StateValidationError as exc:
                        print(f"awf-neon-branch: state validation error: {exc}", file=sys.stderr)
                        return 4
                    action = "created"

    except NeonError as exc:
        print(f"awf-neon-branch: Neon API error: {exc}", file=sys.stderr)
        return 3
    except StateValidationError as exc:
        print(f"awf-neon-branch: state validation error: {exc}", file=sys.stderr)
        return 4

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "branch_id": br.id,
            "branch_name": br.name,
            "project_id": project_id,
        }))
    else:
        print(f"branch_id: {br.id}")
        print(f"branch_name: {br.name}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
