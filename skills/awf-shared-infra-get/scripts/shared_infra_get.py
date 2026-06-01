#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "hcloud>=2", "httpx>=0.27"]
# ///

"""awf-shared-infra-get — provision user-scope shared play infrastructure.

Writes play_server and play_neon_project_id into ~/.config/awf/shared.json.
Idempotent: re-running is a no-op when both resources already exist.

This skill is user-scope: it does NOT require a project anchor (.awf/project.json).

Exit codes:
    0  — success (created, partial, or skipped)
    2  — credentials missing (HETZNER_API_TOKEN or NEON_API_KEY)
    3  — remote API error (HetznerError or NeonError)
    4  — state validation failure (StateValidationError; rare)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-shared-infra-get/scripts/shared_infra_get.py
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.hetzner import HetznerClient  # noqa: E402
from lib.hetzner.errors import HetznerError  # noqa: E402
from lib.neon import NeonClient  # noqa: E402
from lib.neon.errors import NeonError  # noqa: E402
from lib.state import PlayServer, Shared, StateValidationError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-shared-infra-get",
        description="Provision shared play server and Neon project in ~/.config/awf/shared.json.",
    )
    parser.add_argument("--server-type", default="cx22", help="Hetzner server type slug.")
    parser.add_argument("--server-location", default="fsn1", help="Hetzner location code.")
    parser.add_argument("--neon-region", default="aws-eu-central-1", help="Neon region ID.")
    parser.add_argument("--play-hostname", default="awf-play", help="Name for the Hetzner play server.")
    parser.add_argument("--play-neon-name", default="awf-play", help="Name for the Neon play project.")
    parser.add_argument("--json", action="store_true", default=False, help="Emit JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    safe_args = {
        "server_type": args.server_type,
        "server_location": args.server_location,
        "neon_region": args.neon_region,
        "play_hostname": args.play_hostname,
        "play_neon_name": args.play_neon_name,
    }

    try:
        with log.session(composer="awf-shared-infra-get", target="shared-infra"):
            with log.invoke(skill="awf-shared-infra-get", args=safe_args):
                # Resolve credentials first so missing creds exit 2, not 3.
                try:
                    hz = HetznerClient.from_env()
                except RuntimeError as exc:
                    print(f"awf-shared-infra-get: {exc}", file=sys.stderr)
                    return 2

                try:
                    nc = NeonClient.from_env()
                except RuntimeError as exc:
                    print(f"awf-shared-infra-get: {exc}", file=sys.stderr)
                    return 2

                shared = Shared.load_or_create()

                # --- play_server ---
                if shared.play_server is None or not shared.play_server.hetzner_id:
                    srv = hz.servers.get_or_create(
                        name=args.play_hostname,
                        type=args.server_type,
                        location=args.server_location,
                        labels={"awf-role": "play", "awf-shared": "true"},
                    )
                    # We will set play_server AFTER Neon succeeds (single-write-at-end).
                    _pending_server = PlayServer(
                        hetzner_id=str(srv.id),
                        ip=srv.public_net.ipv4.ip,
                        hostname=args.play_hostname,
                        registry="ghcr.io",  # D-005: default registry
                        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    )
                    _server_was_missing = True
                else:
                    _pending_server = shared.play_server
                    _server_was_missing = False
                    srv = None  # not fetched — already exists

                # --- play_neon_project_id ---
                if not shared.play_neon_project_id:
                    proj = nc.projects.get_or_create(
                        name=args.play_neon_name,
                        region_id=args.neon_region,
                    )
                    _pending_neon_id = proj.id
                    _neon_was_missing = True
                else:
                    _pending_neon_id = shared.play_neon_project_id
                    _neon_was_missing = False
                    proj = None  # not fetched — already exists

                # Single atomic write after both succeed (plan_008 §decisions-1).
                if _server_was_missing or _neon_was_missing:
                    shared.play_server = _pending_server
                    shared.play_neon_project_id = _pending_neon_id
                    try:
                        shared.save()
                    except StateValidationError as exc:
                        print(f"awf-shared-infra-get: state validation error: {exc}", file=sys.stderr)
                        return 4

    except HetznerError as exc:
        print(f"awf-shared-infra-get: Hetzner API error: {exc}", file=sys.stderr)
        return 3
    except NeonError as exc:
        print(f"awf-shared-infra-get: Neon API error: {exc}", file=sys.stderr)
        return 3
    except StateValidationError as exc:
        print(f"awf-shared-infra-get: state validation error: {exc}", file=sys.stderr)
        return 4

    if _server_was_missing and _neon_was_missing:
        action = "created"
    elif _server_was_missing or _neon_was_missing:
        action = "partial"
    else:
        action = "skip"

    # Re-read final state for output.
    final_shared = Shared.load_or_create()
    ps = final_shared.play_server
    server_info = {
        "id": ps.hetzner_id if ps else "",
        "ip": ps.ip if ps else "",
        "hostname": ps.hostname if ps else "",
    }

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "play_server": server_info,
            "play_neon_project_id": final_shared.play_neon_project_id or "",
        }))
    else:
        print(f"play_server: {server_info['id']} @ {server_info['ip']} ({server_info['hostname']})")
        print(f"play_neon_project_id: {final_shared.play_neon_project_id or ''}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
