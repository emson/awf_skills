"""Connection-string helper for NeonClient branches.

Extracted from resources/branches.py to keep that file under 200 lines.

References:
  - docs/plans/plan_006_s3_neon_lib.md § "Idempotency contract"
  - docs/spec.md § B2
"""

from __future__ import annotations

from lib import log
from lib.neon.client import CallFn


def get_connection_string(
    call: CallFn,
    project_id: str,
    branch_id: str,
    *,
    role: str,
    database: str,
    pooled: bool,
) -> str:
    """Return the connection URI for a branch endpoint.

    The URI is assembled server-side by Neon's connection_uri endpoint,
    which includes the role password.  The URI is returned verbatim; it
    is **never** written to any log.  Only the ``api.call`` event for the
    GET is logged.

    Neon's default includes ``?sslmode=require``; this function defensively
    appends it if the server omits it (e.g. under a future Neon change).
    A ``log.note`` event is emitted when the append fires so operators can
    detect drift (T2 advisory).

    What it logs: one ``api.call`` event.  The URI itself (which contains
    the role password) is never logged.

    What it raises:
        NeonAuthError, NeonNetworkError, NeonError.

    Args:
        call: Bound ``NeonClient._call`` method.
        project_id: Neon project ID.
        branch_id: Neon branch ID.
        role: Database role name.
        database: Database name.
        pooled: Whether to request a pooled (PgBouncer) endpoint.

    Returns:
        The full Postgres connection URI string, always containing
        ``?sslmode=require``.
    """
    body = call(
        "GET",
        f"/projects/{project_id}/connection_uri",
        params={
            "branch_id": branch_id,
            "role_name": role,
            "database_name": database,
            "pooled": str(pooled).lower(),
        },
        resource_id=branch_id,
    )
    assert body is not None
    uri: str = str(body.get("uri", ""))

    if "sslmode=require" not in uri:
        # Neon's default should always include sslmode=require.  Defensive
        # append so the application is never left with an insecure connection.
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}sslmode=require"
        log.note(
            f"neon.connection_string.ssl_appended branch_id={branch_id}",
            by="neon-client",
        )

    return uri
