"""HetznerClient core: config dataclass, from_env factory, _call helper.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from hcloud import Client

from lib.hetzner.actions import sdk_call

T = TypeVar("T")


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
        # Pass self._call so every resource routes SDK calls through the
        # single log-emit + exception-translate codepath.
        self.ssh_keys = _SSHKeys(self._client, self._call)
        self.networks = _Networks(self._client, self._call)
        self.servers = _Servers(self._client, self._call, self.ssh_keys, self.networks)
        self.firewalls = _Firewalls(self._client, self._call, self.servers.get)
        self.lb = _LoadBalancers(self._client, self._call, self.servers.get)

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

        Delegates to ``actions.sdk_call`` so all resource methods share the
        same log-emit and exception-translate codepath.

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
        return sdk_call(method, path, fn, resource_id=resource_id)
