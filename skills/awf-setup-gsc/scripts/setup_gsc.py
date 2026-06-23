#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cloudflare>=3",
#   "google-auth>=2.27",
#   "google-auth-oauthlib>=1.2",
#   "google-api-python-client>=2.120",
# ]
# ///

"""awf-setup-gsc — add the property to Google Search Console and write
the TXT verification record on Cloudflare.

Idempotent (A5):
  - existing GSC property + verified  → no-op + report
  - existing GSC property + unverified → fetch token, ensure TXT record
  - no GSC property                    → add it, then fetch token + TXT

The TXT record is created with `proxied=False` (TXT records cannot be
proxied through Cloudflare). `lib/cf/dns.create_dns_record` is
search-or-create on `(type, content)` so re-runs reuse the existing
record.

Verification itself happens in `awf-verify-gsc` after DNS propagation.
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
from cf.client import get_client as get_cf_client  # noqa: E402
from cf.dns import create_dns_record  # noqa: E402
from gsc.sites import (  # noqa: E402
    get_or_add_site,
    is_verified,
    get_txt_verification_token,
    sc_domain,
)
from gsc.auth import GscError, find_token_path  # noqa: E402


GREEN, YELLOW, RESET = "\033[92m", "\033[93m", "\033[0m"


def _color(s: str, c: str) -> str:
    return f"{c}{s}{RESET}" if sys.stdout.isatty() else s


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: setup_gsc.py", file=sys.stderr)
        return 2

    try:
        project_root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    passport = Passport.load(project_root)
    domain = normalize_domain(passport.domain)

    with log.session(composer="awf-setup-gsc", target="landing", start=project_root):
        with log.invoke(skill="awf-setup-gsc", args={"domain": domain}):
            # Surface the resolved token path so the user can inspect/clear it.
            tok = find_token_path(project_root, AWF_HOME)
            if tok:
                print(f"- using cached Google token: {tok}")
            else:
                print(
                    "- no cached token found; OAuth will run in-browser on first call.\n"
                    "  (Once authed, token.json lands next to passport.json by default.)"
                )

            try:
                info = get_or_add_site(domain, project_root=project_root, awf_home=AWF_HOME)
            except GscError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1

            print(f"- GSC property: {sc_domain(domain)}")
            print(f"  permissionLevel: {info.get('permissionLevel')!r}")

            if is_verified(info):
                print(_color("- already verified; nothing to do", GREEN))
                passport.mark_gate("gsc_setup", meta={"outcome": "noop", "verified": True})
                passport.save(project_root)
                return 0

            # Need a TXT record. Get the token from Site Verification.
            try:
                token = get_txt_verification_token(
                    domain, project_root=project_root, awf_home=AWF_HOME,
                )
            except GscError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            print(f"- verification token: {token}")

            # Write the TXT record on Cloudflare (proxied=False — TXT can't be
            # proxied; CF will reject proxied=True for non-A/AAAA/CNAME types).
            try:
                cf = get_cf_client(project_root=project_root, awf_home=AWF_HOME)
            except RuntimeError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1

            try:
                create_dns_record(
                    cf, domain,
                    record_type="TXT",
                    name=domain,
                    content=token,
                    proxied=False,
                )
            except Exception as e:
                print(f"error creating TXT record: {e}", file=sys.stderr)
                return 1

            passport.mark_gate(
                "gsc_setup",
                meta={"outcome": "txt_record_set", "token": token, "verified": False},
            )
            passport.save(project_root)

            log.gate(
                name="gsc_dns_propagation",
                reason="TXT record written; DNS must propagate before GSC can verify ownership.",
                instructions=(
                    "Wait a few minutes for DNS propagation, "
                    "then run `awf-verify-gsc` to claim verification."
                ),
            )

            print()
            print(_color("- TXT record created. Wait a few minutes for DNS propagation,", GREEN))
            print(_color(f"  then run `awf-verify-gsc` to claim verification.", GREEN))
            return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
