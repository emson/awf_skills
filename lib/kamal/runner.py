"""KamalRunner: subprocess wrapper around the kamal CLI.

All subprocess invocations go through _run_kamal — the single chokepoint.
No other function in lib/kamal/ calls subprocess directly (dns.py has a
documented exemption for DigResolver._run_dig).

References:
  - docs/plans/plan_007_s3_kamal_lib.md § "KamalRunner"
  - ADR D-001: kamal as deploy abstraction for S3-S5
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from lib import log
from lib.kamal.dns import DigResolver, DnsResolver
from lib.kamal.errors import KamalDnsTimeout, KamalNotInstalled, translate


# ---------------------------------------------------------------------------
# KamalRunner
# ---------------------------------------------------------------------------


class KamalRunner:
    """Subprocess wrapper around the kamal CLI.

    All state mutations (setup, deploy, rollback) and the one read-only
    operation (app_logs) go through this class. Every subprocess call is
    routed through ``_run_kamal``, which emits a ``process.invoke`` log event
    and translates non-zero exit codes to typed KamalError subclasses.

    The DNS gate is encoded inside ``setup()``: ``kamal setup`` is never
    invoked until the A-record for ``domain`` resolves to ``server_ip``.
    This encodes D-001 op rule #1 (DNS-before-TLS) and cannot be bypassed
    by callers.
    """

    def __init__(
        self,
        *,
        cwd: Path,
        dns_resolver: DnsResolver | None = None,
        dns_timeout_s: float = 600.0,
        dns_interval_s: float = 5.0,
    ) -> None:
        """Initialise KamalRunner.

        Args:
            cwd:           Working directory for all kamal invocations.
                           Must contain the kamal config file (config/deploy.yml).
            dns_resolver:  Injectable resolver; defaults to DigResolver (real dig).
            dns_timeout_s: Maximum seconds to wait for DNS propagation in setup().
            dns_interval_s: Seconds between DNS polls in setup().
        """
        self._cwd = cwd
        self._dns: DnsResolver = dns_resolver if dns_resolver is not None else DigResolver()
        self._dns_timeout_s = dns_timeout_s
        self._dns_interval_s = dns_interval_s

    # ------------------------------------------------------------------
    # Single chokepoint
    # ------------------------------------------------------------------

    def _run_kamal(
        self,
        args: list[str],
        *,
        capture_output: bool = True,
        check: bool = True,
    ) -> "subprocess.CompletedProcess[str]":
        """Invoke ``kamal <args>``; emit log.process; translate errors.

        What it does:
            Executes ``["kamal"] + args`` as a subprocess in ``self._cwd``.
            Captures stdout and stderr (unless capture_output=False for
            streaming).  Emits exactly one ``process.invoke`` log event.
            On non-zero exit, emits an additional ``log.error`` event then
            raises the appropriate KamalError subclass via ``translate()``.

        What it logs:
            - Always: one ``process.invoke`` event with cmd, exit_code,
              duration_ms, cwd.
            - On failure: one ``error`` event with the hint heuristic.

        What it raises:
            - KamalNotInstalled: if the kamal binary is not on PATH.
            - KamalSetupFailed / KamalDeployFailed / KamalRollbackFailed:
              for known subcommand failures.
            - KamalError: for unknown subcommands.

        Idempotency: not applicable — this is a transport function.
        """
        cmd = ["kamal", *args]
        t0 = time.monotonic()
        try:
            cp = subprocess.run(
                cmd,
                cwd=self._cwd,
                capture_output=capture_output,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise KamalNotInstalled() from exc

        dur_ms = int((time.monotonic() - t0) * 1000)
        log.process(cmd=cmd, exit_code=cp.returncode, duration_ms=dur_ms, cwd=str(self._cwd))

        if check and cp.returncode != 0:
            err = translate(args, cp)
            log.error(msg=f"kamal {args[0] if args else 'unknown'} failed", hint=err.hint)
            raise err

        return cp

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def setup(self, *, domain: str, server_ip: str) -> None:
        """Poll DNS then run ``kamal setup`` (D-001 op rule #1).

        Logs: gate.hit on DNS timeout; process.invoke on kamal call; error on failure.
        Raises: KamalDnsTimeout, KamalSetupFailed, KamalNotInstalled.
        Idempotency: kamal setup is idempotent for existing setups.
        """
        resolved = self._dns.wait_for(
            domain=domain,
            expected_ip=server_ip,
            timeout_s=self._dns_timeout_s,
            interval_s=self._dns_interval_s,
        )
        if not resolved:
            log.gate(
                name="dns_propagation",
                reason=(
                    f"A-record for {domain!r} did not resolve to {server_ip!r} "
                    f"within {self._dns_timeout_s}s"
                ),
                instructions=(
                    f"Check the CF DNS record for {domain} is set to {server_ip} "
                    f"and grey-cloud. Re-run once `dig +short {domain}` returns "
                    f"{server_ip}."
                ),
            )
            raise KamalDnsTimeout(domain=domain, expected_ip=server_ip)
        self._run_kamal(["setup"])

    def deploy(self) -> None:
        """Build and deploy the current image (``kamal deploy``).

        Logs: process.invoke; error on failure.
        Raises: KamalDeployFailed, KamalNotInstalled.
        Idempotency: re-deploying the same image is safe.
        """
        self._run_kamal(["deploy"])

    def rollback(self, *, to: str | None = None) -> None:
        """Roll back to the previous deploy or a specific image tag.

        Logs: process.invoke; error on failure.
        Raises: KamalRollbackFailed, KamalNotInstalled.
        Idempotency: rolling back to the same tag is safe.
        """
        args = ["rollback"]
        if to is not None:
            args.append(to)
        self._run_kamal(args)

    def app_logs(self, *, tail: int = 200) -> str:
        """Fetch the last ``tail`` lines from the running app container.

        Logs: process.invoke; error on failure.
        Raises: KamalError, KamalNotInstalled.
        Idempotency: read-only; always safe to re-run.
        """
        cp = self._run_kamal(["app", "logs", "--lines", str(tail)])
        return cp.stdout
