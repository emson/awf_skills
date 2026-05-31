"""lib/kamal — Kamal deploy library for awf-skills.

Public surface::

    from lib.kamal import KamalConfig, KamalRunner
    from lib.kamal import (
        KamalError, KamalNotInstalled, KamalDnsTimeout,
        KamalSetupFailed, KamalDeployFailed, KamalRollbackFailed,
    )

References:
  - docs/plans/plan_007_s3_kamal_lib.md
  - docs/spec.md § B3
  - ADR D-001 (kamal as deploy abstraction for S3-S5)
"""

from lib.kamal.config import KamalConfig
from lib.kamal.errors import (
    KamalDeployFailed,
    KamalDnsTimeout,
    KamalError,
    KamalNotInstalled,
    KamalRollbackFailed,
    KamalSetupFailed,
)
from lib.kamal.runner import KamalRunner

__all__ = [
    "KamalConfig",
    "KamalRunner",
    "KamalError",
    "KamalNotInstalled",
    "KamalDnsTimeout",
    "KamalSetupFailed",
    "KamalDeployFailed",
    "KamalRollbackFailed",
]
