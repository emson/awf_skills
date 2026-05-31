"""Projects resource namespace for NeonClient.

References:
  - docs/plans/plan_006_s3_neon_lib.md
  - docs/spec.md § B2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.neon.client import CallFn, looks_like_id
from lib.neon.errors import NeonError
from lib.neon.operations import await_operations


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Project:
    """Immutable snapshot of a Neon project.

    Only the fields that S3–S5 actually reads are included.  Raw JSON keys
    are abstracted away; callers use typed attribute access.
    """

    id: str
    name: str
    region_id: str
    created_at: str


# ---------------------------------------------------------------------------
# Projects namespace
# ---------------------------------------------------------------------------


class _Projects:
    """Manage Neon projects with search-or-create semantics."""

    def __init__(self, call: CallFn) -> None:
        self._call = call

    def get_or_create(
        self,
        name: str,
        *,
        region_id: str = "aws-eu-central-1",
        pg_version: int = 16,
    ) -> Project:
        """Return the named project, creating it if absent.

        Idempotency contract: if a project with ``name`` already exists, it
        is returned immediately; no POST is issued and ``api.call`` logs only
        the search request.

        What it logs: one ``api.call`` on the list/search path (GET); one
        additional ``api.call`` on the POST if a create is required; plus one
        ``api.call`` per operation-poll iteration.

        What it raises:
            NeonAuthError: Invalid or missing API key.
            NeonNetworkError: Network-level failure; retryable=True.
            NeonError: Operation failed during provisioning, or other API error.

        Args:
            name: Project name (human-readable; unique within your Neon account).
            region_id: AWS region for the new project (default: aws-eu-central-1).
            pg_version: Postgres major version for the new project (default: 16).

        Returns:
            The existing or newly created Project.
        """
        existing = self.get(name)
        if existing is not None:
            return existing

        body = self._call(
            "POST",
            "/projects",
            json={
                "project": {
                    "name": name,
                    "region_id": region_id,
                    "pg_version": pg_version,
                }
            },
        )
        assert body is not None
        project_data: dict[str, Any] = body["project"]
        operations: list[dict[str, Any]] = body.get("operations") or []
        project_id = str(project_data["id"])

        await_operations(project_id, operations, self._call)

        return _project_from_dict(project_data)

    def get(self, name_or_id: str) -> Project | None:
        """Return a project by name or ID, or None if not found.

        Disambiguation heuristic (T4 advisory): strings matching the pattern
        ``[a-z]+-[a-z]+-[0-9]+`` (e.g. ``lucky-cloud-123456``) are treated
        as Neon project IDs and trigger a direct ``GET /projects/{id}``
        lookup; everything else triggers a name search via
        ``GET /projects?search=<name>``.

        What it logs: one ``api.call`` event (either the GET-by-id or the
        search; NeonNotFound on the ID path is swallowed and triggers the
        search fallback).

        What it raises:
            NeonAuthError, NeonNetworkError, NeonError.

        Args:
            name_or_id: Project name or Neon project ID.

        Returns:
            Project if found, None if not found.
        """
        if looks_like_id(name_or_id):
            try:
                body = self._call("GET", f"/projects/{name_or_id}")
                if body and "project" in body:
                    return _project_from_dict(body["project"])
            except NeonError as exc:
                if exc.code == "not_found":
                    pass  # fall through to name search
                else:
                    raise

        # Name search
        body = self._call("GET", "/projects", params={"search": name_or_id})
        if body is None:
            return None
        projects: list[dict[str, Any]] = body.get("projects") or []
        for p in projects:
            if p.get("name") == name_or_id:
                return _project_from_dict(p)
        return None

    def delete(self, project_id: str) -> bool:
        """Delete a project by ID.

        Idempotency: returns False if the project does not exist; True if
        deleted successfully.

        What it logs: one ``api.call`` event.

        What it raises:
            NeonAuthError, NeonNetworkError, NeonError (excluding NeonNotFound
            which is swallowed and returns False).

        Args:
            project_id: Neon project ID.

        Returns:
            True if deleted, False if not found.
        """
        try:
            self._call("DELETE", f"/projects/{project_id}", resource_id=project_id)
            return True
        except NeonError as exc:
            if exc.code == "not_found":
                return False
            raise


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _project_from_dict(data: dict[str, Any]) -> Project:
    return Project(
        id=str(data["id"]),
        name=str(data["name"]),
        region_id=str(data.get("region_id", "")),
        created_at=str(data.get("created_at", "")),
    )
