"""Server resource namespace for HetznerClient.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import hcloud
from hcloud import Client
from hcloud.locations import Location
from hcloud.networks import Network
from hcloud.server_types import ServerType
from hcloud.servers import Server
from hcloud.ssh_keys import SSHKey

from lib import log
from lib.hetzner.errors import HetznerError, HetznerNotFound, api_status, translate
from lib.hetzner.client import wait_for_action

if TYPE_CHECKING:
    from lib.hetzner.resources.ssh_keys import _SSHKeys
    from lib.hetzner.resources.networks import _Networks


class _Servers:
    """Manage Hetzner servers with search-or-create semantics."""

    def __init__(
        self,
        client: Client,
        ssh_keys_ns: "_SSHKeys",
        networks_ns: "_Networks",
    ) -> None:
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
                wait_for_action(self._client, action)

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
                status_code=api_status(exc),
            )
            raise translate(exc) from exc
        except HetznerError:
            raise
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/servers",
                status_code=0,
            )
            raise translate(exc) from exc

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
            raise translate(exc) from exc
        except Exception as exc:
            raise translate(exc) from exc

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
                status_code=api_status(exc),
            )
            raise translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="DELETE",
                path="/servers",
                status_code=0,
            )
            raise translate(exc) from exc
