#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///

"""awf-help — context-aware orientation for the awf-skills suite (plan_013 / C3 / D-008).

Three auto-detected modes:
  1. fresh-start  — no .awf/project.json found walking up from cwd.
  2. in-project   — .awf/project.json found; shows stage, next composer,
                    relevant skills, common operations.
  3. overview     — explicit --overview flag; full stage catalogue.

Read-only. Never mutates state. Never calls external APIs.

Flags
-----
--overview      Full catalogue grouped by stage.
--pipeline      Deprecated alias for --overview (removed in plan_015).
--json          Machine-readable output; includes "schema_version": 1.

Exit codes
----------
0  Always (for any non-usage invocation).
2  Argument parsing error (invalid flag combination).
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path
from typing import Any

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib.stages import (  # noqa: E402
    STAGE_ORDER,
    NEXT_COMPOSERS,
    NEXT_HINTS,
    RELEVANT_SKILLS,
)
from lib.state import ProjectAnchor  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402


# ── Skill description scraping ───────────────────────────────────────────────


def _scrape_skill_description(skill_name: str) -> str:
    """Read the 'description:' frontmatter field from a SKILL.md.

    Falls back to the skill name only if the file is missing or malformed.
    """
    skill_md = AWF_HOME / "skills" / skill_name / "SKILL.md"
    try:
        content = skill_md.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("description:"):
                return stripped[len("description:"):].strip()
        return skill_name
    except Exception:
        return skill_name


def _load_anchor() -> "ProjectAnchor | None":
    """Try to load a ProjectAnchor walking up from cwd. Returns None if none found."""
    try:
        return ProjectAnchor.load(start=Path.cwd())
    except (ProjectNotFound, Exception):
        return None


# ── Render functions ─────────────────────────────────────────────────────────


def render_fresh_start(as_json: bool) -> None:
    if as_json:
        data: dict[str, Any] = {
            "schema_version": 1,
            "mode": "fresh_start",
            "stage": None,
            "project": None,
            "next_composer": None,
            "next_hint": None,
            "relevant_skills": [],
            "common_operations": ["awf-status", "awf-log", "awf-doctor", "awf-teardown"],
            "doc_links": [
                "docs/07-multi-stage-architecture.md",
                "docs/08-logging.md",
            ],
        }
        print(jsonlib.dumps(data, indent=2))
        return

    print("You're not in an awf project.")
    print()
    print("Start a project:")
    print("  /awf-create-project <domain>        Scaffold a project at the current stage.")
    print("  /awf-launch <domain> --keywords \"…\" Run the full landing pipeline end-to-end.")
    print()
    print("Learn the system:")
    print("  /awf-help --overview                Full catalogue grouped by stage.")


def render_in_project(anchor: "ProjectAnchor", as_json: bool) -> None:
    stage = anchor.stage
    composer = NEXT_COMPOSERS.get(stage)
    hint = NEXT_HINTS.get(stage, "")
    skills = RELEVANT_SKILLS.get(stage, [])

    # Anchor path is .awf/project.json; its parent's parent is the project root.
    anchor_path: Path | None = anchor._path
    project_root_str = str(anchor_path.parent.parent) if anchor_path else "(unknown)"

    if as_json:
        data = {
            "schema_version": 1,
            "mode": "in_project",
            "stage": stage,
            "project": {
                "slug": anchor.slug,
                "domain": anchor.domain,
                "root": project_root_str,
            },
            "next_composer": composer,
            "next_hint": hint,
            "relevant_skills": skills,
            "common_operations": ["awf-status", "awf-log", "awf-doctor", "awf-teardown"],
            "doc_links": [
                "docs/07-multi-stage-architecture.md",
                "docs/08-logging.md",
            ],
        }
        print(jsonlib.dumps(data, indent=2))
        return

    print(f"You're at stage: {stage}.")
    print()
    if composer:
        print("To advance:")
        print(f"  /{composer}                    {hint}")
        print()
    print("Atomic skills relevant here:")
    if skills:
        for skill in skills:
            print(f"  /{skill}")
    else:
        print("  (TBD — atomic skills land in plan_014+)")
    print()
    print("Common operations:")
    print("  /awf-status                         Show where this project is.")
    print("  /awf-log tail                       Tail the recent event log.")
    print("  /awf-doctor                         Validate environment + credentials.")
    print("  /awf-teardown                       (deferred — D2 plan)")
    print()
    print("Full catalogue: /awf-help --overview")


def render_overview(as_json: bool) -> None:
    stages_data: list[dict[str, Any]] = []

    for stage in STAGE_ORDER:
        composer = NEXT_COMPOSERS.get(stage)
        hint = NEXT_HINTS.get(stage, "")
        skill_names = RELEVANT_SKILLS.get(stage, [])
        atomic_skills = [
            {"name": s, "description": _scrape_skill_description(s)}
            for s in skill_names
        ]
        stages_data.append({
            "name": stage,
            "next_composer": composer,
            "next_hint": hint,
            "atomic_skills": atomic_skills,
        })

    if as_json:
        data = {
            "schema_version": 1,
            "mode": "overview",
            "stage": None,
            "project": None,
            "next_composer": None,
            "next_hint": None,
            "relevant_skills": [],
            "common_operations": ["awf-status", "awf-log", "awf-doctor", "awf-teardown"],
            "stages": stages_data,
            "doc_links": [
                "docs/07-multi-stage-architecture.md",
                "docs/08-logging.md",
            ],
        }
        print(jsonlib.dumps(data, indent=2))
        return

    for sd in stages_data:
        print(f"=== Stage: {sd['name']} ===")
        if sd["next_composer"]:
            print(f"Next composer: /{sd['next_composer']} ({sd['next_hint']})")
        else:
            print("Next composer: (none — terminal stage)")
        print("Atomic skills:")
        if sd["atomic_skills"]:
            for sk in sd["atomic_skills"]:
                print(f"  /{sk['name']} — {sk['description']}")
        else:
            print("  (TBD — atomic skills land in plan_014+)")
        print()

    print("Common operations: /awf-status, /awf-log, /awf-doctor")
    print("Reference: docs/07-multi-stage-architecture.md, docs/08-logging.md")


# ── Entry ─────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="help.py",
        description="awf-help: context-aware orientation for the awf-skills suite.",
        add_help=False,
    )
    p.add_argument(
        "--overview",
        action="store_true",
        help="Show full catalogue grouped by stage.",
    )
    # --pipeline is a deprecated alias for --overview (removed in plan_015).
    p.add_argument(
        "--pipeline",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON.",
    )
    p.add_argument("--help", "-h", action="help")

    try:
        return p.parse_args(argv[1:])
    except SystemExit:
        sys.exit(2)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    # Handle deprecated --pipeline alias.
    if args.pipeline and not args.overview:
        print(
            "--pipeline is renamed --overview; please update your invocation.",
            file=sys.stderr,
        )
        args.overview = True

    if args.overview:
        render_overview(args.as_json)
        return 0

    anchor = _load_anchor()
    if anchor is None:
        render_fresh_start(args.as_json)
        return 0

    render_in_project(anchor, args.as_json)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
