"""Fathom Analytics API client.

The public surface mirrors the legacy `tools/fathom/fathom_client.py`
plus an idempotent `get_or_create_site` helper that the
`awf-setup-analytics` script and `awf-status` Fathom check both consume.

Notes on Fathom's API quirks (preserved here so callers don't have to
guess):

- `GET /v1/sites` returns `{"data": [...sites]}`.
- `GET /v1/sites/<id>` returns the site object directly (no wrapping).
- `POST /v1/sites` returns the created site object directly.
- `search_sites` does a case-insensitive *substring* match on
  `site.name`. The convention in this suite is `site.name == domain`,
  so the substring fallback is benign — but it does mean a pre-existing
  site named "example.com Marketing" will be matched for domain
  "example.com".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests


DEFAULT_BASE_URL = "https://api.usefathom.com/v1"
DEFAULT_TIMEOUT = 15.0  # seconds


@dataclass(frozen=True)
class FathomConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL


class FathomError(RuntimeError):
    """Surfaces upstream HTTP errors verbatim (A13)."""


class FathomClient:
    def __init__(self, config: FathomConfig):
        self.config = config
        self._headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }

    def _check(self, r: requests.Response, what: str) -> dict[str, Any]:
        if not r.ok:
            raise FathomError(f"{what}: {r.status_code} {r.text[:400]}")
        return r.json()

    # ── Sites ──────────────────────────────────────────────────────────

    def list_sites(self) -> list[dict[str, Any]]:
        r = requests.get(
            f"{self.config.base_url}/sites",
            headers=self._headers,
            timeout=DEFAULT_TIMEOUT,
        )
        body = self._check(r, "list sites")
        return body.get("data") or []

    def get_site(self, site_id: str) -> dict[str, Any]:
        r = requests.get(
            f"{self.config.base_url}/sites/{site_id}",
            headers=self._headers,
            timeout=DEFAULT_TIMEOUT,
        )
        return self._check(r, f"get site {site_id}")

    def site_exists(self, site_id: str) -> bool:
        """Cheap existence check — tolerates a 404 without raising."""
        r = requests.get(
            f"{self.config.base_url}/sites/{site_id}",
            headers=self._headers,
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 404:
            return False
        if not r.ok:
            raise FathomError(f"get site {site_id}: {r.status_code} {r.text[:400]}")
        return True

    def search_sites(self, domain: str) -> Optional[dict[str, Any]]:
        """Case-insensitive substring match on `site.name`. See module
        docstring for the caveat."""
        needle = domain.lower()
        for site in self.list_sites():
            name = (site.get("name") or "").lower()
            if needle in name:
                return site
        return None

    def create_site(self, name: str) -> dict[str, Any]:
        r = requests.post(
            f"{self.config.base_url}/sites",
            headers=self._headers,
            json={"name": name},
            timeout=DEFAULT_TIMEOUT,
        )
        return self._check(r, f"create site {name!r}")


# ── Factory ────────────────────────────────────────────────────────────


def get_client(
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> FathomClient:
    from config import Config  # local import; same pattern as cf/client.py

    if awf_home is None:
        try:
            from awf_home import find_awf_home
            awf_home = find_awf_home()
        except Exception:
            awf_home = None

    cfg = Config.layered(project_root=project_root, awf_home=awf_home)
    api_key = cfg.get("FATHOM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Fathom credentials missing: FATHOM_API_KEY. "
            "Run awf-init, then awf-doctor."
        )
    return FathomClient(FathomConfig(api_key=api_key))


# ── Idempotent helper ──────────────────────────────────────────────────


def get_or_create_site(client: FathomClient, domain: str) -> dict[str, Any]:
    """Search-or-create. Returns the site object (with `id` and `name`).

    Convention: new sites are named exactly `domain`. Existing sites
    that match by substring (legacy behaviour) are reused.
    """
    site = client.search_sites(domain)
    if site:
        print(f"- Fathom site exists: {site.get('name')} (id={site.get('id')})")
        return site
    print(f"- creating Fathom site for {domain}")
    return client.create_site(domain)
