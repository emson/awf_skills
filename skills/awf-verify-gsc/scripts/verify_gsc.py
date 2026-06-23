#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-auth>=2.27",
#   "google-auth-oauthlib>=1.2",
#   "google-api-python-client>=2.120",
# ]
# ///

"""awf-verify-gsc — verify the GSC property (now that the TXT record
has propagated) and submit the sitemap.

Idempotent: verifying a verified property is a no-op upstream;
submitting an already-submitted sitemap is harmless.

Common failure: the TXT record hasn't propagated yet — Google's
verifier returns `Failed to verify the site`. We catch that case and
suggest waiting + retrying rather than abort the launch.
"""

from __future__ import annotations

import os
import sys
import time
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
from gsc.sites import (  # noqa: E402
    get_or_add_site,
    is_verified,
    verify_site,
    submit_sitemap,
    sc_domain,
)
from gsc.auth import GscError  # noqa: E402


GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def _color(s: str, c: str) -> str:
    return f"{c}{s}{RESET}" if sys.stdout.isatty() else s


def parse_args(argv: list[str]) -> dict:
    args = argv[1:]
    out = {"sitemap_path": "/sitemap.xml"}
    while args:
        a = args.pop(0)
        if a == "--sitemap":
            out["sitemap_path"] = args.pop(0) if args else "/sitemap.xml"
        else:
            print(f"unknown argument: {a}", file=sys.stderr)
            print("usage: verify_gsc.py [--sitemap <path>]", file=sys.stderr)
            sys.exit(2)
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

    safe_args = {"sitemap_path": opts["sitemap_path"]}

    with log.session(composer="awf-verify-gsc", target="landing", start=project_root):
        with log.invoke(skill="awf-verify-gsc", args=safe_args):
            # 1. Confirm the property exists; bail if not.
            try:
                info = get_or_add_site(domain, project_root=project_root, awf_home=AWF_HOME)
            except GscError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1

            # 2. Verify (no-op if already verified).
            if is_verified(info):
                print(_color(f"- {sc_domain(domain)} is already verified", GREEN))
            else:
                try:
                    resp = verify_site(domain, project_root=project_root, awf_home=AWF_HOME)
                    print(_color(f"- verification response: {resp.get('id', resp)}", GREEN))
                except GscError as e:
                    msg = str(e)
                    print(f"error: {msg}", file=sys.stderr)
                    if "Failed to verify" in msg or "verification" in msg.lower():
                        print(
                            "\nThis usually means the TXT record hasn't propagated yet.\n"
                            "Wait 2–5 minutes and re-run `awf-verify-gsc`.",
                            file=sys.stderr,
                        )
                    return 1

            # Brief pause before submitting sitemap — gives Google time to flip
            # the property's verified state internally so the sitemap submit
            # doesn't bounce.
            time.sleep(3)

            sitemap_url = f"https://{domain}{opts['sitemap_path']}"
            try:
                submit_sitemap(
                    domain, sitemap_url,
                    project_root=project_root, awf_home=AWF_HOME,
                )
            except GscError as e:
                print(f"error submitting sitemap: {e}", file=sys.stderr)
                # We *did* verify — record that partial success.
                passport.mark_gate(
                    "gsc_verify",
                    meta={"verified": True, "sitemap": "failed", "error": str(e)[:200]},
                )
                log.gate(
                    name="gsc_verify",
                    reason="GSC property verified but sitemap submission failed; partial success recorded.",
                    instructions="Check the sitemap URL is accessible, then re-run `awf-verify-gsc`.",
                )
                passport.save(project_root)
                return 1

            print(_color(f"- submitted sitemap: {sitemap_url}", GREEN))
            passport.mark_gate(
                "gsc_verify",
                meta={"verified": True, "sitemap": sitemap_url, "outcome": "ok"},
            )
            log.gate(
                name="gsc_verify",
                reason="GSC property verified and sitemap submitted successfully.",
                instructions="",
            )
            passport.save(project_root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
