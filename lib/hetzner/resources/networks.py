"""Network resource namespace for HetznerClient.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

import hcloud
from hcloud import Client
from hcloud.networks import Network, NetworkSubnet

from lib import log
from lib.hetzner.errors import api_status, translate


class _Networks:
    """Manage Hetzner private networks with search-or-create semantics."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def get_or_create(
        self,
        name: str,
        *,
        ip_range: str = "10.0.0.0/16",
        subnet_zone: str = "eu-central",
        subnet_range: str = "10.0.0.0/24",
    ) -> Network:
        """Return the named network, creating it (with one subnet) if absent.

        Idempotency: if a network with ``name`` already exists, it is
        returned immediately; no subnet is added (the existing network's
        subnets are untouched). Logs a single ``api.call`` event.

        Args:
            name: Network name.
            ip_range: Top-level CIDR block for the network (default 10.0.0.0/16).
            subnet_zone: Hetzner network zone (default "eu-central").
            subnet_range: Subnet CIDR to create inside the network (default 10.0.0.0/24).

        Returns:
            The existing or newly created Network SDK object.

        Raises:
            HetznerAuthError: Invalid or missing API token.
            HetznerNetworkError: Network-level failure; retryable=True.
            HetznerError: Other API errors.
        """
        existing = self.get(name)
        if existing is not None:
            log.api(
                provider="hetzner",
                method="GET",
                path="/networks",
                status_code=200,
                resource_id=str(existing.id),
            )
            return existing
        # Network absent — create it with subnets inline
        subnet = NetworkSubnet(
            ip_range=subnet_range,
            type="cloud",
            network_zone=subnet_zone,
        )
        try:
            network = self._client.networks.create(
                name=name,
                ip_range=ip_range,
                subnets=[subnet],
            )
            log.api(
                provider="hetzner",
                method="POST",
                path="/networks",
                status_code=201,
                resource_id=str(network.id),
            )
            return network
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/networks",
                status_code=api_status(exc),
            )
            raise translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/networks",
                status_code=0,
            )
            raise translate(exc) from exc

    def get(self, name: str) -> Network | None:
        """Return the named network, or None if not found.

        Args:
            name: Network name.

        Returns:
            Network or None.
        """
        try:
            return self._client.networks.get_by_name(name)
        except hcloud.APIException as exc:
            raise translate(exc) from exc
        except Exception as exc:
            raise translate(exc) from exc

    def delete(self, name: str) -> bool:
        """Delete the named network.

        Idempotency: returns False if the network does not exist; True if
        deleted successfully. Logs one ``api.call`` event.

        Args:
            name: Network name.

        Returns:
            True if deleted, False if not found.

        Raises:
            HetznerAuthError: Invalid or missing API token.
            HetznerNetworkError: Network-level failure; retryable=True.
            HetznerError: Other API errors.
        """
        existing = self.get(name)
        if existing is None:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/networks",
                status_code=404,
            )
            return False
        try:
            self._client.networks.delete(existing)
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/networks",
                status_code=204,
                resource_id=str(existing.id),
            )
            return True
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/networks",
                status_code=api_status(exc),
            )
            raise translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/networks",
                status_code=0,
            )
            raise translate(exc) from exc
