"""Branches resource namespace for NeonClient.

References:
  - docs/plans/plan_006_s3_neon_lib.md
  - docs/spec.md § B2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.neon.client import CallFn
from lib.neon.connection import get_connection_string
from lib.neon.errors import NeonError
from lib.neon.operations import await_operations


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Branch:
    """Immutable snapshot of a Neon branch.

    Only the fields that S3–S5 actually reads are included.
    """

    id: str
    project_id: str
    name: str
    parent_id: str | None
    primary: bool
    current_state: str


# ---------------------------------------------------------------------------
# Branches namespace
# ---------------------------------------------------------------------------


class _Branches:
    """Manage Neon branches with search-or-create semantics."""

    def __init__(self, call: CallFn) -> None:
        self._call = call

    def get_or_create(
        self,
        project_id: str,
        *,
        name: str,
        parent_id: str | None = None,
    ) -> Branch:
        """Return the named branch, creating it if absent.

        Idempotency contract: if a branch with ``name`` already exists on the
        project, it is returned immediately; no POST is issued.

        What it logs: one ``api.call`` on the list path (GET); one additional
        ``api.call`` on POST if a create is needed; plus one per poll iteration.

        What it raises:
            NeonAuthError, NeonNetworkError, NeonError.

        Args:
            project_id: Neon project ID to create the branch on.
            name: Branch name (unique within the project).
            parent_id: Parent branch ID to fork from.  Defaults to the
                project's primary branch when None.

        Returns:
            The existing or newly created Branch.
        """
        existing = self.get(project_id, name)
        if existing is not None:
            return existing

        payload: dict[str, Any] = {"branch": {"name": name}}
        if parent_id is not None:
            payload["branch"]["parent_id"] = parent_id

        body = self._call(
            "POST",
            f"/projects/{project_id}/branches",
            json=payload,
            resource_id=project_id,
        )
        assert body is not None
        branch_data: dict[str, Any] = body["branch"]
        operations: list[dict[str, Any]] = body.get("operations") or []
        await_operations(project_id, operations, self._call)

        return _branch_from_dict(branch_data, project_id)

    def get(self, project_id: str, name_or_id: str) -> Branch | None:
        """Return a branch by name or ID, or None if not found.

        Logs one ``api.call`` (list).  Raises NeonError on API failures.
        """
        body = self._call("GET", f"/projects/{project_id}/branches")
        if body is None:
            return None
        branches: list[dict[str, Any]] = body.get("branches") or []
        for b in branches:
            if b.get("id") == name_or_id or b.get("name") == name_or_id:
                return _branch_from_dict(b, project_id)
        return None

    def delete(self, project_id: str, branch_id: str) -> bool:
        """Delete a branch by ID; return False on 404 (idempotent teardown).

        Logs one ``api.call``.  Raises NeonError on non-404 failures.
        """
        try:
            self._call(
                "DELETE",
                f"/projects/{project_id}/branches/{branch_id}",
                resource_id=branch_id,
            )
            return True
        except NeonError as exc:
            if exc.code == "not_found":
                return False
            raise

    def connection_string(
        self,
        project_id: str,
        branch_id: str,
        *,
        role: str = "neondb_owner",
        database: str = "neondb",
        pooled: bool = True,
    ) -> str:
        """Return the connection URI for a branch endpoint.

        The URI includes the role password and is never logged.  Only the
        ``api.call`` event for the GET is emitted.  ``?sslmode=require`` is
        guaranteed to be present (appended defensively if Neon omits it).

        What it logs: one ``api.call`` event.  URI value is never logged.

        What it raises:
            NeonAuthError, NeonNetworkError, NeonError.

        Args:
            project_id: Neon project ID.
            branch_id: Neon branch ID.
            role: Database role name (default: neondb_owner).
            database: Database name (default: neondb).
            pooled: Whether to request a pooled endpoint (default: True).

        Returns:
            The full Postgres connection URI string with ``?sslmode=require``.
        """
        return get_connection_string(
            self._call,
            project_id,
            branch_id,
            role=role,
            database=database,
            pooled=pooled,
        )


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _branch_from_dict(data: dict[str, Any], project_id: str) -> Branch:
    return Branch(
        id=str(data["id"]),
        project_id=project_id,
        name=str(data.get("name", "")),
        parent_id=str(data["parent_id"]) if data.get("parent_id") else None,
        primary=bool(data.get("primary", False)),
        current_state=str(data.get("current_state", "")),
    )
