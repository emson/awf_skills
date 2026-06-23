#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31", "cloudflare>=3"]
# ///

"""awf-setup-nameservers — point the Namecheap registrar at Cloudflare's
nameservers for the project's domain.

Idempotent (A5):
  - reads current NS via Namecheap → if they already match the target,
    no-op + report;
  - else calls setCustom, marks the gate.

Source for the target nameservers, in priority order:
  1. `--nameservers ns1.example.com,ns2.example.com` CLI arg.
  2. Live read from Cloudflare (`cf.zones.get_zone(...).name_servers`).
  3. `passport.launch.gates.domain_setup.meta.nameservers` (set by
     `awf-setup-domain` when it runs).
  4. Error.
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
from slug import normalize_domain  # noqa: E402
from namecheap.client import (  # noqa: E402
    get_client as get_nc_client,
    get_or_set_custom_dns,
    NamecheapError,
)


def parse_args(argv: list[str]) -> dict:
    args = argv[1:]
    out: dict = {"nameservers": None}
    while args:
        a = args.pop(0)
        if a == "--nameservers":
            if not args:
                print("--nameservers requires a value", file=sys.stderr)
                sys.exit(2)
            out["nameservers"] = [
                n.strip() for n in args.pop(0).split(",") if n.strip()
            ]
        else:
            print(f"unknown argument: {a}", file=sys.stderr)
            print("usage: set_ns.py [--nameservers ns1,ns2]", file=sys.stderr)
            sys.exit(2)
    return out


def resolve_nameservers(
    *,
    cli_value: list[str] | None,
    project_root: Path,
    passport: Passport,
    domain: str,
) -> list[str]:
    if cli_value:
        print(f"- nameservers from --nameservers: {', '.join(cli_value)}")
        return cli_value

    # Live CF read.
    try:
        from cf.client import get_client as get_cf_client
        from cf.zones import get_zone
        cf = get_cf_client(project_root=project_root, awf_home=AWF_HOME)
        zone = get_zone(cf, domain)
        if zone is not None:
            ns = list(getattr(zone, "name_servers", []) or [])
            if ns:
                print(f"- nameservers from live Cloudflare zone: {', '.join(ns)}")
                return ns
            print("- Cloudflare zone has no name_servers reported; falling back")
        else:
            print(f"- no Cloudflare zone for {domain}; falling back")
    except Exception as e:
        print(f"- could not read from Cloudflare ({e}); falling back to passport")

    # Passport gate.
    gate = passport.launch.gates.get("domain_setup")
    if gate and gate.meta:
        ns = gate.meta.get("nameservers") or []
        if ns:
            print(f"- nameservers from passport.launch.gates.domain_setup: {', '.join(ns)}")
            return list(ns)

    print(
        "error: could not determine target nameservers. Pass --nameservers ns1,ns2, "
        "or run awf-setup-domain first so the Cloudflare zone exists.",
        file=sys.stderr,
    )
    sys.exit(1)


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    try:
        project_root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    passport = Passport.load(project_root)
    domain = normalize_domain(passport.domain)

    safe_args: dict = {"domain": domain}
    if opts["nameservers"]:
        safe_args["nameservers"] = opts["nameservers"]

    with log.session(composer="awf-setup-nameservers", target="landing", start=project_root):
        with log.invoke(skill="awf-setup-nameservers", args=safe_args):
            nameservers = resolve_nameservers(
                cli_value=opts["nameservers"],
                project_root=project_root,
                passport=passport,
                domain=domain,
            )

            try:
                with get_nc_client(project_root=project_root, awf_home=AWF_HOME) as nc:
                    changed, current = get_or_set_custom_dns(nc, domain, nameservers)
            except NamecheapError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1

            passport.mark_gate(
                "nameservers_setup",
                meta={
                    "nameservers": current,
                    "outcome": "updated" if changed else "noop",
                },
            )
            passport.save(project_root)

            if changed:
                log.gate(
                    name="nameservers_propagation",
                    reason=(
                        "Nameservers at Namecheap have been updated to point at "
                        "Cloudflare, but DNS propagation is asynchronous and may "
                        "take up to 48 hours. This is an inherently manual/async step."
                    ),
                    instructions=(
                        "Wait for DNS propagation to complete (typically 1–24 h). "
                        "Check with: dig NS {domain} +short  OR  "
                        "https://dnschecker.org/#NS/{domain} . "
                        "Once the Cloudflare nameservers are visible globally, "
                        "Cloudflare will mark the zone as active automatically."
                    ).format(domain=domain),
                )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
