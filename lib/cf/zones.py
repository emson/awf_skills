"""Zone (domain) operations.

Idempotent search-or-create. Returns the SDK's zone object (Pydantic
model) — callers access fields with `zone.id`, `zone.name`,
`zone.name_servers`, etc.
"""

from __future__ import annotations

from typing import Optional

from cloudflare import BadRequestError

from .client import CloudflareClient


def list_zones(client: CloudflareClient, *, name: Optional[str] = None) -> list:
    """List all zones, optionally filtered to a single domain name."""
    response = client.client.zones.list(name=name)
    return list(response.result)


def get_zone(client: CloudflareClient, domain_name: str):
    """Return the zone for `domain_name`, or `None` if not found."""
    zones = list_zones(client, name=domain_name)
    return zones[0] if zones else None


def create_zone(client: CloudflareClient, domain_name: str):
    """Create a `full` zone for `domain_name` on the configured account."""
    try:
        return client.client.zones.create(
            account={"id": client.config.account_id},
            name=domain_name,
            type="full",
        )
    except BadRequestError as e:
        # CF returns 400 when the zone already exists on this or another
        # account. Re-fetch from our account; if absent, the zone belongs
        # to someone else and we re-raise (A13).
        existing = get_zone(client, domain_name)
        if existing:
            return existing
        raise RuntimeError(
            f"Failed to create zone for {domain_name}: {e}. "
            "The domain may already be claimed on a different Cloudflare account."
        ) from e


def get_or_create_zone(client: CloudflareClient, domain_name: str):
    """Idempotent: return the existing zone, or create one."""
    zone = get_zone(client, domain_name)
    if zone:
        print(f"- zone for {domain_name} already exists")
        return zone
    print(f"- creating zone for {domain_name}")
    return create_zone(client, domain_name)
