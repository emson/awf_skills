"""Hetzner Cloud API client for awf-skills.

Idempotent wrapper around hcloud-python. Exposes resource namespaces
(servers, firewalls, lb, ssh_keys, networks) with search-or-create
semantics (A1) and structured logging via lib/log.py (A6, plan 003).

All credentials resolve through the layered config chain:
  process env → ./.env → $AWF_HOME/.env → ~/.config/awf/.env
Single credential: HETZNER_API_TOKEN.

Design mirrors lib/cf/client.py so a reader familiar with that module
can read this cold.

References:
  - docs/spec.md § B1
  - docs/plans/plan_005_s3_hetzner_lib.md
  - ADR D-001 (multi-stage architecture)
  - ADR D-003 (infra.json hetzner block schema)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

import hcloud
from hcloud import Client
from hcloud.firewalls import Firewall, FirewallResource, FirewallRule
from hcloud.load_balancer_types import LoadBalancerType
from hcloud.load_balancers import (
    LoadBalancer,
    LoadBalancerHealthCheck,
    LoadBalancerService,
    LoadBalancerTarget,
)
from hcloud.locations import Location
from hcloud.networks import Network, NetworkSubnet
from hcloud.server_types import ServerType
from hcloud.servers import Server
from hcloud.ssh_keys import SSHKey

from lib import log

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

T = TypeVar("T")

# Re-export SDK types so callers can use them without importing hcloud directly.
__all__ = [
    "HetznerClient",
    "HetznerConfig",
    "HetznerError",
    "HetznerNotFound",
    "HetznerConflict",
    "HetznerRateLimited",
    "HetznerAuthError",
    "HetznerNetworkError",
    # SDK re-exports
    "Server",
    "Firewall",
    "FirewallRule",
    "LoadBalancer",
    "LoadBalancerService",
    "LoadBalancerHealthCheck",
    "LoadBalancerTarget",
    "SSHKey",
    "Network",
]

# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class HetznerError(Exception):
    """Base error for all Hetzner API failures.

    Carries provider="hetzner", a code string, a human message, and a
    ``retryable`` flag that composers use to decide retry policy.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class HetznerNotFound(HetznerError):
    """Resource not found (404-equivalent)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="not_found", retryable=False)


class HetznerConflict(HetznerError):
    """Resource already exists or conflicting state (409-equivalent).

    Handled internally by get_or_create callers; surfaced for ops where
    the conflict cannot be resolved by a lookup.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="conflict", retryable=False)


class HetznerRateLimited(HetznerError):
    """Rate limit exceeded (429-equivalent). Always retryable.

    ``retry_after`` is the number of seconds to wait before retrying,
    extracted from the Hetzner response details when available.
    """

    def __init__(self, message: str, *, retry_after: float = 5.0) -> None:
        super().__init__(message, code="rate_limit_exceeded", retryable=True)
        self.retry_after = retry_after


class HetznerAuthError(HetznerError):
    """Authentication or authorisation failure (401/403). Never retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="auth_error", retryable=False)


class HetznerNetworkError(HetznerError):
    """Network-level failure (timeouts, connection resets). Always retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="network_error", retryable=True)


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def _translate(exc: Exception) -> HetznerError:
    """Translate an hcloud exception to a HetznerError subclass.

    Maps hcloud.APIException.code strings to the appropriate subclass.
    Non-APIException exceptions (requests errors, socket errors) become
    HetznerNetworkError with retryable=True.
    """
    if isinstance(exc, hcloud.APIException):
        code = str(exc.code)
        msg = str(exc.message)
        if code in ("unauthorized", "forbidden"):
            return HetznerAuthError(msg)
        if code in ("not_found",):
            return HetznerNotFound(msg)
        if code in ("conflict", "resource_already_exists"):
            return HetznerConflict(msg)
        if code in ("rate_limit_exceeded",):
            retry_after = 5.0
            if isinstance(exc.details, dict):
                try:
                    retry_after = float(exc.details.get("retry_after", 5.0))
                except (TypeError, ValueError):
                    pass
            return HetznerRateLimited(msg, retry_after=retry_after)
        return HetznerError(msg, code=code, retryable=False)
    # Treat everything else (socket/requests errors) as network errors
    return HetznerNetworkError(str(exc))


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HetznerConfig:
    """Credential + connection settings for Hetzner Cloud.

    Mirrors CloudflareConfig in lib/cf/client.py for consistency.
    """

    api_token: str
    app_name: str = "awf-skills"


# ---------------------------------------------------------------------------
# Private helper: extract numeric id from an SDK resource object
# ---------------------------------------------------------------------------


def _extract_id(obj: Any) -> str | None:
    """Return ``str(obj.id)`` if obj has an ``id`` attribute, else None."""
    try:
        return str(obj.id)
    except AttributeError:
        return None


