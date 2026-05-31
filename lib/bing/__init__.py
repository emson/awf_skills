"""Bing IndexNow helpers.

IndexNow is auth-free at the call site: the per-domain key is stored at
`https://<domain>/<key>.txt` and Bing fetches it to confirm ownership.
There is no client state, so this lib is module-level functions.
"""

from .indexnow import (  # noqa: F401
    IndexNowError,
    BATCH_SIZE,
    SITEMAP_PATH,
    INDEXNOW_API_ENDPOINT,
    generate_key,
    fetch_sitemap_urls,
    submit_urls,
    key_file_reachable,
    key_file_path_in_project,
)
