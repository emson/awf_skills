"""Hetzner error hierarchy and exception translation.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

import hcloud


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class HetznerError(Exception):
    """Base error for all Hetzner API failures.

    Carries provider="hetzner", a code string, a human message, and a
    ``retryable`` flag that composers use to decide retry policy.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class HetznerNotFound(HetznerError):
    """Resource not found (404-equivalent)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="not_found", retryable=False)


class HetznerConflict(HetznerError):
    """Resource already exists or conflicting state (409-equivalent).

    Handled internally by get_or_create callers; surfaced for ops where
    the conflict cannot be resolved by a lookup.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="conflict", retryable=False)


class HetznerRateLimited(HetznerError):
    """Rate limit exceeded (429-equivalent). Always retryable.

    ``retry_after`` is the number of seconds to wait before retrying,
    extracted from the Hetzner response details when available.
    """

    def __init__(self, message: str, *, retry_after: float = 5.0) -> None:
        super().__init__(message, code="rate_limit_exceeded", retryable=True)
        self.retry_after = retry_after


class HetznerAuthError(HetznerError):
    """Authentication or authorisation failure (401/403). Never retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="auth_error", retryable=False)


class HetznerNetworkError(HetznerError):
    """Network-level failure (timeouts, connection resets). Always retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="network_error", retryable=True)


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def translate(exc: Exception) -> HetznerError:
    """Translate an hcloud exception to a HetznerError subclass.

    Maps hcloud.APIException.code strings to the appropriate subclass.
    Non-APIException exceptions (requests errors, socket errors) become
    HetznerNetworkError with retryable=True.
    """
    if isinstance(exc, hcloud.APIException):
        code = str(exc.code)
        msg = str(exc.message)
        if code in ("unauthorized", "forbidden"):
            return HetznerAuthError(msg)
        if code in ("not_found",):
            return HetznerNotFound(msg)
        if code in ("conflict", "resource_already_exists"):
            return HetznerConflict(msg)
        if code in ("rate_limit_exceeded",):
            retry_after = 5.0
            if isinstance(exc.details, dict):
                try:
                    retry_after = float(exc.details.get("retry_after", 5.0))
                except (TypeError, ValueError):
                    pass
            return HetznerRateLimited(msg, retry_after=retry_after)
        return HetznerError(msg, code=code, retryable=False)
    # Treat everything else (socket/requests errors) as network errors
    return HetznerNetworkError(str(exc))


def api_status(exc: hcloud.APIException) -> int:
    """Extract an HTTP-style integer status code from an APIException.

    The hcloud SDK stores error codes as strings (e.g. "not_found",
    "rate_limit_exceeded"). We map them to canonical HTTP codes for the
    log.api call. Unknown codes map to 400.
    """
    code_map = {
        "not_found": 404,
        "unauthorized": 401,
        "forbidden": 403,
        "rate_limit_exceeded": 429,
        "conflict": 409,
        "resource_already_exists": 409,
        "invalid_input": 422,
        "action_failed": 424,
    }
    return code_map.get(str(exc.code), 400)
