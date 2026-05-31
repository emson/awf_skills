"""DNS polling helpers for Kamal setup gate (D-001 op rule #1).

This module implements the DnsResolver protocol and two concrete implementations:
  - DigResolver: shells out to ``dig +short`` (production default).
  - FakeResolver: returns scripted answers (test injection).

References:
  - docs/plans/plan_007_s3_kamal_lib.md § "DNS polling"
  - ADR D-001: Rule 1 (DNS-before-TLS) must be encoded inside KamalRunner.setup()

Subprocess exemption note
-------------------------
``DigResolver._run_dig()`` uses ``subprocess.run`` directly rather than
routing through ``KamalRunner._run_kamal()``.  This is intentional and
documented:

  1. ``DigResolver`` exists *to gate* ``kamal setup`` — it must be callable
     before any KamalRunner instance is available.
  2. ``dig`` is a read-only probe (no side effects, no state mutation).
  3. It predates ``kamal setup`` in the call order, making it architecturally
     inappropriate to route through the kamal subprocess chokepoint.

The grep test ``grep -rnE "subprocess.(run|Popen|call|check_output)" lib/kamal/``
is expected to return matches in BOTH ``runner.py`` and this file.

# TODO(awf-doctor): add `dig` presence check
"""

from __future__ import annotations

import subprocess
import time
from typing import Protocol


# ---------------------------------------------------------------------------
# DnsResolver protocol
# ---------------------------------------------------------------------------


class DnsResolver(Protocol):
    """Injectable DNS resolver used by KamalRunner.setup()."""

    def wait_for(
        self,
        *,
        domain: str,
        expected_ip: str,
        timeout_s: float,
        interval_s: float,
    ) -> bool:
        """Poll until ``domain`` resolves to ``expected_ip``, or timeout.

        Args:
            domain:      The fully-qualified domain name to resolve.
            expected_ip: The IPv4 address we must see before proceeding.
            timeout_s:   Maximum wall-clock seconds to wait.
            interval_s:  Seconds between polls.

        Returns:
            True if the domain resolved to expected_ip within timeout_s;
            False on timeout.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# DigResolver — production implementation
# ---------------------------------------------------------------------------


class DigResolver:
    """DNS resolver that shells out to ``dig +short <domain>`` on a poll loop.

    Requires ``dig`` to be on PATH (part of BIND / bind-utils / dnsutils).
    The AWF doctor validates this; the FakeResolver is used in tests.

    # TODO(awf-doctor): add `dig` presence check
    """

    def _run_dig(self, domain: str) -> str:
        """Invoke ``dig +short <domain>`` and return stdout.

        Returns an empty string on any failure (non-zero exit, missing binary,
        etc.) so the poll loop treats it as "not yet resolved."

        Note: subprocess.run is used here directly — see module docstring
        for the documented exemption from the _run_kamal chokepoint.
        """
        try:
            cp = subprocess.run(
                ["dig", "+short", domain],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            return cp.stdout.strip() if cp.returncode == 0 else ""
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return ""

    def wait_for(
        self,
        *,
        domain: str,
        expected_ip: str,
        timeout_s: float,
        interval_s: float,
    ) -> bool:
        """Poll ``dig +short <domain>`` until it returns ``expected_ip`` or timeout.

        Logs nothing here — KamalRunner.setup() owns the gate event.

        Returns:
            True if resolved; False on timeout.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            result = self._run_dig(domain)
            # dig +short may return multiple lines; check each
            if expected_ip in result.splitlines():
                return True
            time.sleep(interval_s)
        # Final check after last sleep
        result = self._run_dig(domain)
        return expected_ip in result.splitlines()


# ---------------------------------------------------------------------------
# FakeResolver — test injection
# ---------------------------------------------------------------------------


class FakeResolver:
    """Scripted DNS resolver for tests.

    ``sequence`` is a list of booleans: each ``wait_for`` call pops the next
    value and returns it immediately (no sleeping). If the sequence is
    exhausted, subsequent calls return False.

    Usage::

        resolver = FakeResolver(sequence=[False, False, True])
        # First two calls return False, third returns True.
    """

    def __init__(self, sequence: list[bool]) -> None:
        self._sequence = list(sequence)

    def wait_for(
        self,
        *,
        domain: str,
        expected_ip: str,
        timeout_s: float,
        interval_s: float,
    ) -> bool:
        """Return the next scripted answer (no I/O, no sleeping)."""
        if self._sequence:
            return self._sequence.pop(0)
        return False
