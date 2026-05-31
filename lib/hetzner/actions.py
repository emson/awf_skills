"""Action polling and shared SDK-call helper for lib/hetzner.

Contains two utilities used by multiple modules:

- ``wait_for_action``: polls an hcloud Action to terminal state.
- ``sdk_call``: try/except wrapper that emits one ``api.call`` log event
  per SDK invocation and translates hcloud exceptions to HetznerError
  subclasses. Resource classes receive a ``caller`` bound to ``sdk_call``
  so every SDK call routes through a single codepath.

References:
  - docs/plans/plan_005_s3_hetzner_lib.md
  - docs/spec.md § B1
"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

import hcloud
from hcloud import Client

from lib import log
from lib.hetzner.errors import HetznerError, HetznerNetworkError, api_status, translate

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACTION_POLL_INTERVAL = 2    # seconds between status checks
_ACTION_TIMEOUT = 300        # seconds before giving up


# ---------------------------------------------------------------------------
# Action polling
# ---------------------------------------------------------------------------


def wait_for_action(client: Client, action: Any, timeout: int = _ACTION_TIMEOUT) -> None:
    """Poll ``action`` until it reaches a terminal state.

    Raises ``HetznerError`` if the action fails, or ``HetznerNetworkError``
    if it does not complete within ``timeout`` seconds. This is an internal
    helper; composers own readiness policy for higher-level concerns like
    "server is fully booted".

    Args:
        client: The hcloud Client used to refresh the action status.
        action: An hcloud Action object (must have ``.id`` and ``.status``).
        timeout: Seconds to wait before raising HetznerNetworkError.

    Raises:
        HetznerError: If the action errors out.
        HetznerNetworkError: If the action does not complete within ``timeout``.
    """
    start = time.monotonic()
    while True:
        refreshed = client.actions.get_by_id(action.id)
        if refreshed.status == "success":
            return
        if refreshed.status == "error":
            raise HetznerError(
                f"Action {action.id} failed: {refreshed.error}",
                code="action_failed",
                retryable=False,
            )
        if time.monotonic() - start > timeout:
            raise HetznerNetworkError(
                f"Action {action.id} timed out after {timeout}s"
            )
        time.sleep(_ACTION_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Shared SDK-call wrapper
# ---------------------------------------------------------------------------


def _extract_id(obj: Any) -> str | None:
    """Return ``str(obj.id)`` if obj has an ``id`` attribute, else None."""
    try:
        return str(obj.id)
    except AttributeError:
        return None


def sdk_call(
    method: str,
    path: str,
    fn: Callable[[], T],
    *,
    resource_id: str | None = None,
) -> T:
    """Wrap an hcloud SDK call: emit log.api, translate exceptions.

    Every SDK invocation in every resource class routes through this
    function (or a bound alias of it). This gives a single source of
    truth for ``api.call`` log emission and exception translation.

    Args:
        method: HTTP method for the log (e.g. ``"GET"``, ``"POST"``).
        path: API path for the log (e.g. ``"/servers"``).
        fn: Zero-argument callable that performs the SDK call.
        resource_id: Pre-known resource id (e.g. on skip/delete paths).
            If ``None`` and ``fn`` returns an object with ``.id``, it is
            extracted automatically.

    Returns:
        Whatever ``fn()`` returns.

    Raises:
        HetznerError subclass translated from ``hcloud.APIException``.
    """
    try:
        result = fn()
        rid = resource_id if resource_id is not None else _extract_id(result)
        log.api(
            provider="hetzner",
            method=method,
            path=path,
            status_code=200,
            resource_id=rid,
        )
        return result
    except hcloud.APIException as e:
        log.api(
            provider="hetzner",
            method=method,
            path=path,
            status_code=api_status(e),
            resource_id=None,
        )
        raise translate(e) from e
    except HetznerError:
        raise
    except Exception as e:
        log.api(
            provider="hetzner",
            method=method,
            path=path,
            status_code=0,
            resource_id=None,
        )
        raise translate(e) from e