# ---------------------------------------------------------------------------
# Action polling helper (internal only)
# ---------------------------------------------------------------------------

_ACTION_POLL_INTERVAL = 2   # seconds between status checks
_ACTION_TIMEOUT = 300       # seconds before giving up


def _wait_for_action(client: Client, action: Any, timeout: int = _ACTION_TIMEOUT) -> None:
    """Poll ``action`` until it reaches terminal state.

    Raises HetznerError if the action fails or times out. This is an
    internal helper; composers own readiness policy for higher-level
    concerns like "server is fully booted".

    Args:
        client: The hcloud Client.
        action: An hcloud Action object (must have .id and .status).
        timeout: Seconds to wait before raising HetznerNetworkError.

    Raises:
        HetznerError: If the action errors out.
        HetznerNetworkError: If the action does not complete within ``timeout``.
    """
    start = time.monotonic()
    while True:
        refreshed = client.actions.get_by_id(action.id)
        if refreshed.status == "success":
            return
        if refreshed.status == "error":
            raise HetznerError(
                f"Action {action.id} failed: {refreshed.error}",
                code="action_failed",
                retryable=False,
            )
        if time.monotonic() - start > timeout:
            raise HetznerNetworkError(
                f"Action {action.id} timed out after {timeout}s"
            )
        time.sleep(_ACTION_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Resource namespaces
# ---------------------------------------------------------------------------


class _SSHKeys:
    """Manage Hetzner SSH keys with search-or-create semantics."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def get_or_create(self, name: str, *, public_key: str) -> SSHKey:
        """Return the named SSH key, creating it if absent.

        Idempotency: if a key with ``name`` already exists, it is returned
        immediately with no create call. Logs a single ``api.call`` event
        with ``result=ok`` and the existing ``resource_id``.

        Args:
            name: Key name (unique within the Hetzner project).
            public_key: OpenSSH public key string.

        Returns:
            The existing or newly created SSHKey SDK object.

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
                path="/ssh-keys",
                status_code=200,
                resource_id=str(existing.id),
            )
            return existing
        # Key absent — create it
        try:
            result = self._client.ssh_keys.create(name=name, public_key=public_key)
            log.api(
                provider="hetzner",
                method="POST",
                path="/ssh-keys",
                status_code=201,
                resource_id=str(result.id),
            )
            return result
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/ssh-keys",
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/ssh-keys",
                status_code=0,
            )
            raise _translate(exc) from exc

    def get(self, name: str) -> SSHKey | None:
        """Return the named SSH key, or None if not found.

        Logs no ``api.call`` event — callers inside this namespace use
        get() as a pre-flight check; the outer method logs the final
        outcome.

        Args:
            name: Key name.

        Returns:
            SSHKey or None.
        """
        try:
            result = self._client.ssh_keys.get_by_name(name)
            return result
        except hcloud.APIException as exc:
            raise _translate(exc) from exc
        except Exception as exc:
            raise _translate(exc) from exc


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
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/networks",
                status_code=0,
            )
            raise _translate(exc) from exc

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
            raise _translate(exc) from exc
        except Exception as exc:
            raise _translate(exc) from exc

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
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/networks",
                status_code=0,
            )
            raise _translate(exc) from exc


