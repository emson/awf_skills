#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "httpx>=0.27"]
# ///

"""awf-neon-project — provision one Neon Postgres project for a project.

Writes neon.project_id into .awf/infra.json.
Idempotent: re-running with the same --name is a no-op.

Exit codes:
    0  — success (created, updated, or skipped)
    1  — project not found (no .awf/project.json walking up)
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
# Script lives at: <AWF_HOME>/skills/awf-neon-project/scripts/neon_project.py
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
        prog="awf-neon-project",
        description="Provision one Neon project and record its ID in .awf/infra.json.",
    )
    parser.add_argument("--name", required=True, help="Neon project name.")
    parser.add_argument("--region", default="aws-eu-central-1", help="Neon region ID.")
    parser.add_argument("--pg-version", dest="pg_version", type=int, default=16, help="Postgres major version.")
    parser.add_argument("--json", action="store_true", default=False, help="Emit JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Step 1: locate project root outside session (plan_004 lesson M1).
    try:
        root = find_project_root()
    except ProjectNotFound as exc:
        print(f"awf-neon-project: project not found: {exc}", file=sys.stderr)
        return 1

    safe_args = {
        "name": args.name,
        "region": args.region,
        "pg_version": args.pg_version,
    }

    try:
        with log.session(composer="awf-neon-project", target="neon-project"):
            with log.invoke(skill="awf-neon-project", args=safe_args):
                # Resolve credentials first so missing creds exit 2, not 3.
                try:
                    nc = NeonClient.from_env()
                except RuntimeError as exc:
                    print(f"awf-neon-project: {exc}", file=sys.stderr)
                    return 2

                infra = Infra.load_or_create(start=root)

                # Capture before_id BEFORE the lib call (plan_008 Reviewer note).
                before_id = infra.neon.project_id

                proj = nc.projects.get_or_create(
                    name=args.name,
                    region_id=args.region,
                    pg_version=args.pg_version,
                )

                if infra.neon.project_id == proj.id:
                    action = "skip"
                else:
                    # Determine action from the before-state, not after (T4 bug fix).
                    action = "created" if not before_id else "updated"
                    infra.neon.project_id = proj.id
                    # Do NOT touch branch_id / branch_name — owned by awf-neon-branch.
                    try:
                        infra.save()
                    except StateValidationError as exc:
                        print(f"awf-neon-project: state validation error: {exc}", file=sys.stderr)
                        return 4

    except NeonError as exc:
        print(f"awf-neon-project: Neon API error: {exc}", file=sys.stderr)
        return 3
    except StateValidationError as exc:
        print(f"awf-neon-project: state validation error: {exc}", file=sys.stderr)
        return 4

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "neon_project_id": proj.id,
            "neon_project_name": proj.name,
        }))
    else:
        print(f"neon_project_id: {proj.id}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
