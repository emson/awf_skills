"""HetznerClient core: config dataclass, from_env factory, _call helper.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

import hcloud
from hcloud import Client

from lib import log
from lib.hetzner.errors import (
    HetznerError,
    HetznerNetworkError,
    api_status,
    translate,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Action polling helper (internal only)
# ---------------------------------------------------------------------------

_ACTION_POLL_INTERVAL = 2   # seconds between status checks
_ACTION_TIMEOUT = 300       # seconds before giving up


def wait_for_action(client: Client, action: Any, timeout: int = _ACTION_TIMEOUT) -> None:
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
# Private utility
# ---------------------------------------------------------------------------


def _extract_id(obj: Any) -> str | None:
    """Return ``str(obj.id)`` if obj has an ``id`` attribute, else None."""
    try:
        return str(obj.id)
    except AttributeError:
        return None


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
        from lib.hetzner.resources.ssh_keys import _SSHKeys
        from lib.hetzner.resources.networks import _Networks
        from lib.hetzner.resources.servers import _Servers
        from lib.hetzner.resources.firewalls import _Firewalls
        from lib.hetzner.resources.lb import _LoadBalancers

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

    def _call(
        self,
        method: str,
        path: str,
        fn: Callable[[], T],
        *,
        resource_id: str | None = None,
    ) -> T:
        """Wrap an hcloud SDK call: emit log.api, translate exceptions.

        All resource methods route through this helper to guarantee:
        - exactly one ``api.call`` log event per SDK call
        - consistent exception translation via errors.translate()
        - token redaction (the token never reaches log.api directly)

        Args:
            method: HTTP method string for the log (e.g. "GET", "POST").
            path: API path for the log (e.g. "/servers").
            fn: Zero-argument callable that performs the SDK call.
            resource_id: Pre-known resource id (e.g. on skip/delete paths).
                If None and fn returns an object with .id, it is extracted.

        Returns:
            Whatever ``fn()`` returns.

        Raises:
            HetznerError subclass translated from hcloud.APIException.
        """
        try:
            result = fn()
            rid = resource_id if resource_id is not None else _extract_id(result)
            log.api(
                provider="hetzner",
                method=method,
                path=path,
                status_code=200,
                resource_id=rid,
            )
            return result
        except hcloud.APIException as e:
            log.api(
                provider="hetzner",
                method=method,
                path=path,
                status_code=api_status(e),
                resource_id=None,
            )
            raise translate(e) from e
        except HetznerError:
            raise
        except Exception as e:
            log.api(
                provider="hetzner",
                method=method,
                path=path,
                status_code=0,
                resource_id=None,
            )
            raise translate(e) from e
