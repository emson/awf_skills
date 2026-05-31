"""Namecheap registrar API helpers — only the launch-path operations.

Domain-availability checking and account listing (the legacy
`namecheap_domains.py` and `namecheap_domain_checker.py`) are not on
the launch path and are not ported here.
"""

from .client import (  # noqa: F401
    NamecheapClient,
    NamecheapConfig,
    NamecheapError,
    extract_sld_tld,
    get_client,
    get_or_set_custom_dns,
)
