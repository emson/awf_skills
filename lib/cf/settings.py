"""Per-zone settings.

Currently just `always_use_https`. Add new settings here as needed; do
not scatter `client.client.zones.settings.*` calls across skills.
"""

from __future__ import annotations

from .client import CloudflareClient
from .zones import get_zone


def set_always_use_https(
    client: CloudflareClient,
    domain_name: str,
    *,
    value: str = "on",
):
    """Idempotent: skip the edit when the setting is already `value`."""
    zone = get_zone(client, domain_name)
    if zone is None:
        raise RuntimeError(
            f"No zone for {domain_name}. Run awf-setup-domain first."
        )
    zone_id = zone.id

    setting = client.client.zones.settings.get(
        setting_id="always_use_https",
        zone_id=zone_id,
    )
    if setting.value == value:
        print(f"- always_use_https already '{value}'")
        return setting

    response = client.client.zones.settings.edit(
        zone_id=zone_id,
        setting_id="always_use_https",
        value=value,
    )
    print(f"- always_use_https set to '{value}'")
    return response
