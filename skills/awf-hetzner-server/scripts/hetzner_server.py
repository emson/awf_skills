#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "hcloud>=2"]
# ///

"""awf-hetzner-server — provision one Hetzner Cloud VM for a project.

Writes the server entry into .awf/infra.json (Infra.hetzner.servers[]).
Idempotent: re-running with the same --name is a no-op.

Exit codes:
    0  — success (created, updated, or skipped)
    1  — project not found (no .awf/project.json walking up)
    2  — credentials missing (HETZNER_API_TOKEN not in any config layer)
    3  — remote API error (HetznerError)
    4  — state validation failure (StateValidationError; rare)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-hetzner-server/scripts/hetzner_server.py
#   parents[0] = .../awf-hetzner-server/scripts
#   parents[1] = .../awf-hetzner-server
#   parents[2] = .../skills
#   parents[3] = AWF_HOME (repo root)
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.hetzner import HetznerClient  # noqa: E402
from lib.hetzner.errors import HetznerError  # noqa: E402
from lib.state import Infra, Server, StateValidationError  # noqa: E402
from project import ProjectNotFound, find_project_root  # noqa: E402

# ---------------------------------------------------------------------------
# Cost-per-month lookup table (hardcoded for Phase B).
# TODO(phase-D): call Hetzner /server_types API for live pricing; see plan_008
# ---------------------------------------------------------------------------
_COST_TABLE: dict[str, float] = {
    "cx22": 4.5,
    "cx32": 7.5,
    "cpx21": 5.0,
}


def _cost_lookup(server_type: str) -> float:
    return _COST_TABLE.get(server_type, 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-hetzner-server",
        description="Provision one Hetzner Cloud server and record it in .awf/infra.json.",
    )
    parser.add_argument("--name", required=True, help="Server name (unique within Hetzner project).")
    parser.add_argument("--type", default="cx22", help="Hetzner server type slug.")
    parser.add_argument("--location", default="fsn1", help="Hetzner location code.")
    parser.add_argument("--role", default="app", help="Role label for Infra.hetzner.servers[].")
    parser.add_argument("--shared", action="store_true", default=False, help="Mark server as shared.")
    parser.add_argument(
        "--ssh-key",
        dest="ssh_key",
        action="append",
        metavar="KEY_NAME",
        help="SSH key name to attach; repeat for multiple.",
    )
    parser.add_argument("--json", action="store_true", default=False, help="Emit JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Step 1: locate project root (outside session — not-found exits before
    # session opens, per plan_004 lesson M1).
    try:
        root = find_project_root()
    except ProjectNotFound as exc:
        print(f"awf-hetzner-server: project not found: {exc}", file=sys.stderr)
        return 1

    safe_args = {
        "name": args.name,
        "type": args.type,
        "location": args.location,
        "role": args.role,
        "shared": args.shared,
        "ssh_key": args.ssh_key,
    }

    try:
        with log.session(composer="awf-hetzner-server", target="hetzner-server"):
            with log.invoke(skill="awf-hetzner-server", args=safe_args):
                # Resolve credentials first so missing creds exit 2, not 3.
                try:
                    hz = HetznerClient.from_env()
                except RuntimeError as exc:
                    print(f"awf-hetzner-server: {exc}", file=sys.stderr)
                    return 2

                infra = Infra.load_or_create(start=root)

                srv = hz.servers.get_or_create(
                    name=args.name,
                    type=args.type,
                    location=args.location,
                    ssh_keys=args.ssh_key or None,
                    labels={"awf-role": args.role, "awf-shared": str(args.shared).lower()},
                )

                new_entry = Server(
                    id=str(srv.id),
                    ip=srv.public_net.ipv4.ip,
                    role=args.role,
                    shared=args.shared,
                    cost_eur_month=_cost_lookup(args.type),
                )

                # Determine action by comparing against existing state
                idx = next(
                    (i for i, s in enumerate(infra.hetzner.servers) if s.id == new_entry.id),
                    None,
                )
                if idx is None:
                    infra.hetzner.servers.append(new_entry)
                    try:
                        infra.save()
                    except StateValidationError as exc:
                        print(f"awf-hetzner-server: state validation error: {exc}", file=sys.stderr)
                        return 4
                    action = "created"
                elif infra.hetzner.servers[idx] != new_entry:
                    infra.hetzner.servers[idx] = new_entry
                    try:
                        infra.save()
                    except StateValidationError as exc:
                        print(f"awf-hetzner-server: state validation error: {exc}", file=sys.stderr)
                        return 4
                    action = "updated"
                else:
                    action = "skip"

    except HetznerError as exc:
        print(f"awf-hetzner-server: Hetzner API error: {exc}", file=sys.stderr)
        return 3
    except StateValidationError as exc:
        print(f"awf-hetzner-server: state validation error: {exc}", file=sys.stderr)
        return 4

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "server": {
                "id": str(srv.id),
                "ip": srv.public_net.ipv4.ip,
                "name": args.name,
                "role": args.role,
            },
        }))
    else:
        print(f"server: {srv.id} @ {srv.public_net.ipv4.ip} ({args.name})")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
