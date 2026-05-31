"""KamalConfig: pure YAML renderer for config/deploy.yml.

This is the only module in lib/kamal/ that imports yaml.  The renderer is
referentially transparent: same (anchor, infra) inputs always produce
byte-identical output.  No timestamps, no random IDs, no environment reads.

References:
  - docs/plans/plan_007_s3_kamal_lib.md § "KamalConfig.render()"
  - ADR D-003 (infra.json kamal block schema)
  - ADR D-005 (GHCR as default registry)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lib.kamal.errors import KamalError
from lib.state import Infra, ProjectAnchor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Token used in rendered YAML for the registry password environment variable.
_GHCR_PASSWORD_ENV = "GHCR_TOKEN"
_DOCKER_PASSWORD_ENV = "DOCKER_PASSWORD"

#: Filename of the current golden fixture (bump to v2 on Kamal schema change;
#: never edit v1 in place — see plan_007 Risk 5).
FIXTURE_VERSION = "deploy_v1.yml"

# ---------------------------------------------------------------------------
# KamalConfig
# ---------------------------------------------------------------------------


class KamalConfig:
    """Pure YAML renderer.  No I/O beyond anchor/infra reads and optional file write.

    Responsibilities:
      - Project ``config/deploy.yml`` from ``(ProjectAnchor, Infra)``.
      - Write the file when ``path`` is provided in ``render()``.
      - Never read credentials or environment variables.
    """

    def __init__(self, anchor: ProjectAnchor, infra: Infra) -> None:
        """Initialise with a project anchor and infra ledger.

        Args:
            anchor: Project identity (domain, slug).
            infra:  Infrastructure ledger (registry, hetzner, neon, kamal).
        """
        self._anchor = anchor
        self._infra = infra

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self, *, path: Path | str | None = None) -> str:
        """Compose deploy.yml YAML; optionally write to disk; return YAML string.

        What it does:
            Builds a Kamal-compatible deploy.yml document from the anchor and
            infra fields.  Keys are emitted in stable insertion order
            (sort_keys=False).  Trailing newline; LF line endings.

        What it logs:
            Nothing.  This method is pure.

        What it raises:
            KamalError: if ``infra.hetzner.servers`` contains no server with
                role=="web".

        Idempotency:
            Fully idempotent — same inputs always produce byte-identical output.
            Two calls in the same process with the same (anchor, infra) will
            match byte-for-byte.
        """
        doc = self._build_document()
        yaml_str: str = yaml.dump(
            doc,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=4096,
        )
        # Ensure trailing newline (yaml.dump already adds one, but be explicit)
        if not yaml_str.endswith("\n"):
            yaml_str += "\n"

        if path is not None:
            dest = Path(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(yaml_str, encoding="utf-8")

        return yaml_str

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _web_server_ip(self) -> str:
        """Return the IP of the first server with role=='web'.

        Raises:
            KamalError: if no web server is found in infra.hetzner.servers.
        """
        for server in self._infra.hetzner.servers:
            if server.role == "web":
                return server.ip
        raise KamalError(
            "no web server in infra: infra.hetzner.servers has no entry with role='web'",
            code="config_error",
        )

    def _password_env(self) -> str:
        """Return the registry password env var name based on the registry host."""
        host = self._infra.registry.host or "ghcr.io"
        return _GHCR_PASSWORD_ENV if host == "ghcr.io" else _DOCKER_PASSWORD_ENV

    def _build_document(self) -> dict[str, Any]:
        """Construct the YAML document as a Python dict (stable insertion order)."""
        anchor = self._anchor
        infra = self._infra

        registry_host = infra.registry.host or "ghcr.io"
        registry_user = infra.registry.user
        slug = anchor.slug
        domain = anchor.domain
        server_ip = self._web_server_ip()
        password_env = self._password_env()
        secret_ref = infra.neon.connection_secret_ref

        # Build in stable insertion order matching golden fixture deploy_v1.yml
        doc: dict[str, Any] = {
            "service": slug,
            "image": f"{registry_host}/{registry_user}/{slug}",
            "servers": {
                "web": {
                    "hosts": [server_ip],
                }
            },
            "proxy": {
                "ssl": True,
                "host": domain,
            },
            "registry": {
                "server": registry_host,
                "username": registry_user,
                "password": [
                    f'<%= ENV["{password_env}"] %>',
                ],
            },
            "env": {
                "secret": [secret_ref],
            },
        }
        return doc
