"""lib/neon — Neon database API client for awf-skills.

Public surface::

    from lib.neon import NeonClient, NeonConfig
    from lib.neon import (
        NeonError, NeonNotFound, NeonConflict,
        NeonRateLimited, NeonAuthError, NeonNetworkError,
    )
    from lib.neon.resources.projects import Project
    from lib.neon.resources.branches import Branch

References:
  - docs/plans/plan_006_s3_neon_lib.md
  - docs/spec.md § B2
"""

from lib.neon.client import NeonClient, NeonConfig
from lib.neon.errors import (
    NeonAuthError,
    NeonConflict,
    NeonError,
    NeonNetworkError,
    NeonNotFound,
    NeonRateLimited,
)
from lib.neon.resources.branches import Branch, _Branches
from lib.neon.resources.projects import Project, _Projects

__all__ = [
    "NeonClient",
    "NeonConfig",
    "NeonError",
    "NeonNotFound",
    "NeonConflict",
    "NeonRateLimited",
    "NeonAuthError",
    "NeonNetworkError",
    "Project",
    "Branch",
    "_Projects",
    "_Branches",
]
