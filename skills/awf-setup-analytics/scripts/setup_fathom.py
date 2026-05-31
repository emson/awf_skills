#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31"]
# ///

"""awf-setup-analytics — provision a Fathom site for the current
project and persist its `site_id` into `passport.json`.

Idempotent (A5):
  - passport already has `fathom_site_id` AND Fathom confirms it
    exists  →  no-op.
  - passport has `fathom_site_id` but Fathom returns 404 →  drift;
    refuse without `--force`. With `--force`, re-create.
  - passport empty → search-or-create by domain, then patch passport.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME / "lib"))

from project import find_project_root, ProjectNotFound  # noqa: E402
from passport import Passport  # noqa: E402
from slug import normalize_domain  # noqa: E402
from fathom.client import get_client, get_or_create_site, FathomError  # noqa: E402


def parse_args(argv: list[str]) -> dict:
    args = argv[1:]
    out = {"force": False}
    bad = [a for a in args if a not in ("--force",)]
    if bad:
        print(f"unknown argument(s): {' '.join(bad)}", file=sys.stderr)
        print("usage: setup_fathom.py [--force]", file=sys.stderr)
        sys.exit(2)
    if "--force" in args:
        out["force"] = True
    return out


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    try:
        project_root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    passport = Passport.load(project_root)
    domain = normalize_domain(passport.domain)

    try:
        client = get_client(project_root=project_root, awf_home=AWF_HOME)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    existing_id = (passport.fathom_site_id or "").strip()

    if existing_id and not opts["force"]:
        try:
            if client.site_exists(existing_id):
                print(f"- Fathom site_id={existing_id} already in passport and confirmed live; no-op")
                passport.mark_gate(
                    "analytics_setup",
                    meta={"site_id": existing_id, "outcome": "noop"},
                )
                passport.save(project_root)
                return 0
            else:
                print(
                    f"- DRIFT: passport has fathom_site_id={existing_id} but Fathom returns 404.\n"
                    f"  Re-run with --force to create a new site, or manually edit passport.json.",
                    file=sys.stderr,
                )
                return 1
        except FathomError as e:
            print(f"error checking Fathom site {existing_id}: {e}", file=sys.stderr)
            return 1

    # No id, or --force: search-or-create.
    try:
        site = get_or_create_site(client, domain)
    except FathomError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    site_id = site.get("id")
    if not site_id:
        print(f"error: Fathom did not return an id: {site!r}", file=sys.stderr)
        return 1

    passport.fathom_site_id = site_id
    passport.mark_gate(
        "analytics_setup",
        meta={"site_id": site_id, "outcome": "created" if not existing_id else "recreated"},
    )
    passport.save(project_root)
    print(f"- passport.fathom_site_id = {site_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
