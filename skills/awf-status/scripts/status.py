#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cloudflare>=3",
#   "requests>=2.31",
#   "google-auth>=2.27",
#   "google-auth-oauthlib>=1.2",
#   "google-api-python-client>=2.120",
# ]
# ///

"""awf-status — query upstream APIs for the live state of a project.

Read-only. Per A7, we don't trust local state for anything we can ask
the API about. The only local read is `passport.json` for the canonical
domain (which is config, not state).

Coverage at this step:
  - Cloudflare: zone, Pages project, project domain, apex CNAME,
    www A record, always_use_https, bulk-redirect entry.
  - Fathom: TODO (lib/fathom/ not yet ported).
  - GSC: TODO (lib/gsc/ not yet ported).
"""

from __future__ import annotations

import json as jsonlib
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# ── Bootstrap ───────────────────────────────────────────────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME / "lib"))

from project import find_project_root  # noqa: E402
from passport import Passport  # noqa: E402
from slug import domain_to_project_name, normalize_domain  # noqa: E402
from cf.client import get_client as get_cf_client  # noqa: E402
from cf.zones import get_zone  # noqa: E402
from cf.dns import list_dns_records  # noqa: E402
from cf.pages import (  # noqa: E402
    search_pages_project,
    list_project_domains,
)
from cf.bulk_redirects import list_bulk_redirect_lists, search_list_for_url  # noqa: E402
from fathom.client import get_client as get_fathom_client, FathomError  # noqa: E402
from gsc.sites import get_site as gsc_get_site, list_sitemaps, sc_domain  # noqa: E402
from gsc.auth import GscError, find_token_path  # noqa: E402


OK, MISSING, ERROR, TODO = "ok", "missing", "error", "todo"


@dataclass
class Result:
    section: str
    name: str
    status: str  # OK | MISSING | ERROR | TODO
    detail: str = ""


# ── Cloudflare checks ───────────────────────────────────────────────────────


def _safe(fn, *args, **kwargs):
    """Return (value, error_str). The CF SDK raises on auth/network errors;
    we surface them per-check rather than letting the script exit early."""
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, str(e)[:160]


def check_cloudflare(domain: str, results: list[Result]) -> None:
    section = "cloudflare"
    try:
        client = get_cf_client()
    except Exception as e:
        results.append(Result(section, "credentials", ERROR, str(e)[:160]))
        return

    account_id = client.config.account_id
    project_name = domain_to_project_name(domain)

    # Zone
    zone, err = _safe(get_zone, client, domain)
    if err:
        results.append(Result(section, "zone", ERROR, err))
        return
    if zone is None:
        results.append(Result(section, "zone", MISSING, "no zone for this domain"))
        # Without a zone, the rest can't be evaluated.
        for n in ("apex_cname", "www_record", "always_use_https"):
            results.append(Result(section, n, MISSING, "no zone"))
    else:
        ns = ", ".join(getattr(zone, "name_servers", []) or []) or "(none reported)"
        results.append(Result(section, "zone", OK, f"id={zone.id} ns={ns}"))

        # DNS records (one list call → multiple checks)
        records, err = _safe(list_dns_records, client, zone.id)
        if err:
            for n in ("apex_cname", "www_record"):
                results.append(Result(section, n, ERROR, err))
        else:
            apex_cname = next(
                (r for r in records
                 if r.type == "CNAME" and r.name == domain
                 and r.content == f"{project_name}.pages.dev"),
                None,
            )
            results.append(Result(
                section, "apex_cname",
                OK if apex_cname else MISSING,
                f"-> {project_name}.pages.dev" if apex_cname
                else f"expected CNAME {domain} -> {project_name}.pages.dev",
            ))
            www = next(
                (r for r in records
                 if r.type == "A" and r.name == f"www.{domain}"),
                None,
            )
            results.append(Result(
                section, "www_record",
                OK if www else MISSING,
                f"www -> {www.content}" if www else "expected A record on www",
            ))

        # always_use_https
        try:
            setting = client.client.zones.settings.get(
                setting_id="always_use_https", zone_id=zone.id
            )
            results.append(Result(
                section, "always_use_https",
                OK if setting.value == "on" else MISSING,
                f"value={setting.value}",
            ))
        except Exception as e:
            results.append(Result(section, "always_use_https", ERROR, str(e)[:160]))

    # Pages project (independent of zone)
    project, err = _safe(search_pages_project, client, account_id, project_name)
    if err:
        results.append(Result(section, "pages_project", ERROR, err))
        results.append(Result(section, "pages_domain", ERROR, err))
    elif project is None:
        results.append(Result(section, "pages_project", MISSING,
                              f"no Pages project named {project_name!r}"))
        results.append(Result(section, "pages_domain", MISSING, "no project"))
    else:
        results.append(Result(section, "pages_project", OK, f"name={project.name}"))
        domains, err = _safe(list_project_domains, client, account_id, project_name)
        if err:
            results.append(Result(section, "pages_domain", ERROR, err))
        else:
            attached = next((d for d in domains if d.name == domain), None)
            results.append(Result(
                section, "pages_domain",
                OK if attached else MISSING,
                f"{domain} attached" if attached else f"{domain} not attached",
            ))

    # Bulk redirect www -> apex
    lists, err = _safe(list_bulk_redirect_lists, client, account_id)
    if err:
        results.append(Result(section, "bulk_redirect_www", ERROR, err))
    else:
        results_list = list(lists.result)
        if not results_list:
            results.append(Result(section, "bulk_redirect_www", MISSING,
                                  "no bulk-redirect lists on this account"))
        else:
            found = False
            for lst in results_list:
                item, err = _safe(
                    search_list_for_url, client, account_id, lst.id, f"www.{domain}"
                )
                if err:
                    continue
                if item:
                    found = True
                    break
            results.append(Result(
                section, "bulk_redirect_www",
                OK if found else MISSING,
                f"www.{domain} -> https://{domain}",
            ))


