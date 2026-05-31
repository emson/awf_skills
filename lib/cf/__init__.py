"""Cloudflare API helpers used across awf-* skills.

Named `cf` (not `cloudflare`) to avoid shadowing the PyPI `cloudflare`
package on `sys.path`.

Each submodule wraps a slice of the Cloudflare SDK with a search-or-
create idiom, so callers can re-run any setup step without duplicating
side effects (A5).

Convenience re-exports for common entry points:
"""

from .client import CloudflareClient, get_client  # noqa: F401
from .zones import get_or_create_zone  # noqa: F401
from .dns import create_dns_record, create_www_to_apex_redirect_record  # noqa: F401
from .pages import (  # noqa: F401
    search_or_create_pages_project,
    get_or_create_project_domain,
)
from .settings import set_always_use_https  # noqa: F401
from .bulk_redirects import search_and_create_bulk_redirect  # noqa: F401
