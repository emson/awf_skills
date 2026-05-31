"""Zone rulesets — dynamic redirects.

Not currently exercised on the launch path (the bulk-redirects list
handles `www → apex`), but ported for parity with the legacy code in
case a future skill needs zone-level redirect rules.

`enable_http_to_https` is redundant with `always_use_https` (settings.py)
in nearly all cases — Cloudflare's HSTS/HTTPS rules are simpler. Prefer
`set_always_use_https` for that need.
"""

from __future__ import annotations

from .client import CloudflareClient
from .zones import get_zone


def _zone_id(client: CloudflareClient, domain_name: str) -> str:
    zone = get_zone(client, domain_name)
    if zone is None:
        raise RuntimeError(f"No zone for {domain_name}. Run awf-setup-domain first.")
    return zone.id


def create_www_redirect_ruleset(client: CloudflareClient, domain_name: str):
    """Zone-scoped 301 from `www.<domain>` to `<domain>` preserving path."""
    zone_id = _zone_id(client, domain_name)

    rule = {
        "expression": f'http.host eq "www.{domain_name}"',
        "description": f"Redirect www.{domain_name} to {domain_name}",
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "target_url": {
                    "expression": f'concat("https://{domain_name}", http.request.uri.path)'
                },
                "status_code": 301,
                "preserve_query_string": True,
            }
        },
    }

    ruleset = client.client.rulesets.create(
        zone_id=zone_id,
        name="Redirect www to root",
        kind="zone",
        phase="http_request_dynamic_redirect",
        rules=[rule],
    )
    print(f"- created www-redirect ruleset: {ruleset.id}")
    return ruleset


def enable_http_to_https(client: CloudflareClient, domain_name: str):
    """Append an HTTP → HTTPS redirect rule to the existing dynamic-redirect
    ruleset on the zone. Prefer `set_always_use_https` instead.
    """
    zone_id = _zone_id(client, domain_name)

    existing = client.client.rulesets.list(zone_id=zone_id)
    dynamic = next(
        (r for r in existing if r.phase == "http_request_dynamic_redirect"),
        None,
    )
    if not dynamic:
        raise RuntimeError(
            "No http_request_dynamic_redirect ruleset on this zone — "
            "create one first."
        )

    rule = {
        "expression": 'http.request.full_uri starts_with "http://"',
        "description": "Redirect HTTP to HTTPS",
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "target_url": {
                    "expression": 'concat("https://", http.host, http.request.uri.path)'
                },
                "status_code": 301,
                "preserve_query_string": True,
            }
        },
    }

    client.client.rulesets.create(
        zone_id=zone_id,
        name=dynamic.name,
        kind="zone",
        phase="http_request_dynamic_redirect",
        rules=[rule],
    )
    print("- added HTTP→HTTPS redirect rule")
