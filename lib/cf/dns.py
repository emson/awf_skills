"""DNS record operations on a zone.

`create_dns_record` is search-or-create on `(type, content)` — re-running
returns the existing record without an extra side effect.
"""

from __future__ import annotations

from typing import Optional

from .client import CloudflareClient
from .zones import get_zone


def list_dns_records(client: CloudflareClient, zone_id: str) -> list:
    response = client.client.dns.records.list(zone_id=zone_id)
    return list(response.result)


def search_dns_record(
    client: CloudflareClient,
    zone_id: str,
    record_type: str,
    content: str,
) -> Optional[object]:
    """Find an existing record matching `(type, content)`."""
    for record in list_dns_records(client, zone_id):
        if record.type == record_type and record.content == content:
            return record
    return None


def create_dns_record(
    client: CloudflareClient,
    domain_name: str,
    record_type: str,
    name: str,
    content: str,
    *,
    proxied: bool = True,
):
    """Idempotent. Returns the (existing or newly-created) record."""
    zone = get_zone(client, domain_name)
    if zone is None:
        raise RuntimeError(
            f"No zone for {domain_name}. Run awf-setup-domain first."
        )
    zone_id = zone.id

    existing = search_dns_record(client, zone_id, record_type, content)
    if existing:
        print(f"- DNS record {record_type} {name} -> {content} already exists")
        return existing

    record = client.client.dns.records.create(
        zone_id=zone_id,
        type=record_type,
        name=name,
        content=content,
        proxied=proxied,
        ttl=1,  # auto TTL when proxied, 1 when not
    )
    print(f"- created DNS record {record_type} {name} -> {content}")
    return record


def create_www_to_apex_redirect_record(client: CloudflareClient, domain_name: str):
    """Proxied placeholder A record on `www`.

    The actual redirect is performed by the bulk-redirects ruleset
    (`lib/cf/bulk_redirects.py`); this record exists so DNS resolves
    `www` to *some* IP that Cloudflare can intercept and rewrite.
    `192.0.2.1` is reserved (TEST-NET-1) and never reachable directly,
    which is intentional — only the proxied path works.
    """
    return create_dns_record(
        client, domain_name, "A", "www", "192.0.2.1", proxied=True
    )
