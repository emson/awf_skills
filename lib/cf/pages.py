"""Cloudflare Pages projects and custom domains.

The slug derivation (`domain_to_project_name`) is the canonical one
in `lib/slug.py`; we re-export it here for convenience but never
re-implement it (A12).
"""

from __future__ import annotations

from typing import Optional

from cloudflare import ConflictError

# slug is at lib root; importable when AWF_HOME/lib is on sys.path.
from slug import domain_to_project_name  # noqa: F401 — re-exported

from .client import CloudflareClient


# ── Projects ───────────────────────────────────────────────────────────


def list_pages_projects(client: CloudflareClient, account_id: str) -> list:
    response = client.client.pages.projects.list(account_id=account_id)
    return list(response.result)


def search_pages_project(
    client: CloudflareClient,
    account_id: str,
    project_name: str,
) -> Optional[object]:
    for p in list_pages_projects(client, account_id):
        if p.name == project_name:
            return p
    return None


def create_pages_project(
    client: CloudflareClient,
    account_id: str,
    domain_name: str,
):
    """Create a Pages project. Production branch `main`, build via
    `npm run build` writing to `dist/`.
    """
    project_name = domain_to_project_name(domain_name)
    try:
        project = client.client.pages.projects.create(
            account_id=account_id,
            name=project_name,
            production_branch="main",
            deployment_configs={"preview": {"branch": "main"}},
            build_config={
                "build_command": "npm run build",
                "destination_dir": "dist",
                "root_dir": "/",
            },
        )
        print(f"- created Pages project: {project_name}")
        return project
    except ConflictError:
        # Race with another caller; re-fetch.
        existing = search_pages_project(client, account_id, project_name)
        if existing:
            return existing
        raise


def search_or_create_pages_project(
    client: CloudflareClient,
    account_id: str,
    domain_name: str,
):
    """Idempotent."""
    project_name = domain_to_project_name(domain_name)
    project = search_pages_project(client, account_id, project_name)
    if project:
        print(f"- Pages project {project_name} already exists")
        return project
    print(f"- creating Pages project for {domain_name}")
    return create_pages_project(client, account_id, domain_name)


# ── Custom domains on a project ────────────────────────────────────────


def list_project_domains(
    client: CloudflareClient,
    account_id: str,
    project_name: str,
) -> list:
    response = client.client.pages.projects.domains.list(
        account_id=account_id,
        project_name=project_name,
    )
    return list(response.result)


def create_project_domain(
    client: CloudflareClient,
    account_id: str,
    project_name: str,
    domain_name: str,
):
    return client.client.pages.projects.domains.create(
        project_name=project_name,
        account_id=account_id,
        name=domain_name,
    )


def get_or_create_project_domain(
    client: CloudflareClient,
    account_id: str,
    project_name: str,
    domain_name: str,
):
    """Idempotent."""
    for d in list_project_domains(client, account_id, project_name):
        if d.name == domain_name:
            print(f"- project domain {domain_name} already attached to {project_name}")
            return d
    print(f"- attaching {domain_name} to Pages project {project_name}")
    return create_project_domain(client, account_id, project_name, domain_name)