class _Servers:
    """Manage Hetzner servers with search-or-create semantics."""

    def __init__(self, client: Client, ssh_keys_ns: _SSHKeys, networks_ns: _Networks) -> None:
        self._client = client
        self._ssh_keys_ns = ssh_keys_ns
        self._networks_ns = networks_ns

    def get_or_create(
        self,
        name: str,
        *,
        type: str = "cx22",
        image: str = "ubuntu-24.04",
        location: str = "fsn1",
        ssh_keys: list[str] | None = None,
        network: str | None = None,
        labels: dict[str, str] | None = None,
        user_data: str | None = None,
    ) -> Server:
        """Return the named server, creating it if absent.

        Idempotency: if a server with ``name`` already exists, it is returned
        immediately with no create call. No drift detection: if the existing
        server has the wrong type/image it is returned as-is; the composer
        is responsible for detecting drift.

        Args:
            name: Server name (unique within the Hetzner project).
            type: Server type slug (e.g. "cx22", "cx32").
            image: OS image name (e.g. "ubuntu-24.04"). Use ``user_data`` to
                   install Docker via cloud-init; do not pass a "docker-ce"
                   app image (requires a different lookup path — see plan_005).
            location: Hetzner location code (e.g. "fsn1").
            ssh_keys: List of SSH key *names* (resolved via _SSHKeys.get()).
            network: Network name to attach (resolved via _Networks.get()).
            labels: Hetzner resource labels.
            user_data: Cloud-init user data string.

        Returns:
            The existing or newly created Server SDK object.

        Raises:
            HetznerNotFound: If ``type``, ``image``, ``location``, or a named
                ssh_key / network does not exist.
            HetznerAuthError: Invalid or missing API token.
            HetznerNetworkError: Network-level failure; retryable=True.
            HetznerError: Other API errors.
        """
        existing = self.get(name)
        if existing is not None:
            log.api(
                provider="hetzner",
                method="GET",
                path="/servers",
                status_code=200,
                resource_id=str(existing.id),
            )
            return existing

        # Resolve server type
        server_type_obj: ServerType | None = self._client.server_types.get_by_name(type)
        if server_type_obj is None:
            raise HetznerNotFound(f"Server type not found: {type}")

        # Resolve image
        image_obj = self._client.images.get_by_name(image)
        if image_obj is None:
            raise HetznerNotFound(f"Image not found: {image}")

        # Resolve location
        location_obj: Location | None = self._client.locations.get_by_name(location)
        if location_obj is None:
            raise HetznerNotFound(f"Location not found: {location}")

        # Resolve SSH keys by name
        ssh_key_objs: list[SSHKey] = []
        for key_name in (ssh_keys or []):
            key = self._ssh_keys_ns.get(key_name)
            if key is None:
                raise HetznerNotFound(f"SSH key not found: {key_name}")
            ssh_key_objs.append(key)

        # Resolve network by name
        network_objs: list[Network] = []
        if network is not None:
            net = self._networks_ns.get(network)
            if net is None:
                raise HetznerNotFound(f"Network not found: {network}")
            network_objs.append(net)

        try:
            response = self._client.servers.create(
                name=name,
                server_type=server_type_obj,
                image=image_obj,
                location=location_obj,
                ssh_keys=ssh_key_objs if ssh_key_objs else None,
                networks=network_objs if network_objs else None,
                labels=labels or {},
                user_data=user_data,
            )
            # Poll any next_actions to terminal state
            for action in (response.next_actions or []):
                _wait_for_action(self._client, action)

            log.api(
                provider="hetzner",
                method="POST",
                path="/servers",
                status_code=201,
                resource_id=str(response.server.id),
            )
            return response.server
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/servers",
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except HetznerError:
            raise
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/servers",
                status_code=0,
            )
            raise _translate(exc) from exc

    def get(self, name: str) -> Server | None:
        """Return the named server, or None if not found.

        Args:
            name: Server name.

        Returns:
            Server or None.
        """
        try:
            return self._client.servers.get_by_name(name)
        except hcloud.APIException as exc:
            raise _translate(exc) from exc
        except Exception as exc:
            raise _translate(exc) from exc

    def delete(self, name: str) -> bool:
        """Delete the named server.

        Idempotency: returns False if the server does not exist; True if
        deleted successfully. Logs one ``api.call`` event.

        Args:
            name: Server name.

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
                path="/servers",
                status_code=404,
            )
            return False
        try:
            self._client.servers.delete(existing)
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/servers",
                status_code=204,
                resource_id=str(existing.id),
            )
            return True
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/servers",
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/servers",
                status_code=0,
            )
            raise _translate(exc) from exc


class _Firewalls:
    """Manage Hetzner firewalls with ensure/convergence semantics."""

    def __init__(self, client: Client, servers_getter: Callable[[str], Server | None]) -> None:
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
                    status_code=_api_status(exc),
                )
                raise _translate(exc) from exc
            except Exception as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path="/firewalls",
                    status_code=0,
                )
                raise _translate(exc) from exc
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
                    _wait_for_action(self._client, action)
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
                    status_code=_api_status(exc),
                )
                raise _translate(exc) from exc
            except HetznerError:
                raise
            except Exception as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/set_rules",
                    status_code=0,
                )
                raise _translate(exc) from exc

        # Apply to servers if requested
        if apply_to:
            resources: list[FirewallResource] = []
            for server_name in apply_to:
                server = self._servers_getter(server_name)
                if server is None:
                    raise HetznerNotFound(f"Server not found for firewall apply_to: {server_name}")
                resources.append(FirewallResource(type="server", server=server))
            try:
                apply_actions = self._client.firewalls.apply_to_resources(firewall, resources)
                for action in (apply_actions or []):
                    _wait_for_action(self._client, action)
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
                    status_code=_api_status(exc),
                )
                raise _translate(exc) from exc
            except HetznerError:
                raise
            except Exception as exc:
                log.api(
                    provider="hetzner",
                    method="POST",
                    path=f"/firewalls/{firewall.id}/actions/apply_to_resources",
                    status_code=0,
                )
                raise _translate(exc) from exc

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
            raise _translate(exc) from exc
        except Exception as exc:
            raise _translate(exc) from exc

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
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/firewalls",
                status_code=0,
            )
            raise _translate(exc) from exc


class _LoadBalancers:
    """Manage Hetzner load balancers with search-or-create semantics."""

    def __init__(self, client: Client, servers_getter: Callable[[str], Server | None]) -> None:
        self._client = client
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
            log.api(
                provider="hetzner",
                method="GET",
                path="/load_balancers",
                status_code=200,
                resource_id=str(existing.id),
            )
            return existing

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

        try:
            response = self._client.load_balancers.create(
                name=name,
                load_balancer_type=lb_type_obj,
                location=location_obj,
                targets=target_objs if target_objs else None,
                services=services,
                labels={},
            )
            lb: LoadBalancer = response.load_balancer
            # Poll creation action if present
            if response.action is not None:
                _wait_for_action(self._client, response.action)

            log.api(
                provider="hetzner",
                method="POST",
                path="/load_balancers",
                status_code=201,
                resource_id=str(lb.id),
            )
            return lb
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/load_balancers",
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except HetznerError:
            raise
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/load_balancers",
                status_code=0,
            )
            raise _translate(exc) from exc

    def get(self, name: str) -> LoadBalancer | None:
        """Return the named load balancer, or None if not found.

        Args:
            name: Load balancer name.

        Returns:
            LoadBalancer or None.
        """
        try:
            return self._client.load_balancers.get_by_name(name)
        except hcloud.APIException as exc:
            raise _translate(exc) from exc
        except Exception as exc:
            raise _translate(exc) from exc

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
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/load_balancers",
                status_code=404,
            )
            return False
        try:
            self._client.load_balancers.delete(existing)
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/load_balancers",
                status_code=204,
                resource_id=str(existing.id),
            )
            return True
        except hcloud.APIException as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/load_balancers",
                status_code=_api_status(exc),
            )
            raise _translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/load_balancers",
                status_code=0,
            )
            raise _translate(exc) from exc


# ---------------------------------------------------------------------------
# Private utility
# ---------------------------------------------------------------------------


def _api_status(exc: hcloud.APIException) -> int:
    """Extract an HTTP-style integer status code from an APIException.

    The hcloud SDK stores error codes as strings (e.g. "not_found",
    "rate_limit_exceeded"). We map them to canonical HTTP codes for the
    log.api call. Unknown codes map to 400.
    """
    code_map = {
        "not_found": 404,
        "unauthorized": 401,
        "forbidden": 403,
        "rate_limit_exceeded": 429,
        "conflict": 409,
        "resource_already_exists": 409,
        "invalid_input": 422,
        "action_failed": 424,
    }
    return code_map.get(str(exc.code), 400)


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


class HetznerClient:
    """Top-level Hetzner Cloud client for awf-skills.

    Exposes resource namespaces (servers, firewalls, lb, ssh_keys,
    networks) as attributes. Each namespace has search-or-create
    semantics and emits ``api.call`` log events via lib/log.py.

    Credential resolution: HETZNER_API_TOKEN via layered config (A6).

    Usage::

        from lib.hetzner import HetznerClient
        hz = HetznerClient.from_env()
        server = hz.servers.get_or_create("my-server", type="cx22")
        hz.firewalls.ensure("my-fw", rules=[...])

    """

    def __init__(self, config: HetznerConfig) -> None:
        self.config = config
        self._client = Client(
            token=config.api_token,
            application_name=config.app_name,
        )
        self.ssh_keys = _SSHKeys(self._client)
        self.networks = _Networks(self._client)
        self.servers = _Servers(self._client, self.ssh_keys, self.networks)
        self.firewalls = _Firewalls(self._client, self.servers.get)
        self.lb = _LoadBalancers(self._client, self.servers.get)

    @classmethod
    def from_env(
        cls,
        *,
        project_root: Path | None = None,
        awf_home: Path | None = None,
    ) -> "HetznerClient":
        """Build a HetznerClient from the layered env config.

        Resolves HETZNER_API_TOKEN from: process env → ./.env →
        $AWF_HOME/.env → ~/.config/awf/.env.

        Args:
            project_root: Explicit project root for .env lookup (optional).
            awf_home: Explicit awf home for .env lookup (optional).

        Returns:
            A fully initialised HetznerClient.

        Raises:
            RuntimeError: If HETZNER_API_TOKEN is missing from all layers.
        """
        from lib.config import Config  # local import to avoid hard dep at import time

        if awf_home is None:
            try:
                from lib.awf_home import find_awf_home
                awf_home = find_awf_home()
            except Exception:
                awf_home = None

        cfg = Config.layered(project_root=project_root, awf_home=awf_home)

        token = cfg.get("HETZNER_API_TOKEN")
        if not token:
            raise RuntimeError(
                "Hetzner credentials missing: HETZNER_API_TOKEN. "
                "Run awf-init, then awf-doctor."
            )

        return cls(HetznerConfig(api_token=token))
