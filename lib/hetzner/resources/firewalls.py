"""Firewall resource namespace for HetznerClient.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

from typing import Callable

import hcloud
from hcloud import Client
from hcloud.firewalls import Firewall, FirewallResource, FirewallRule
from hcloud.servers import Server

from lib import log
from lib.hetzner.errors import HetznerError, HetznerNotFound, api_status, translate
from lib.hetzner.client import wait_for_action


class _Firewalls:
    """Manage Hetzner firewalls with ensure/convergence semantics."""

    def __init__(
        self,
        client: Client,
        servers_getter: Callable[[str], Server | None],
    ) -> None:
        self._client = client
        self._servers_getter = servers_getter

    def _rules_equal(self, a: list[FirewallRule], b: list[FirewallRule]) -> bool:
        """Return True if two rule lists are semantically equivalent.

        Compares direction, protocol, source_ips, destination_ips, and port
        for each rule. Order-sensitive.
        """
        if len(a) != len(b):
            return False
        for r1, r2 in zip(a, b):
            if (
                r1.direction != r2.direction
                or r1.protocol != r2.protocol
                or (r1.source_ips or []) != (r2.source_ips or [])
                or (r1.destination_ips or []) != (r2.destination_ips or [])
                or r1.port != r2.port
            ):
                return False
        return True

    def ensure(
        self,
        name: str,
        *,
        rules: list[FirewallRule],
        apply_to: list[str] | None = None,
    ) -> Firewall:
        """Converge the named firewall to the declared rule set.

        Read-modify-write semantics:
        1. Get-or-create the firewall shell.
        2. Diff the current rules against ``rules``.
        3. If equal, log ``api.call`` with status=200 (skip). Return.
        4. If different, replace via ``set_rules``.
        5. If ``apply_to`` is given, apply the firewall to the named servers
           (applies even on a rule-skip so the firewall is idempotently
           attached to the correct servers).

        Idempotency: calling with the same rules twice makes only one
        API write (the first call); the second call logs a skip.

        Args:
            name: Firewall name.
            rules: Desired rule list. Replaces existing rules on mismatch.
            apply_to: Optional list of server names to apply the firewall to.

        Returns:
            The Firewall SDK object (existing or updated).

        Raises:
            HetznerNotFound: If a named server in ``apply_to`` does not exist.
            HetznerAuthError: Invalid or missing API token.
            HetznerNetworkError: Network-level failure; retryable=True.
            HetznerError: Other API errors.
        """
        existing = self.get(name)
        if existing is None:
            # Create firewall shell (no rules yet — set separately)
            try:
                response = self._client.firewalls.create(name=name)
                firewall: Firewall = response.firewall
                log.api(
                    provider="hetzner",
                    method="POST",
                    path="/firewalls",
                    status_code=201,
                    resource_id=str(firewall.id),
                )
            except hcloud.APIException as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path="/firewalls",
                    status_code=api_status(exc),
                )
                raise translate(exc) from exc
            except Exception as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path="/firewalls",
                    status_code=0,
                )
                raise translate(exc) from exc
        else:
            firewall = existing

        # Diff rules
        current_rules: list[FirewallRule] = firewall.rules or []
        if self._rules_equal(current_rules, rules):
            log.api(
                provider="hetzner",
                method="GET",
                path="/firewalls",
                status_code=200,
                resource_id=str(firewall.id),
            )
        else:
            try:
                actions = self._client.firewalls.set_rules(firewall, rules)
                for action in (actions or []):
                    wait_for_action(self._client, action)
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/set_rules",
                    status_code=200,
                    resource_id=str(firewall.id),
                )
            except hcloud.APIException as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/set_rules",
                    status_code=api_status(exc),
                )
                raise translate(exc) from exc
            except HetznerError:
                raise
            except Exception as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/set_rules",
                    status_code=0,
                )
                raise translate(exc) from exc

        # Apply to servers if requested
        if apply_to:
            resources: list[FirewallResource] = []
            for server_name in apply_to:
                server = self._servers_getter(server_name)
                if server is None:
                    raise HetznerNotFound(
                        f"Server not found for firewall apply_to: {server_name}"
                    )
                resources.append(FirewallResource(type="server", server=server))
            try:
                apply_actions = self._client.firewalls.apply_to_resources(
                    firewall, resources
                )
                for action in (apply_actions or []):
                    wait_for_action(self._client, action)
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/apply_to_resources",
                    status_code=200,
                    resource_id=str(firewall.id),
                )
            except hcloud.APIException as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/apply_to_resources",
                    status_code=api_status(exc),
                )
                raise translate(exc) from exc
            except HetznerError:
                raise
            except Exception as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/apply_to_resources",
                    status_code=0,
                )
                raise translate(exc) from exc

        return firewall

    def get(self, name: str) -> Firewall | None:
        """Return the named firewall, or None if not found.

        Args:
            name: Firewall name.

        Returns:
            Firewall or None.
        """
        try:
            return self._client.firewalls.get_by_name(name)
        except hcloud.APIException as exc:
            raise translate(exc) from exc
        except Exception as exc:
            raise translate(exc) from exc

    def delete(self, name: str) -> bool:
        """Delete the named firewall.

        Idempotency: returns False if the firewall does not exist; True if
        deleted successfully. Logs one ``api.call`` event.

        Args:
            name: Firewall name.

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
                path="/firewalls",
                status_code=404,
            )
            return False
        try:
            self._client.firewalls.delete(existing)
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/firewalls",
                status_code=204,
                resource_id=str(existing.id),
            )
            return True
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/firewalls",
                status_code=api_status(exc),
            )
            raise translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/firewalls",
                status_code=0,
            )
            raise translate(exc) from exc
