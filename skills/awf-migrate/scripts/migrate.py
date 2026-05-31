#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///

"""awf-migrate — one-shot migration from passport-only to dual-file layout.

Wraps lib.project.ensure_anchor() to upgrade a legacy project (passport.json
only) to the dual-file layout (.awf/project.json + passport.json). Idempotent:
running on an already-migrated project is a no-op.

References: spec.md § A4, plan_004_foundation_migrate_skill.md.

Exit codes:
    0  — success (migrated or already migrated)
    1  — project not found (no passport.json or .awf/project.json in walk)
    2  — I/O failure (read-only filesystem, malformed passport.json, etc.)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-migrate/scripts/migrate.py
#   parents[0] = .../awf-migrate/scripts
#   parents[1] = .../awf-migrate
#   parents[2] = .../skills
#   parents[3] = AWF_HOME (repo root)
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from project import (  # noqa: E402
    ProjectNotFound,
    ensure_anchor,
    find_project_root,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-migrate",
        description="Migrate a legacy passport-only project to dual-file layout.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON on stdout.",
    )
    args = parser.parse_args()
    as_json: bool = args.json

    # ── Step 1: locate project root (outside session — not-found exits before
    # session opens, so ProjectNotFound is deliberately outside log.session) ──
    try:
        root = find_project_root()
    except ProjectNotFound as exc:
        print(
            f"awf-migrate: project not found: {exc}\n"
            "Run from a directory containing passport.json or .awf/project.json,\n"
            "or run awf-create-project to create a new project.",
            file=sys.stderr,
        )
        return 1

    # ── Step 2: probe anchor presence BEFORE calling ensure_anchor ────────────
    # Pre-call probe is the only way to detect no-op, because ensure_anchor
    # returns the loaded-or-created anchor unconditionally. This is not a
    # TOCTOU risk — concurrent migration is not a supported workflow, and
    # ensure_anchor itself re-checks is_file() before writing.
    anchor_pre_exists: bool = (root / ".awf" / "project.json").is_file()

    # ── Step 3: open log session (session boundary wraps the I/O work only) ──
    with log.session(composer="awf-migrate", target="anchor"):
        try:
            anchor = ensure_anchor(root)
        except OSError as exc:
            print(f"awf-migrate: I/O failure: {exc}", file=sys.stderr)
            sys.exit(2)
        except Exception as exc:  # noqa: BLE001
            print(f"awf-migrate: migration failed: {exc}", file=sys.stderr)
            sys.exit(2)

        # ── Step 4: emit user-visible output ─────────────────────────────────
        anchor_path = root / ".awf" / "project.json"

        if as_json:
            # domain and slug source: on no-op the anchor was loaded from
            # .awf/project.json (carries domain/slug); on migration the anchor
            # was just created from passport.json. Either way anchor has them.
            action = "no-op" if anchor_pre_exists else "migrated"
            output = {
                "action": action,
                "anchor_path": str(anchor_path),
                "domain": anchor.domain,
                "slug": anchor.slug,
            }
            print(jsonlib.dumps(output))
        else:
            if anchor_pre_exists:
                print(f"already migrated: {anchor_path}")
            else:
                print(f"migrated: {anchor_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
