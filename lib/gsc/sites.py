"""Search Console + Site Verification operations.

GSC properties for a domain are addressed as `sc-domain:<domain>` (the
"Domain" property type — DNS-verified, covers all subdomains and both
schemes). This module enforces that prefix; callers pass plain domains.

All methods raise `GscError` (defined in `gsc.auth`) on API failure;
search-or-create patterns swallow only the specific errors that mean
"already exists" and re-fetch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from googleapiclient.errors import HttpError

from .auth import GscError, get_gsc_service, get_site_verification_service


# ── Helpers ────────────────────────────────────────────────────────────


def sc_domain(domain: str) -> str:
    """Idempotent: returns `sc-domain:<domain>` regardless of input."""
    d = domain.strip()
    if d.startswith("sc-domain:"):
        return d
    return f"sc-domain:{d}"


def _strip_prefix(d: str) -> str:
    return d.removeprefix("sc-domain:") if d.startswith("sc-domain:") else d


# ── Sites ──────────────────────────────────────────────────────────────


def get_site(
    domain: str,
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> dict | None:
    """Return the GSC site dict for `domain`, or None if the property
    isn't on the authenticated account."""
    service = get_gsc_service(project_root=project_root, awf_home=awf_home)
    site_url = sc_domain(domain)
    try:
        return service.sites().get(siteUrl=site_url).execute()
    except HttpError as e:
        # 404 = site not found = not added on this account.
        if e.resp.status == 404:
            return None
        raise GscError(f"GSC get_site({site_url}): {e}") from e


def add_site(
    domain: str,
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> None:
    """Add `sc-domain:<domain>` as a property on the authenticated
    account. The PUT is idempotent (re-adding is a no-op)."""
    service = get_gsc_service(project_root=project_root, awf_home=awf_home)
    site_url = sc_domain(domain)
    try:
        service.sites().add(siteUrl=site_url).execute()
    except HttpError as e:
        raise GscError(f"GSC add_site({site_url}): {e}") from e


def get_or_add_site(
    domain: str,
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> dict:
    """Idempotent. Returns the site info dict.

    NOTE: a freshly-added property will return `permissionLevel`
    `siteUnverifiedUser` until DNS verification completes — caller must
    not interpret a present-but-unverified site as "done".
    """
    info = get_site(domain, project_root=project_root, awf_home=awf_home)
    if info is not None:
        return info
    print(f"- adding {sc_domain(domain)} to Google Search Console")
    add_site(domain, project_root=project_root, awf_home=awf_home)
    info = get_site(domain, project_root=project_root, awf_home=awf_home)
    if info is None:
        raise GscError(f"GSC: could not read back the property after adding")
    return info


def is_verified(site_info: dict) -> bool:
    return site_info.get("permissionLevel") == "siteOwner"


# ── Verification ───────────────────────────────────────────────────────


def get_txt_verification_token(
    domain: str,
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> str:
    """Return the `google-site-verification=...` token to publish as a
    TXT record at the apex."""
    service = get_site_verification_service(
        project_root=project_root, awf_home=awf_home,
    )
    request = {
        "site": {
            "type": "INET_DOMAIN",
            "identifier": _strip_prefix(domain),
        },
        "verificationMethod": "DNS_TXT",
    }
    try:
        response = service.webResource().getToken(body=request).execute()
    except HttpError as e:
        raise GscError(f"siteVerification getToken: {e}") from e
    token = response.get("token")
    if not token:
        raise GscError(f"siteVerification getToken: response had no token: {response!r}")
    return token


def verify_site(
    domain: str,
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> dict:
    """Trigger verification. Returns the API response dict.

    Common cause of failure: the TXT record hasn't propagated yet. This
    function does NOT wait — caller decides retry policy.
    """
    service = get_site_verification_service(
        project_root=project_root, awf_home=awf_home,
    )
    body = {
        "site": {
            "type": "INET_DOMAIN",
            "identifier": _strip_prefix(domain),
        }
    }
    try:
        return service.webResource().insert(
            verificationMethod="DNS_TXT",
            body=body,
        ).execute()
    except HttpError as e:
        # 400 with "Failed to verify" means TXT not visible yet — bubble
        # up so the script can advise.
        raise GscError(f"siteVerification insert: {e}") from e


# ── Sitemaps ───────────────────────────────────────────────────────────


def list_sitemaps(
    domain: str,
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> list[dict]:
    service = get_gsc_service(project_root=project_root, awf_home=awf_home)
    site_url = sc_domain(domain)
    try:
        response = service.sitemaps().list(siteUrl=site_url).execute()
    except HttpError as e:
        raise GscError(f"GSC list_sitemaps({site_url}): {e}") from e
    return response.get("sitemap", []) or []


def submit_sitemap(
    domain: str,
    feedpath: str,
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> None:
    """Submit `feedpath` as a sitemap for the property. Idempotent
    upstream (re-submitting the same feedpath is harmless)."""
    service = get_gsc_service(project_root=project_root, awf_home=awf_home)
    site_url = sc_domain(domain)
    try:
        service.sitemaps().submit(siteUrl=site_url, feedpath=feedpath).execute()
    except HttpError as e:
        raise GscError(f"GSC submit_sitemap({site_url}, {feedpath}): {e}") from e
