"""SSH key resource namespace for HetznerClient.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

from typing import Callable

from hcloud import Client
from hcloud.ssh_keys import SSHKey


class _SSHKeys:
    """Manage Hetzner SSH keys with search-or-create semantics."""

    def __init__(self, client: Client, caller: Callable[..., object]) -> None:
        self._client = client
        self._call = caller

    def get_or_create(self, name: str, *, public_key: str) -> SSHKey:
        """Return the named SSH key, creating it if absent.

        Idempotency: if a key with ``name`` already exists, it is returned
        immediately with no create call. Logs a single ``api.call`` event
        with the existing ``resource_id``.

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
            return self._call(  # type: ignore[return-value]
                "GET", "/ssh-keys",
                lambda: existing,
                resource_id=str(existing.id),
            )
        return self._call(  # type: ignore[return-value]
            "POST", "/ssh-keys",
            lambda: self._client.ssh_keys.create(name=name, public_key=public_key),
        )

    def get(self, name: str) -> SSHKey | None:
        """Return the named SSH key, or None if not found.

        Internal probe used by get_or_create. Does not emit a log event so
        the outer method controls exactly one log entry per public operation.

        Args:
            name: Key name.

        Returns:
            SSHKey or None.
        """
        return self._call(  # type: ignore[return-value]
            "GET", f"/ssh-keys?name={name}",
            lambda: self._client.ssh_keys.get_by_name(name),
        )
