#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "pyyaml>=6"]
# ///

"""awf-kamal-setup — run `kamal setup` after DNS propagation poll.

Wraps KamalRunner.setup(). The lib polls DNS before shelling out to kamal.
Skips if Shared.play_server.kamal_setup_done_for_server_id == Shared.play_server.hetzner_id
(proxy already installed on this server). Re-runs automatically if the server
is reprovisioned and gets a new hetzner_id.

Exit codes:
    0  — success (action may be "skip" or "created")
    1  — project not found (no .awf/project.json walking up)
    2  — kamal CLI not on PATH (KamalNotInstalled)
    3  — remote error (KamalDnsTimeout or KamalSetupFailed)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.kamal.errors import KamalDnsTimeout, KamalNotInstalled, KamalSetupFailed  # noqa: E402
from lib.kamal.runner import KamalRunner  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402
from lib.state import ProjectAnchor, Shared  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-kamal-setup",
        description="Run `kamal setup` after polling DNS propagation.",
    )
    parser.add_argument("--server-ip", required=True, dest="server_ip",
                        help="IP address kamal will SSH to.")
    parser.add_argument("--domain", default=None,
                        help="Domain whose A-record is polled. Defaults to anchor.domain.")
    parser.add_argument("--dns-timeout", type=float, default=600.0, dest="dns_timeout",
                        help="Seconds to wait for DNS propagation (default: 600).")
    parser.add_argument("--json", action="store_true", default=False,
                        help="Emit machine-readable JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Locate project root OUTSIDE session.
    try:
        anchor = ProjectAnchor.load()
    except ProjectNotFound as exc:
        print(f"awf-kamal-setup: project not found: {exc}", file=sys.stderr)
        return 1

    root = anchor._path.parent.parent  # type: ignore[union-attr]
    domain = args.domain or anchor.domain

    safe_args = {
        "server_ip": args.server_ip,
        "domain": domain,
        "dns_timeout": args.dns_timeout,
    }

    # Load shared state to check skip condition.
    shared = Shared.load_or_create()

    # Determine whether to skip based on server-ID tracking.
    skip = (
        shared.play_server is not None
        and shared.play_server.hetzner_id != ""
        and shared.play_server.kamal_setup_done_for_server_id == shared.play_server.hetzner_id
    )

    try:
        with log.session(composer="awf-kamal-setup", target="kamal-setup"):
            with log.invoke(skill="awf-kamal-setup", args=safe_args):
                if skip:
                    action = "skip"
                else:
                    runner = KamalRunner(cwd=root, dns_timeout_s=args.dns_timeout)
                    runner.setup(domain=domain, server_ip=args.server_ip)
                    action = "created"
                    # Update tracking field if we have a shared play_server record.
                    if shared.play_server is not None:
                        shared.play_server.kamal_setup_done_for_server_id = (
                            shared.play_server.hetzner_id
                        )
                        shared.save()

    except KamalNotInstalled as exc:
        print(f"awf-kamal-setup: {exc}", file=sys.stderr)
        return 2
    except KamalDnsTimeout as exc:
        if as_json:
            print(jsonlib.dumps({
                "skill": "awf-kamal-setup",
                "action": "gate",
                "gate": "dns_propagation",
                "result": "fail",
                "reason": str(exc),
                "domain": exc.domain,
                "expected_ip": exc.expected_ip,
            }))
        else:
            print(f"awf-kamal-setup: {exc}", file=sys.stderr)
        return 3
    except KamalSetupFailed as exc:
        print(f"awf-kamal-setup: {exc}", file=sys.stderr)
        return 3

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "domain": domain,
            "server_ip": args.server_ip,
        }))
    else:
        print(f"domain: {domain}")
        print(f"server_ip: {args.server_ip}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
