#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "pyyaml>=6"]
# ///

"""awf-kamal-config — render config/deploy.yml from Infra state.

Thin wrapper around KamalConfig(anchor, infra).render(path=...).
Records Infra.kamal.config_path on first creation or path change.

Exit codes:
    0  — success (created, updated, or skipped)
    1  — project not found (no .awf/project.json walking up)
    2  — config error (no web server in infra)
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
from lib.kamal.config import KamalConfig  # noqa: E402
from lib.kamal.errors import KamalError  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402
from lib.state import Infra, ProjectAnchor, StateValidationError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-kamal-config",
        description="Render config/deploy.yml and record Infra.kamal.config_path.",
    )
    parser.add_argument(
        "--path",
        default="config/deploy.yml",
        help="Output path relative to project root (default: config/deploy.yml).",
    )
    parser.add_argument("--json", action="store_true", default=False,
                        help="Emit machine-readable JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Locate project root OUTSIDE session.
    try:
        anchor = ProjectAnchor.load()
    except ProjectNotFound as exc:
        print(f"awf-kamal-config: project not found: {exc}", file=sys.stderr)
        return 1

    root = anchor._path.parent.parent  # type: ignore[union-attr]

    safe_args = {"path": args.path}

    try:
        with log.session(composer="awf-kamal-config", target="kamal-config"):
            with log.invoke(skill="awf-kamal-config", args=safe_args):
                infra = Infra.load_or_create(start=root)
                before_path = infra.kamal.config_path

                abs_path = root / args.path

                try:
                    KamalConfig(anchor, infra).render(path=abs_path)
                except KamalError as exc:
                    print(f"awf-kamal-config: config error: {exc}", file=sys.stderr)
                    return 2

                if before_path == args.path:
                    action = "skip"
                else:
                    infra.kamal.config_path = args.path
                    try:
                        infra.save()
                    except StateValidationError as exc:
                        print(f"awf-kamal-config: state validation error: {exc}",
                              file=sys.stderr)
                        return 4
                    action = "created" if not before_path else "updated"

    except StateValidationError as exc:
        print(f"awf-kamal-config: state validation error: {exc}", file=sys.stderr)
        return 4

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "config_path": args.path,
        }))
    else:
        print(f"config_path: {args.path}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
