"""Namecheap API client (XML over HTTPS).

Public surface, narrow:

  - `extract_sld_tld(domain)`         — split a domain into Namecheap's
                                        SLD/TLD pair, with compound-TLD
                                        support (`.co.uk`, `.com.au`, …)
  - `NamecheapClient.get_dns_servers` — read current nameservers
  - `NamecheapClient.set_custom_dns`  — write nameservers
  - `get_or_set_custom_dns(...)`      — idempotent: read first, no-op if
                                        already matching, else write
  - `get_client()`                    — factory from layered Config

Per A13, errors from Namecheap are surfaced verbatim via `NamecheapError`.

Dependencies: `requests`. The `requests` package is declared in the
inline metadata of any uv-script that imports this module.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


BASE_URL = "https://api.namecheap.com/xml.response"
NS = {"nc": "http://api.namecheap.com/xml.response"}
DEFAULT_TIMEOUT = 15.0  # seconds

# A small list of known compound TLDs. Public Suffix List is the
# canonical source but it's a heavy dependency; this covers the AWF
# launch surface. Add entries as needed.
COMPOUND_TLDS: frozenset[str] = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk", "net.uk",
    "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
    "com.au", "net.au", "org.au", "id.au", "edu.au", "gov.au",
    "co.nz", "net.nz", "org.nz",
    "co.za", "org.za", "net.za",
    "com.br", "com.mx", "com.ar", "com.sg", "com.hk", "com.tw",
})


# ── Errors / config ────────────────────────────────────────────────────


class NamecheapError(RuntimeError):
    """Surfaces upstream API errors with their original messages."""


@dataclass(frozen=True)
class NamecheapConfig:
    api_user: str
    api_key: str
    username: str
    client_ip: str


# ── Domain splitting ───────────────────────────────────────────────────


def extract_sld_tld(domain: str) -> tuple[str, str]:
    """Split a domain into the (SLD, TLD) pair Namecheap expects.

    `example.com`     → (`example`, `com`)
    `example.co.uk`   → (`example`, `co.uk`)
    `example.com.au`  → (`example`, `com.au`)
    """
    cleaned = domain.strip().lower().rstrip(".")
    parts = cleaned.split(".")
    if len(parts) < 2 or any(not p for p in parts):
        raise ValueError(f"not a valid domain: {domain!r}")
    if len(parts) >= 3:
        compound = ".".join(parts[-2:])
        if compound in COMPOUND_TLDS:
            return ".".join(parts[:-2]), compound
    return parts[-2], parts[-1]


# ── Client ─────────────────────────────────────────────────────────────


class NamecheapClient:
    def __init__(self, config: NamecheapConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "awf-skills/1.0",
            "Accept": "application/xml",
        })

    # -- low-level ---------------------------------------------------

    def _params(self, command: str, **extra: str) -> dict[str, str]:
        return {
            "ApiUser": self.config.api_user,
            "ApiKey": self.config.api_key,
            "UserName": self.config.username,
            "ClientIp": self.config.client_ip,
            "Command": command,
            **extra,
        }

    def _call(self, command: str, **extra: str) -> ET.Element:
        """Issue a Namecheap API call. Returns the parsed XML root.
        Raises `NamecheapError` on transport, parse, or `Status != OK`.
        """
        try:
            r = self.session.get(
                BASE_URL,
                params=self._params(command, **extra),
                timeout=DEFAULT_TIMEOUT,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise NamecheapError(f"{command}: transport error: {e}") from e

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as e:
            raise NamecheapError(f"{command}: invalid XML: {e} | body={r.text[:400]}") from e

        status = root.attrib.get("Status", "ERROR")
        if status != "OK":
            errors = root.find("nc:Errors", NS)
            msgs = []
            if errors is not None:
                msgs = [e.text or "" for e in errors.findall("nc:Error", NS)]
            raise NamecheapError(
                f"{command}: API status={status}: {'; '.join(msgs) or '(no error detail)'}"
            )
        return root

    # -- DNS ---------------------------------------------------------

    def get_dns_servers(self, sld: str, tld: str) -> list[str]:
        """Return the current nameservers configured for `sld.tld`.

        Empty list = the domain is on Namecheap's default DNS rather
        than custom nameservers.
        """
        root = self._call("namecheap.domains.dns.getList", SLD=sld, TLD=tld)
        result = root.find(".//nc:DomainDNSGetListResult", NS)
        if result is None:
            raise NamecheapError("getList: missing DomainDNSGetListResult node")
        # Either custom or default. When IsUsingOurDNS=true, no <Nameserver> children.
        return [
            (n.text or "").strip().lower()
            for n in result.findall("nc:Nameserver", NS)
            if (n.text or "").strip()
        ]

    def set_custom_dns(self, sld: str, tld: str, nameservers: list[str]) -> bool:
        """Set custom nameservers. Returns the API's `Updated` boolean.

        Note: Namecheap's setCustom is itself idempotent (passing the
        same NS twice is fine), so this can safely be called without a
        prior get-and-compare. `get_or_set_custom_dns` does the compare
        anyway for cleaner reporting.
        """
        if not nameservers:
            raise ValueError("nameservers list is empty")
        root = self._call(
            "namecheap.domains.dns.setCustom",
            SLD=sld,
            TLD=tld,
            Nameservers=",".join(nameservers),
        )
        result = root.find(".//nc:DomainDNSSetCustomResult", NS)
        if result is None:
            raise NamecheapError("setCustom: missing DomainDNSSetCustomResult node")
        updated = (result.attrib.get("Updated", "false") or "").lower() == "true"
        return updated

    # -- context manager so `session` gets closed cleanly -----------

    def __enter__(self) -> "NamecheapClient":
        return self

    def __exit__(self, *exc) -> None:
        self.session.close()


# ── Factory ────────────────────────────────────────────────────────────


def get_client(
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> NamecheapClient:
    from config import Config

    if awf_home is None:
        try:
            from awf_home import find_awf_home
            awf_home = find_awf_home()
        except Exception:
            awf_home = None

    cfg = Config.layered(project_root=project_root, awf_home=awf_home)
    missing = [
        k for k in (
            "NAMECHEAP_API_USER",
            "NAMECHEAP_API_KEY",
            "NAMECHEAP_USERNAME",
            "NAMECHEAP_CLIENT_IP",
        )
        if not cfg.get(k)
    ]
    if missing:
        raise NamecheapError(
            f"Namecheap credentials missing: {', '.join(missing)}. "
            "Run awf-init, then awf-doctor."
        )
    return NamecheapClient(NamecheapConfig(
        api_user=cfg.require("NAMECHEAP_API_USER"),
        api_key=cfg.require("NAMECHEAP_API_KEY"),
        username=cfg.require("NAMECHEAP_USERNAME"),
        client_ip=cfg.require("NAMECHEAP_CLIENT_IP"),
    ))


# ── Idempotent helper ──────────────────────────────────────────────────


def get_or_set_custom_dns(
    client: NamecheapClient,
    domain: str,
    nameservers: list[str],
) -> tuple[bool, list[str]]:
    """Idempotent. Returns `(changed, current_nameservers)`.

    Reads current NS first. If they match `nameservers` (order-insensitive,
    case-insensitive), no-op. Otherwise calls `setCustom` and returns the
    new list.
    """
    sld, tld = extract_sld_tld(domain)
    target = sorted(n.strip().lower() for n in nameservers if n.strip())
    if not target:
        raise ValueError("nameservers list is empty")

    current = client.get_dns_servers(sld, tld)
    if sorted(current) == target:
        print(f"- nameservers for {domain} already match target ({', '.join(target)})")
        return False, current

    print(f"- updating nameservers for {domain} → {', '.join(target)}")
    updated = client.set_custom_dns(sld, tld, target)
    if not updated:
        raise NamecheapError(
            f"setCustom returned Updated=false for {domain}; check Namecheap account state"
        )
    return True, target
