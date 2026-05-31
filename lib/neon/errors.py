"""Neon error hierarchy and HTTP-response translation.

References:
  - docs/plans/plan_006_s3_neon_lib.md
  - docs/spec.md § B2
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class NeonError(Exception):
    """Base error for all Neon API failures.

    Carries a ``code`` string, a human-readable ``message``, and a
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


class NeonNotFound(NeonError):
    """Resource not found (404)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="not_found", retryable=False)


class NeonConflict(NeonError):
    """Conflicting state (409).

    Handled internally by get_or_create; exposed for callers that cannot
    resolve the conflict by a lookup.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="conflict", retryable=False)


class NeonRateLimited(NeonError):
    """Rate limit exceeded (429). Always retryable.

    ``retry_after`` is the number of seconds to wait before retrying,
    extracted from the ``Retry-After`` response header when available.
    """

    def __init__(self, message: str, *, retry_after: float = 5.0) -> None:
        super().__init__(message, code="rate_limit_exceeded", retryable=True)
        self.retry_after = retry_after


class NeonAuthError(NeonError):
    """Authentication or authorisation failure (401/403). Never retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="auth_error", retryable=False)


class NeonNetworkError(NeonError):
    """Network-level failure (timeouts, connection resets). Always retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="network_error", retryable=True)


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def _extract_message(body: dict[str, Any] | None) -> str:
    """Pull a human-readable message from a Neon error response body."""
    if body is None:
        return "no response body"
    return str(body.get("message") or body.get("error") or body)


def translate(response: "httpx.Response", body: dict[str, Any] | None) -> NeonError:
    """Map an HTTP error response to the appropriate NeonError subclass.

    Args:
        response: The httpx.Response with status_code >= 400.
        body: Parsed JSON body (may be None on parse failure).

    Returns:
        The matching NeonError subclass instance.
    """
    msg = _extract_message(body)
    sc = response.status_code

    if sc in (401, 403):
        return NeonAuthError(msg)
    if sc == 404:
        return NeonNotFound(msg)
    if sc == 409:
        return NeonConflict(msg)
    if sc == 429:
        retry_after = 5.0
        raw_ra = response.headers.get("Retry-After", "")
        if raw_ra:
            try:
                retry_after = float(raw_ra)
            except ValueError:
                pass
        return NeonRateLimited(msg, retry_after=retry_after)
    return NeonError(msg, code=f"http_{sc}", retryable=False)
