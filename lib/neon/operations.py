"""Operation polling helper for NeonClient.

Neon's create-project and create-branch calls return an ``operations[]``
array representing async provisioning work.  This module provides the
single polling helper used by both resources.

References:
  - docs/plans/plan_006_s3_neon_lib.md
  - docs/spec.md § B2
"""

from __future__ import annotations

import time
from typing import Any

from lib.neon.client import CallFn
from lib.neon.errors import NeonError


# Operation polling settings
POLL_INTERVAL_S = 2.0
MAX_WAIT_S = 300.0


def await_operations(
    project_id: str,
    operations: list[dict[str, Any]],
    call: CallFn,
) -> None:
    """Poll each operation until it reaches a terminal state.

    Shared by ``_Projects.get_or_create`` and ``_Branches.get_or_create``.

    Args:
        project_id: Neon project ID owning the operations.
        operations: List of operation dicts from a create response.
        call: The ``NeonClient._call`` bound method.

    Raises:
        NeonError(code="operation_timeout", retryable=True): If polling
            exceeds ``MAX_WAIT_S`` seconds.
        NeonError(code="operation_failed", retryable=False): If any
            operation reaches status ``failed``.
    """
    deadline = time.monotonic() + MAX_WAIT_S

    for op in operations:
        op_id = str(op["id"])
        while True:
            if time.monotonic() > deadline:
                raise NeonError(
                    f"Timed out waiting for operation {op_id} on project {project_id}",
                    code="operation_timeout",
                    retryable=True,
                )
            body = call(
                "GET",
                f"/projects/{project_id}/operations/{op_id}",
                resource_id=project_id,
            )
            assert body is not None
            status = str((body.get("operation") or {}).get("status", ""))
            if status == "finished":
                break
            if status == "failed":
                raise NeonError(
                    f"Operation {op_id} failed on project {project_id}",
                    code="operation_failed",
                    retryable=False,
                )
            time.sleep(POLL_INTERVAL_S)
