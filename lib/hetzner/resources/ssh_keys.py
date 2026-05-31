"""SSH key resource namespace for HetznerClient.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

import hcloud
from hcloud import Client
from hcloud.ssh_keys import SSHKey

from lib import log
from lib.hetzner.errors import api_status, translate


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
                status_code=api_status(exc),
            )
            raise translate(exc) from exc
        except Exception as exc:
            log.api(
                provider="hetzner",
                method="POST",
                path="/ssh-keys",
                status_code=0,
            )
            raise translate(exc) from exc

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
            raise translate(exc) from exc
        except Exception as exc:
            raise translate(exc) from exc
