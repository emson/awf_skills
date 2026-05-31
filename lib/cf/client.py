"""Cloudflare client construction.

Decouples credential loading from the SDK so tests can pass an explicit
`CloudflareConfig`. The `get_client()` factory uses `lib/config.py` to
read from the layered `.env` chain (A6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cloudflare import Cloudflare


@dataclass(frozen=True)
class CloudflareConfig:
    api_email: str
    api_key: str
    account_id: str


class CloudflareClient:
    """Thin wrapper around the SDK that also carries the account_id.

    Account ID isn't part of the SDK auth context — many calls require
    it as a parameter — so we keep it on `client.config.account_id` for
    convenience.
    """

    def __init__(self, config: CloudflareConfig):
        self.config = config
        self.client = Cloudflare(
            api_email=config.api_email,
            api_key=config.api_key,
        )


# ── Factory ────────────────────────────────────────────────────────────


def get_client(
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> CloudflareClient:
    """Build a `CloudflareClient` from the layered config.

    Caller is expected to pass `project_root` and `awf_home` if they've
    already resolved them (e.g. via `lib/project.py` and
    `lib/awf_home.py`); otherwise we resolve `awf_home` only.
    """
    from config import Config  # local import to avoid hard dep at import time

    if awf_home is None:
        try:
            from awf_home import find_awf_home
            awf_home = find_awf_home()
        except Exception:
            awf_home = None

    cfg = Config.layered(project_root=project_root, awf_home=awf_home)

    missing = [
        k for k in ("CLOUDFLARE_EMAIL", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID")
        if not cfg.get(k)
    ]
    if missing:
        raise RuntimeError(
            f"Cloudflare credentials missing: {', '.join(missing)}. "
            "Run awf-init, then awf-doctor."
        )

    return CloudflareClient(
        CloudflareConfig(
            api_email=cfg.require("CLOUDFLARE_EMAIL"),
            api_key=cfg.require("CLOUDFLARE_API_KEY"),
            account_id=cfg.require("CLOUDFLARE_ACCOUNT_ID"),
        )
    )