# ── Stubs for not-yet-ported services ───────────────────────────────────────


def check_fathom(domain: str, results: list[Result], passport_site_id: str | None) -> None:
    section = "fathom"
    try:
        client = get_fathom_client()
    except Exception as e:
        results.append(Result(section, "credentials", ERROR, str(e)[:160]))
        return

    if passport_site_id:
        try:
            if client.site_exists(passport_site_id):
                results.append(Result(
                    section, "site", OK,
                    f"id={passport_site_id} (from passport, live)",
                ))
            else:
                results.append(Result(
                    section, "site", MISSING,
                    f"passport has id={passport_site_id} but Fathom returns 404 (drift)",
                ))
        except FathomError as e:
            results.append(Result(section, "site", ERROR, str(e)[:160]))
        return

    # No id in passport → see if a site for this domain exists anyway.
    try:
        site = client.search_sites(domain)
    except FathomError as e:
        results.append(Result(section, "site", ERROR, str(e)[:160]))
        return
    if site:
        results.append(Result(
            section, "site", MISSING,
            f"Fathom site exists (id={site.get('id')}) but passport has no fathom_site_id — run awf-setup-analytics",
        ))
    else:
        results.append(Result(section, "site", MISSING, "no Fathom site for this domain"))


def check_gsc(
    domain: str,
    results: list[Result],
    *,
    project_root: Path | None,
    awf_home: Path,
    expected_sitemap: str,
) -> None:
    section = "gsc"

    # If there's no cached token, treat as missing rather than triggering
    # an interactive OAuth pop-up from a status check.
    tok = find_token_path(project_root, awf_home)
    if tok is None:
        results.append(Result(
            section, "auth", MISSING,
            "no cached token — run awf-setup-gsc to authenticate",
        ))
        results.append(Result(section, "verified", MISSING, "no auth"))
        results.append(Result(section, "sitemap", MISSING, "no auth"))
        return

    try:
        info = gsc_get_site(
            domain,
            project_root=project_root,
            awf_home=awf_home,
        )
    except GscError as e:
        results.append(Result(section, "auth", ERROR, str(e)[:160]))
        return

    if info is None:
        results.append(Result(
            section, "verified", MISSING,
            f"property {sc_domain(domain)} not on this account",
        ))
        results.append(Result(section, "sitemap", MISSING, "no property"))
        return

    perm = info.get("permissionLevel")
    if perm == "siteOwner":
        results.append(Result(section, "verified", OK,
                              f"{sc_domain(domain)} ({perm})"))
    else:
        results.append(Result(
            section, "verified", MISSING,
            f"{sc_domain(domain)} permission={perm!r} (expected siteOwner)",
        ))

    # Sitemap
    try:
        sitemaps = list_sitemaps(
            domain, project_root=project_root, awf_home=awf_home,
        )
    except GscError as e:
        results.append(Result(section, "sitemap", ERROR, str(e)[:160]))
        return

    submitted = next(
        (s for s in sitemaps if s.get("path") == expected_sitemap),
        None,
    )
    if submitted is not None:
        last = submitted.get("lastSubmitted") or "(unknown)"
        results.append(Result(
            section, "sitemap", OK,
            f"{expected_sitemap} (lastSubmitted={last})",
        ))
    elif sitemaps:
        names = ", ".join(s.get("path", "?") for s in sitemaps)
        results.append(Result(
            section, "sitemap", MISSING,
            f"have sitemaps {names} but not {expected_sitemap}",
        ))
    else:
        results.append(Result(section, "sitemap", MISSING, "no sitemaps submitted"))


