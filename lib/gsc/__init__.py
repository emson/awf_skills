"""Google Search Console + Site Verification helpers.

The OAuth flow is interactive (browser pop-up) on first auth. Cached
refresh tokens are stored at one of these paths, in resolution order:

  1. `<project_root>/token.json`     (when a project root is in scope)
  2. `$AWF_HOME/token.json`
  3. `~/.config/awf/token.json`

`awf-doctor` and the skill scripts surface the resolved path so the
user can clear it manually if the OAuth scopes change.
"""

from .auth import (  # noqa: F401
    GscError,
    SCOPES,
    find_token_path,
    load_or_authenticate,
    get_gsc_service,
    get_site_verification_service,
)
from .sites import (  # noqa: F401
    sc_domain,
    get_or_add_site,
    get_txt_verification_token,
    verify_site,
    submit_sitemap,
    list_sitemaps,
)
