"""NeonClient core: config dataclass, from_env factory, _call chokepoint.

Every HTTP byte leaving this library travels through ``_call``.  Resource
classes receive ``client._call`` as their only transport reference; they
never see ``self._http`` or ``httpx`` directly.

References:
  - docs/plans/plan_006_s3_neon_lib.md
  - docs/spec.md § B2
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from lib import log
from lib.neon.errors import NeonNetworkError, translate

# Type alias for the _call bound-method signature passed to resources.
CallFn = Callable[..., "dict[str, Any] | None"]

_BASE_URL = "https://console.neon.tech/api/v2"
_TIMEOUT = 30.0

# Neon project IDs look like: "lucky-cloud-123456" (two words + digits).
# We use this to decide whether get(name_or_id) tries a direct ID lookup.
_ID_RE = re.compile(r"^[a-z]+-[a-z]+-\d+")


@dataclass(frozen=True)
class NeonConfig:
    """Credential + connection settings for Neon.

    The bearer token is structurally excluded from all log payloads:
    ``_call`` never passes it to ``log.api``; resources never see it.
    """

    api_key: str
    base_url: str = _BASE_URL
    timeout: float = _TIMEOUT


class NeonClient:
    """Top-level Neon API client.  NEON_API_KEY via layered config (A6).

    Usage::

        nc = NeonClient.from_env()
        p  = nc.projects.get_or_create("my-app")
        b  = nc.branches.get_or_create(p.id, name="preview")
    """

    def __init__(
        self,
        config: NeonConfig,
        *,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Args:
            config: Credentials and connection settings.
            _transport: Test-only transport override (httpx.MockTransport).
        """
        from lib.neon.resources.projects import _Projects
        from lib.neon.resources.branches import _Branches

        self.config = config
        self._http = _build_http_client(config, _transport)
        self.projects = _Projects(self._call)
        self.branches = _Branches(self._call)

    @classmethod
    def from_env(
        cls,
        *,
        project_root: Path | None = None,
        awf_home: Path | None = None,
    ) -> "NeonClient":
        """Resolve NEON_API_KEY from layered config (env→project→awf_home→user).

        Raises RuntimeError if NEON_API_KEY is missing from all layers.
        """
        from lib.config import Config

        if awf_home is None:
            try:
                from lib.awf_home import find_awf_home
                awf_home = find_awf_home()
            except Exception:
                awf_home = None

        cfg = Config.layered(project_root=project_root, awf_home=awf_home)
        token = cfg.get("NEON_API_KEY")
        if not token:
            raise RuntimeError(
                "Neon credentials missing: NEON_API_KEY. "
                "Run awf-init, then awf-doctor."
            )
        return cls(NeonConfig(api_key=token))

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        resource_id: str | None = None,
    ) -> "dict[str, Any] | None":
        """Issue one HTTP request; emit log.api; translate errors.

        Single chokepoint: every HTTP call in lib/neon/ routes through here.
        No resource may call ``self._http`` directly.

        Returns parsed JSON body or None on 204.  Raises NeonError subclass
        on network failures or HTTP 4xx/5xx responses.
        """
        try:
            resp = self._http.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            log.api(provider="neon", method=method, path=path,
                    status_code=0, resource_id=resource_id)
            raise NeonNetworkError(str(exc)) from exc

        body: dict[str, Any] | None = None
        if resp.content:
            try:
                body = resp.json()
            except Exception:
                body = None

        rid = resource_id or _extract_id(body)
        log.api(provider="neon", method=method, path=path,
                status_code=resp.status_code, resource_id=rid)

        if resp.status_code >= 400:
            raise translate(resp, body)

        return body

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "NeonClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _extract_id(body: dict[str, Any] | None) -> str | None:
    """Best-effort extraction of a resource ID from a response body."""
    if body is None:
        return None
    for key in ("project", "branch", "endpoint", "operation"):
        obj = body.get(key)
        if isinstance(obj, dict):
            rid = obj.get("id")
            if rid:
                return str(rid)
    direct = body.get("id")
    return str(direct) if direct else None


def looks_like_id(s: str) -> bool:
    """Return True if ``s`` matches the Neon project-ID pattern.

    Neon project IDs look like ``lucky-cloud-123456``: two lowercase words
    followed by digits.  Used by ``_Projects.get`` to decide whether to try
    a direct GET-by-id before falling back to a name search.
    """
    return bool(_ID_RE.match(s))


def _build_http_client(
    config: NeonConfig,
    transport: httpx.BaseTransport | None,
) -> httpx.Client:
    """Construct the httpx.Client with auth headers, optional transport."""
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    # httpx.Client accepts transport=None (uses default); passing it
    # explicitly lets us inject a MockTransport in tests without a branch.
    if transport is not None:
        return httpx.Client(base_url=config.base_url, headers=headers,
                            timeout=config.timeout, transport=transport)
    return httpx.Client(base_url=config.base_url, headers=headers,
                        timeout=config.timeout)
