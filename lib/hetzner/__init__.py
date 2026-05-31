"""Hetzner Cloud API client for awf-skills.

Idempotent wrapper around hcloud-python. Exposes resource namespaces
(servers, firewalls, lb, ssh_keys, networks) with search-or-create
semantics (A1) and structured logging via lib/log.py (A6, plan 003).

All credentials resolve through the layered config chain:
  process env → ./.env → $AWF_HOME/.env → ~/.config/awf/.env
Single credential: HETZNER_API_TOKEN.

Design mirrors lib/cf/client.py so a reader familiar with that module
can read this cold.

Public import path:
  from lib.hetzner import HetznerClient, HetznerConfig
  from lib.hetzner import HetznerError, HetznerNotFound, HetznerRateLimited

References:
  - docs/spec.md § B1
  - docs/plans/plan_005_s3_hetzner_lib.md
  - ADR D-001 (multi-stage architecture)
  - ADR D-003 (infra.json hetzner block schema)
"""

from __future__ import annotations

# Re-export public API from sub-modules so callers can use:
#   from lib.hetzner import HetznerClient
# without knowing the internal package layout.

from lib.hetzner.errors import (
    HetznerError,
    HetznerNotFound,
    HetznerConflict,
    HetznerRateLimited,
    HetznerAuthError,
    HetznerNetworkError,
)
from lib.hetzner.client import HetznerConfig, HetznerClient
from lib.hetzner.resources.ssh_keys import _SSHKeys
from lib.hetzner.resources.networks import _Networks
from lib.hetzner.resources.servers import _Servers
from lib.hetzner.resources.firewalls import _Firewalls
from lib.hetzner.resources.lb import _LoadBalancers

# SDK type re-exports so callers avoid importing hcloud directly.
from hcloud.firewalls import Firewall, FirewallRule
from hcloud.load_balancers import (
    LoadBalancer,
    LoadBalancerHealthCheck,
    LoadBalancerService,
    LoadBalancerTarget,
)
from hcloud.networks import Network
from hcloud.servers import Server
from hcloud.ssh_keys import SSHKey

__all__ = [
    # Core client
    "HetznerClient",
    "HetznerConfig",
    # Errors
    "HetznerError",
    "HetznerNotFound",
    "HetznerConflict",
    "HetznerRateLimited",
    "HetznerAuthError",
    "HetznerNetworkError",
    # Private namespace classes (used by tests for direct injection)
    "_SSHKeys",
    "_Networks",
    "_Servers",
    "_Firewalls",
    "_LoadBalancers",
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
