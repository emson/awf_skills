"""IndexNow client + sitemap helpers.

Per the IndexNow spec (https://www.indexnow.org/documentation):

  - Key length: 8–128 chars, hex.
  - The key file at `https://<host>/<key>.txt` must contain exactly the
    key string (no surrounding whitespace) for ownership proof.
  - URLs are submitted in batches up to 10,000 per request.

Raises `IndexNowError` for transport, parse, and API errors (A13).
"""

from __future__ import annotations

import secrets
import xml.etree.ElementTree as ET
from pathlib import Path

import requests


INDEXNOW_API_ENDPOINT = "https://api.indexnow.org/indexnow"
SITEMAP_PATH = "/sitemap.xml"
BATCH_SIZE = 10_000
DEFAULT_TIMEOUT = 30.0  # IndexNow can take a while on large batches
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class IndexNowError(RuntimeError):
    pass


# ── Key helpers ────────────────────────────────────────────────────────


def generate_key() -> str:
    """Return a fresh 32-char hex IndexNow key (well within 8–128)."""
    return secrets.token_hex(16)


def key_file_path_in_project(project_root: Path, key: str) -> Path:
    """Where the key file should live in the project source tree.

    Convention: `static/<key>.txt`. Cloudflare Pages serves
    `static/foo.txt` at `https://<domain>/foo.txt` for SvelteKit and
    most other static-host frameworks the AWF template uses.
    """
    return project_root / "static" / f"{key}.txt"


def key_file_reachable(domain: str, key: str) -> tuple[bool, str]:
    """GET the key file and confirm its body equals the key.

    Returns `(reachable, detail)`. We always GET (not HEAD) because
    SvelteKit / SPA fallbacks return 200 for any path, masking a
    missing key file. Verifying the body content is the only way to
    be sure IndexNow will accept the submission.
    """
    url = f"https://{domain}/{key}.txt"
    try:
        r = requests.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        return False, f"transport error fetching {url}: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} at {url}"
    body = (r.text or "").strip()
    if body != key:
        return False, (
            f"{url} returned 200 but body did not match key "
            f"(SPA fallback? body starts with {body[:40]!r})"
        )
    return True, f"200 at {url} with matching body"


# ── Sitemap parsing ────────────────────────────────────────────────────


def fetch_sitemap_urls(domain: str) -> list[str]:
    """Fetch and parse `https://<domain>/sitemap.xml`. Returns a flat
    list of URLs. Nested sitemap-indexes are not followed (out of scope
    for the AWF launch path; sites here are small)."""
    url = f"https://{domain}{SITEMAP_PATH}"
    try:
        r = requests.get(url, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        raise IndexNowError(f"could not fetch sitemap {url}: {e}") from e

    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        raise IndexNowError(f"sitemap {url} is not valid XML: {e}") from e

    return [
        (elem.text or "").strip()
        for elem in root.findall(f".//{SITEMAP_NS}loc")
        if (elem.text or "").strip()
    ]


# ── Submission ─────────────────────────────────────────────────────────


def submit_urls(domain: str, key: str, urls: list[str]) -> int:
    """POST URLs to IndexNow in batches. Returns total number submitted.

    Caller is responsible for verifying `https://<domain>/<key>.txt` is
    live before calling — IndexNow rejects submissions when the key
    file is unreachable.
    """
    if not urls:
        return 0
    key_location = f"https://{domain}/{key}.txt"
    submitted = 0
    for i in range(0, len(urls), BATCH_SIZE):
        batch = urls[i:i + BATCH_SIZE]
        payload = {
            "host": domain,
            "key": key,
            "keyLocation": key_location,
            "urlList": batch,
        }
        try:
            r = requests.post(
                INDEXNOW_API_ENDPOINT,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise IndexNowError(f"transport error submitting batch {i}: {e}") from e
        # IndexNow returns 200 (success), 202 (accepted), or various 4xx.
        if r.status_code not in (200, 202):
            raise IndexNowError(
                f"IndexNow returned {r.status_code}: {r.text[:300]}"
            )
        submitted += len(batch)
    return submitted
