"""Kamal error hierarchy and subprocess-output translation.

References:
  - docs/plans/plan_007_s3_kamal_lib.md
  - docs/spec.md § B3
  - ADR D-001 (kamal as deploy abstraction for S3-S5)
"""

from __future__ import annotations

import subprocess
from typing import Any


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class KamalError(Exception):
    """Base error for all Kamal CLI failures.

    Carries a ``code`` string, a human-readable ``message``, raw ``stderr``
    from the subprocess, a ``hint`` string pointing at common causes, and a
    ``retryable`` flag that composers use to decide retry policy.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown",
        stderr: str = "",
        hint: str = "See log.jsonl for the full stderr.",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stderr = stderr
        self.hint = hint
        self.retryable = retryable


class KamalNotInstalled(KamalError):
    """kamal CLI is not on PATH.

    Always non-retryable — the fix is to install kamal.
    """

    def __init__(self, message: str = "kamal CLI not on PATH; install with `gem install kamal`") -> None:
        super().__init__(message, code="not_installed", retryable=False)


class KamalDnsTimeout(KamalError):
    """DNS propagation poll exhausted before the A-record matched.

    Retryable — the human can wait for DNS and re-run.
    """

    def __init__(self, *, domain: str, expected_ip: str) -> None:
        message = (
            f"DNS propagation timeout: A-record for {domain!r} "
            f"did not resolve to {expected_ip!r}"
        )
        super().__init__(
            message,
            code="dns_timeout",
            hint=(
                f"Check the CF DNS record for {domain} is set to {expected_ip} "
                f"and grey-cloud. Re-run once `dig +short {domain}` returns "
                f"{expected_ip}."
            ),
            retryable=True,
        )
        self.domain = domain
        self.expected_ip = expected_ip


class KamalSetupFailed(KamalError):
    """``kamal setup`` exited non-zero."""

    def __init__(self, message: str, *, stderr: str = "", hint: str = "See log.jsonl for the full stderr.") -> None:
        super().__init__(message, code="setup_failed", stderr=stderr, hint=hint, retryable=False)


class KamalDeployFailed(KamalError):
    """``kamal deploy`` exited non-zero.

    Retryable for network-ish exit codes (hint heuristic applied by _translate).
    """

    def __init__(
        self,
        message: str,
        *,
        stderr: str = "",
        hint: str = "See log.jsonl for the full stderr.",
        retryable: bool = False,
    ) -> None:
        super().__init__(message, code="deploy_failed", stderr=stderr, hint=hint, retryable=retryable)


class KamalRollbackFailed(KamalError):
    """``kamal rollback`` exited non-zero."""

    def __init__(self, message: str, *, stderr: str = "", hint: str = "See log.jsonl for the full stderr.") -> None:
        super().__init__(message, code="rollback_failed", stderr=stderr, hint=hint, retryable=False)


# ---------------------------------------------------------------------------
# Hint heuristics
# ---------------------------------------------------------------------------

#: Ordered list of (stderr-substring, hint-string) pairs.
#: First match wins; the final entry is the default fallback.
_HINT_RULES: list[tuple[str, str]] = [
    (
        "dockerfile",
        "Run awf-app-dockerize to scaffold the Dockerfile.",
    ),
    (
        "missing secret",
        "Run awf-app-secret-set; check .kamal/secrets.",
    ),
    (
        "secret not set",
        "Run awf-app-secret-set; check .kamal/secrets.",
    ),
    (
        "denied: permission_denied",
        "Check GHCR_TOKEN in ~/.config/awf/.env; run awf-doctor.",
    ),
    (
        "unauthorized",
        "Check GHCR_TOKEN in ~/.config/awf/.env; run awf-doctor.",
    ),
    (
        "connection refused",
        "Server may still be booting; re-run in 60s.",
    ),
]

_DEFAULT_HINT = "See log.jsonl for the full stderr."


def _hint_from_stderr(stderr: str) -> str:
    """Return the first matching hint for the given stderr string."""
    lower = stderr.lower()
    for substring, hint in _HINT_RULES:
        if substring.lower() in lower:
            return hint
    return _DEFAULT_HINT


def _is_network_retryable(stderr: str) -> bool:
    """Return True when the error looks like a transient network failure."""
    lower = stderr.lower()
    return "connection refused" in lower


# ---------------------------------------------------------------------------
# Exception translation
# ---------------------------------------------------------------------------


def translate(
    args: list[str],
    cp: "subprocess.CompletedProcess[str]",
) -> KamalError:
    """Map a failed CompletedProcess to the appropriate KamalError subclass.

    Args:
        args: The CLI argument list passed to kamal (excluding the ``kamal``
              binary itself — i.e. the first element is the subcommand).
        cp:   The CompletedProcess whose returncode != 0.

    Returns:
        A KamalError subclass with hint and retryable set from heuristics.
    """
    stderr = cp.stderr or ""
    hint = _hint_from_stderr(stderr)
    subcommand = args[0] if args else "unknown"
    message = f"kamal {subcommand} failed (exit {cp.returncode})"

    error_kwargs: dict[str, Any] = {"stderr": stderr, "hint": hint}

    if subcommand == "setup":
        return KamalSetupFailed(message, **error_kwargs)
    if subcommand == "deploy":
        return KamalDeployFailed(
            message, retryable=_is_network_retryable(stderr), **error_kwargs
        )
    if subcommand == "rollback":
        return KamalRollbackFailed(message, **error_kwargs)

    # Generic fallback for other subcommands
    return KamalError(message, code=f"kamal_{subcommand}_failed", **error_kwargs)