# ── Output ──────────────────────────────────────────────────────────────────

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[0m"
)

ICONS = {
    OK:      ("[OK]    ", GREEN),
    MISSING: ("[MISS]  ", YELLOW),
    ERROR:   ("[ERR]   ", RED),
    TODO:    ("[TODO]  ", DIM),
}


def render_human(domain: str, results: list[Result], *, color: bool) -> str:
    lines: list[str] = []
    lines.append(f"awf-status — {domain}")
    lines.append("─" * 60)

    by_section: dict[str, list[Result]] = {}
    for r in results:
        by_section.setdefault(r.section, []).append(r)

    for section in ("cloudflare", "fathom", "gsc"):
        if section not in by_section:
            continue
        lines.append(section)
        for r in by_section[section]:
            icon, c = ICONS[r.status]
            tag = f"{c}{icon}{RESET}" if color else icon
            line = f"  {tag}{r.name}"
            if r.detail:
                line += f"  — {r.detail}"
            lines.append(line)
        lines.append("")

    missing = sum(1 for r in results if r.status == MISSING)
    errors = sum(1 for r in results if r.status == ERROR)
    if errors:
        lines.append(f"{RED if color else ''}{errors} error(s); {missing} missing.{RESET if color else ''}")
    elif missing:
        lines.append(f"{YELLOW if color else ''}{missing} step(s) outstanding.{RESET if color else ''}")
    else:
        lines.append(f"{GREEN if color else ''}All checked steps complete.{RESET if color else ''}")
    return "\n".join(lines)


def render_json(domain: str, results: list[Result]) -> str:
    return jsonlib.dumps(
        {
            "domain": domain,
            "checks": [asdict(r) for r in results],
            "missing": [f"{r.section}.{r.name}" for r in results if r.status == MISSING],
            "errors":  [f"{r.section}.{r.name}" for r in results if r.status == ERROR],
        },
        indent=2,
    )


# ── Entry ───────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> tuple[str | None, bool]:
    domain = None
    as_json = False
    args = argv[1:]
    while args:
        a = args.pop(0)
        if a == "--json":
            as_json = True
        elif a == "--domain":
            if not args:
                raise SystemExit("--domain requires a value")
            domain = args.pop(0)
        else:
            raise SystemExit(f"unknown argument: {a}")
    return domain, as_json


def resolve_context(override: str | None) -> tuple[str, str | None]:
    """Return (domain, fathom_site_id_from_passport_or_None)."""
    if override:
        return normalize_domain(override), None
    root = find_project_root(optional=True)
    if root is None:
        raise SystemExit(
            "Not inside a project (no passport.json found). "
            "Pass --domain <domain> or cd into a project."
        )
    p = Passport.load(root)
    return normalize_domain(p.domain), (p.fathom_site_id or None)


def main(argv: list[str]) -> int:
    domain_override, as_json = parse_args(argv)
    domain, passport_site_id = resolve_context(domain_override)

    project_root = find_project_root(optional=True)
    expected_sitemap = f"https://{domain}/sitemap.xml"

    results: list[Result] = []
    check_cloudflare(domain, results)
    check_fathom(domain, results, passport_site_id)
    check_gsc(
        domain, results,
        project_root=project_root,
        awf_home=AWF_HOME,
        expected_sitemap=expected_sitemap,
    )

    out = (
        render_json(domain, results)
        if as_json
        else render_human(domain, results, color=sys.stdout.isatty())
    )
    print(out)
    return 1 if any(r.status == ERROR for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
