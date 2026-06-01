#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "pyyaml>=6"]
# ///

"""awf-kamal-deploy — run `kamal deploy` and record last_deploy_image.

Wraps KamalRunner.deploy(). Updates Infra.kamal.last_deploy_image on success.
action reflects state-file delta (not whether kamal actually changed anything).

Exit codes:
    0  — success (created, updated, or skipped)
    1  — project not found (no .awf/project.json walking up)
    2  — kamal CLI not on PATH (KamalNotInstalled)
    3  — remote error (KamalDeployFailed)
    4  — state validation failure (StateValidationError; rare)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.kamal.errors import KamalDeployFailed, KamalNotInstalled  # noqa: E402
from lib.kamal.runner import KamalRunner  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402
from lib.state import Infra, ProjectAnchor, StateValidationError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-kamal-deploy",
        description="Run `kamal deploy` and record Infra.kamal.last_deploy_image.",
    )
    parser.add_argument("--json", action="store_true", default=False,
                        help="Emit machine-readable JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Locate project root OUTSIDE session.
    try:
        anchor = ProjectAnchor.load()
    except ProjectNotFound as exc:
        print(f"awf-kamal-deploy: project not found: {exc}", file=sys.stderr)
        return 1

    root = anchor._path.parent.parent  # type: ignore[union-attr]

    try:
        with log.session(composer="awf-kamal-deploy", target="kamal-deploy"):
            with log.invoke(skill="awf-kamal-deploy", args={}):
                infra = Infra.load_or_create(start=root)
                before_image = infra.kamal.last_deploy_image
                deployed_image = infra.registry.image  # what kamal will deploy

                runner = KamalRunner(cwd=root)
                runner.deploy()

                if before_image == deployed_image:
                    action = "skip"
                else:
                    infra.kamal.last_deploy_image = deployed_image
                    try:
                        infra.save()
                    except StateValidationError as exc:
                        print(f"awf-kamal-deploy: state validation error: {exc}",
                              file=sys.stderr)
                        return 4
                    action = "created" if not before_image else "updated"

    except KamalNotInstalled as exc:
        print(f"awf-kamal-deploy: {exc}", file=sys.stderr)
        return 2
    except KamalDeployFailed as exc:
        print(f"awf-kamal-deploy: {exc}", file=sys.stderr)
        return 3
    except StateValidationError as exc:
        print(f"awf-kamal-deploy: state validation error: {exc}", file=sys.stderr)
        return 4

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "last_deploy_image": deployed_image,
        }))
    else:
        print(f"last_deploy_image: {deployed_image or '(none)'}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
