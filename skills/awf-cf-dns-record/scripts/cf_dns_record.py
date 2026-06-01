#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cloudflare>=3", "pydantic>=2"]
# ///

"""awf-cf-dns-record — create one Cloudflare DNS record.

Writes the record ID into passport.json under cloudflare["<TYPE>:<name>"].
Idempotent: re-running with the same inputs is a no-op.

Exit codes:
    0  — success (created or skipped)
    1  — project not found (no .awf/project.json walking up)
    2  — credentials missing (Cloudflare credentials not in any config layer)
    3  — remote API error (Cloudflare API or zone not found)
    4  — state validation failure (StateValidationError; rare)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-cf-dns-record/scripts/cf_dns_record.py
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.cf.client import get_client  # noqa: E402
from lib.cf.dns import create_dns_record  # noqa: E402
from lib.passport import Passport  # noqa: E402
from project import ProjectNotFound, find_project_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-cf-dns-record",
        description="Create one Cloudflare DNS record and record its ID in passport.json.",
    )
    parser.add_argument("--type", required=True, choices=["A", "AAAA", "CNAME", "TXT"], help="DNS record type.")
    parser.add_argument("--name", required=True, help="Subdomain (@ or www or full FQDN).")
    parser.add_argument("--content", required=True, help="Record value (e.g. IP address or hostname).")

    proxied_group = parser.add_mutually_exclusive_group()
    proxied_group.add_argument("--proxied", dest="proxied", action="store_true", default=None, help="Enable Cloudflare proxy.")
    proxied_group.add_argument("--no-proxied", dest="proxied", action="store_false", help="Disable Cloudflare proxy.")

    parser.add_argument("--domain", default=None, help="Override domain (default: from project anchor).")
    parser.add_argument("--json", action="store_true", default=False, help="Emit JSON on stdout.")

    args = parser.parse_args()
    as_json: bool = args.json

    # Determine proxied default based on record type if not explicitly set.
    proxied: bool
    if args.proxied is None:
        proxied = args.type in ("A", "AAAA", "CNAME")
    else:
        proxied = args.proxied

    # Step 1: locate project root outside session (plan_004 lesson M1).
    try:
        root = find_project_root()
    except ProjectNotFound as exc:
        print(f"awf-cf-dns-record: project not found: {exc}", file=sys.stderr)
        return 1

    safe_args = {
        "type": args.type,
        "name": args.name,
        "content": args.content,
        "proxied": proxied,
        "domain": args.domain,
    }

    try:
        with log.session(composer="awf-cf-dns-record", target="dns-record"):
            with log.invoke(skill="awf-cf-dns-record", args=safe_args):
                # Resolve credentials first so missing creds exit 2, not 3.
                try:
                    cf = get_client()
                except RuntimeError as exc:
                    print(f"awf-cf-dns-record: {exc}", file=sys.stderr)
                    return 2

                # Load passport to get domain and existing cloudflare record cache.
                passport = Passport.load(root)
                domain = args.domain or passport.domain

                if not domain:
                    print("awf-cf-dns-record: no domain available (set --domain or ensure passport.domain is set)", file=sys.stderr)
                    return 1

                record = create_dns_record(
                    cf,
                    domain,
                    args.type,
                    args.name,
                    args.content,
                    proxied=proxied,
                )

                key = f"{args.type}:{args.name}"
                existing_cloudflare = dict(passport.cloudflare) if isinstance(passport.cloudflare, dict) else {}

                if existing_cloudflare.get(key) == record.id:
                    action = "skip"
                else:
                    existing_cloudflare[key] = record.id
                    passport.cloudflare = existing_cloudflare
                    passport.save(root)
                    action = "created"

    except RuntimeError as exc:
        print(f"awf-cf-dns-record: API error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        # Catch Cloudflare SDK errors and other unexpected errors
        err_str = str(exc)
        if "missing" in err_str.lower() or "credentials" in err_str.lower() or "token" in err_str.lower():
            print(f"awf-cf-dns-record: credentials error: {exc}", file=sys.stderr)
            return 2
        print(f"awf-cf-dns-record: error: {exc}", file=sys.stderr)
        return 3

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "record_id": record.id,
            "type": args.type,
            "name": args.name,
            "content": args.content,
            "domain": domain,
        }))
    else:
        print(f"record_id: {record.id}")
        print(f"record: {args.type} {args.name} -> {args.content}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
