"""Load balancer resource namespace for HetznerClient.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

from typing import Any, Callable

import hcloud
from hcloud import Client
from hcloud.load_balancer_types import LoadBalancerType
from hcloud.load_balancers import (
    LoadBalancer,
    LoadBalancerService,
    LoadBalancerTarget,
)
from hcloud.locations import Location
from hcloud.servers import Server

from lib.hetzner.errors import HetznerNotFound, translate
from lib.hetzner.actions import wait_for_action


class _LoadBalancers:
    """Manage Hetzner load balancers with search-or-create semantics."""

    def __init__(
        self,
        client: Client,
        caller: Callable[..., object],
        servers_getter: Callable[[str], Server | None],
    ) -> None:
        self._client = client
        self._call = caller
        self._servers_getter = servers_getter

    def get_or_create(
        self,
        name: str,
        *,
        type: str = "lb11",
        location: str = "fsn1",
        targets: list[str] | None = None,
        health_check: dict[str, Any] | None = None,
        services: list[LoadBalancerService] | None = None,
    ) -> LoadBalancer:
        """Return the named load balancer, creating it if absent.

        Idempotency: if an LB with ``name`` already exists, it is returned
        immediately; no targets or services are modified (no drift detection
        in this plan). Logs a single ``api.call`` event.

        Args:
            name: Load balancer name.
            type: LB type slug (e.g. "lb11", "lb21").
            location: Hetzner location code (e.g. "fsn1").
            targets: List of server *names* to add as targets.
            health_check: Optional health check config dict passed as-is to
                the service's health_check argument. Unused in this plan if
                ``services`` is provided directly.
            services: List of LoadBalancerService objects defining protocols
                and ports. If None, the LB is created with no services.

        Returns:
            The existing or newly created LoadBalancer SDK object.

        Raises:
            HetznerNotFound: If ``type`` or ``location`` does not exist, or
                a named server in ``targets`` does not exist.
            HetznerAuthError: Invalid or missing API token.
            HetznerNetworkError: Network-level failure; retryable=True.
            HetznerError: Other API errors.
        """
        existing = self.get(name)
        if existing is not None:
            return self._call(  # type: ignore[return-value]
                "GET", "/load_balancers",
                lambda: existing,
                resource_id=str(existing.id),
            )

        # Resolve LB type
        lb_type_obj: LoadBalancerType | None = self._client.load_balancer_types.get_by_name(type)
        if lb_type_obj is None:
            raise HetznerNotFound(f"Load balancer type not found: {type}")

        # Resolve location
        location_obj: Location | None = self._client.locations.get_by_name(location)
        if location_obj is None:
            raise HetznerNotFound(f"Location not found: {location}")

        # Resolve server targets
        target_objs: list[LoadBalancerTarget] = []
        for server_name in (targets or []):
            server = self._servers_getter(server_name)
            if server is None:
                raise HetznerNotFound(f"Server not found for LB target: {server_name}")
            # hcloud SDK type annotation uses BoundServer; Server is its base class.
            # The SDK accepts any Server-like object at runtime.
            target_objs.append(LoadBalancerTarget(type="server", server=server))  # type: ignore[arg-type]

        def _create() -> LoadBalancer:
            response = self._client.load_balancers.create(
                name=name,
                load_balancer_type=lb_type_obj,
                location=location_obj,
                targets=target_objs if target_objs else None,
                services=services,
                labels={},
            )
            if response.action is not None:
                wait_for_action(self._client, response.action)
            return response.load_balancer

        return self._call(  # type: ignore[return-value]
            "POST", "/load_balancers",
            _create,
        )

    def get(self, name: str) -> LoadBalancer | None:
        """Return the named load balancer, or None if not found.

        Internal probe used by get_or_create. Does not emit a log event so
        the outer method controls exactly one log entry per public operation.

        Args:
            name: Load balancer name.

        Returns:
            LoadBalancer or None.
        """
        try:
            return self._client.load_balancers.get_by_name(name)
        except hcloud.APIException as exc:
            raise translate(exc) from exc
        except Exception as exc:
            raise translate(exc) from exc

    def delete(self, name: str) -> bool:
        """Delete the named load balancer.

        Idempotency: returns False if the LB does not exist; True if
        deleted successfully. Logs one ``api.call`` event.

        Args:
            name: Load balancer name.

        Returns:
            True if deleted, False if not found.

        Raises:
            HetznerAuthError: Invalid or missing API token.
            HetznerNetworkError: Network-level failure; retryable=True.
            HetznerError: Other API errors.
        """
        existing = self.get(name)
        if existing is None:
            return False
        self._call(
            "DELETE", "/load_balancers",
            lambda: self._client.load_balancers.delete(existing),
            resource_id=str(existing.id),
        )
        return True
