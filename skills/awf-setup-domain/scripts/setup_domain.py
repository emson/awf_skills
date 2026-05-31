#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cloudflare>=3"]
# ///

"""awf-setup-domain — establish all Cloudflare-side resources for the
project's domain.

Each step is search-or-create in `lib/cf/`, so the whole pipeline is
idempotent (A5). On success, stashes the zone's nameservers in
`passport.launch.gates.domain_setup.meta.nameservers` so
`awf-setup-nameservers` can consume them without re-querying.
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
from slug import normalize_domain, domain_to_project_name  # noqa: E402
from cf.client import get_client  # noqa: E402
from cf.zones import get_or_create_zone  # noqa: E402
from cf.dns import create_dns_record, create_www_to_apex_redirect_record  # noqa: E402
from cf.pages import (  # noqa: E402
    search_or_create_pages_project,
    get_or_create_project_domain,
)
from cf.settings import set_always_use_https  # noqa: E402
from cf.bulk_redirects import search_and_create_bulk_redirect  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: setup_domain.py", file=sys.stderr)
        return 2

    try:
        project_root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    passport = Passport.load(project_root)
    domain = normalize_domain(passport.domain)
    project_name = domain_to_project_name(domain)

    try:
        cf = get_client(project_root=project_root, awf_home=AWF_HOME)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    account_id = cf.config.account_id

    try:
        # 1. Zone
        zone = get_or_create_zone(cf, domain)

        # 2. Pages project + custom domain
        search_or_create_pages_project(cf, account_id, domain)
        get_or_create_project_domain(cf, account_id, project_name, domain)

        # 3. DNS records
        create_dns_record(
            cf, domain, "CNAME", domain, f"{project_name}.pages.dev",
            proxied=True,
        )
        create_www_to_apex_redirect_record(cf, domain)

        # 4. Force HTTPS
        set_always_use_https(cf, domain)

        # 5. Bulk redirect www → apex
        search_and_create_bulk_redirect(
            cf, f"www.{domain}", f"https://{domain}",
        )
    except Exception as e:
        # Surface verbatim (A13). Do not partially mark the gate.
        print(f"error: {e}", file=sys.stderr)
        return 1

    nameservers = list(getattr(zone, "name_servers", []) or [])
    passport.mark_gate(
        "domain_setup",
        meta={
            "zone_id": getattr(zone, "id", None),
            "nameservers": nameservers,
            "outcome": "ok",
        },
    )
    passport.save(project_root)

    print()
    if nameservers:
        print(f"- Cloudflare nameservers for {domain}:")
        for ns in nameservers:
            print(f"    {ns}")
        print("\nNext: `awf-setup-nameservers` will pick these up automatically.")
    else:
        print("- WARN: Cloudflare did not report nameservers; check the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
