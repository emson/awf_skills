"""Account-level bulk-redirect lists.

The legacy implementation iterated over *every* bulk-redirect list on
the account and added the source→target pair to *each* one. That works
when there is exactly one list per account (the AWF default), but is
suspicious shape when more than one exists. We preserve the behaviour
unchanged but flag it via a runtime warning.

If you need stricter targeting later, extend `search_and_create_bulk_redirect`
to accept a `list_name` argument and use `get_bulk_redirect_list` first.
"""

from __future__ import annotations

from typing import Optional

from .client import CloudflareClient


# A naming convention from the legacy code; not currently used as a
# selector but kept here as documentation.
DEFAULT_LIST_NAME = "bulk_redirect_apex_1"


def list_bulk_redirect_lists(client: CloudflareClient, account_id: str):
    return client.client.rules.lists.list(account_id=account_id)


def get_bulk_redirect_list(
    client: CloudflareClient,
    account_id: str,
    list_name: str,
) -> Optional[object]:
    lists = list_bulk_redirect_lists(client, account_id)
    for lst in lists.result:
        if lst.name == list_name:
            return lst
    return None


def search_list_for_url(
    client: CloudflareClient,
    account_id: str,
    list_id: str,
    source_url: str,
) -> Optional[object]:
    items = client.client.rules.lists.items.list(
        list_id=list_id, account_id=account_id
    )
    for item in items.result:
        if hasattr(item, "redirect") and item.redirect:
            src = item.redirect.source_url
            if src and src.rstrip("/") == source_url.rstrip("/"):
                return item
    return None


def create_bulk_redirect_item(
    client: CloudflareClient,
    account_id: str,
    list_id: str,
    source_url: str,
    target_url: str,
):
    """Idempotent on `(list_id, source_url)`."""
    existing = search_list_for_url(client, account_id, list_id, source_url)
    if existing:
        print(f"- bulk redirect {source_url} -> already in list")
        return existing

    new_item = {
        "redirect": {
            "source_url": source_url,
            "target_url": target_url,
            "status_code": 301,
            "preserve_query_string": True,
            "include_subdomains": True,
            "subpath_matching": True,
            "preserve_path_suffix": True,
        }
    }
    response = client.client.rules.lists.items.create(
        account_id=account_id, list_id=list_id, body=[new_item]
    )
    print(f"- added bulk redirect {source_url} -> {target_url}")
    return response


def search_and_create_bulk_redirect(
    client: CloudflareClient,
    source_url: str,
    target_url: str,
):
    """Add `source_url -> target_url` to the bulk-redirect list(s) on the
    configured account.

    NOTE: legacy behaviour preserved — this iterates every list on the
    account and adds the redirect to each one. Safe and idempotent when
    the account has exactly one list (the AWF default). Emits a warning
    when more than one is present.
    """
    account_id = client.config.account_id
    lists = list_bulk_redirect_lists(client, account_id)
    results = list(lists.result)

    if len(results) == 0:
        raise RuntimeError(
            "No bulk-redirect lists configured on this Cloudflare account. "
            "Create one in the dashboard (Rules → Lists → New list, type "
            "`Redirect`) before running awf-setup-domain."
        )
    if len(results) > 1:
        names = ", ".join(lst.name for lst in results)
        print(f"- WARN: {len(results)} bulk-redirect lists found; will add to all ({names})")

    for lst in results:
        create_bulk_redirect_item(
            client, account_id, lst.id, source_url, target_url
        )
