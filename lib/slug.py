"""Domain ↔ slug. The single source of truth (A12).

`devroast.com`        → `devroast-com`
`agentwebfactory.com` → `agentwebfactory-com`
`example.co.uk`       → `example-co-uk`

The legacy implementation lived in `agent_factory/tools/cloudflare/pages.py`.
Replicated here so this suite has no upstream dependency.
"""

from __future__ import annotations

import re

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def normalize_domain(domain: str) -> str:
    d = domain.strip().lower()
    if d.startswith(("http://", "https://")):
        d = d.split("://", 1)[1]
    d = d.rstrip("/")
    if not _DOMAIN_RE.match(d):
        raise ValueError(f"not a valid domain: {domain!r}")
    return d


def domain_to_project_name(domain: str) -> str:
    return normalize_domain(domain).replace(".", "-")


def project_name_to_domain(project_name: str) -> str:
    """Best-effort reverse. Loses TLD ambiguity (`-com` → `.com`)."""
    return project_name.replace("-", ".")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("usage: slug.py <domain>")
    print(domain_to_project_name(sys.argv[1]))
