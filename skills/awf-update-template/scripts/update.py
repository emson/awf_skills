#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-update-template — re-overlay a template version onto an existing
project, preserving content per the template's preserve-list.

Steps:
  1. Load passport; require a project root.
  2. Resolve target template (default: latest of the same name).
  3. Refuse without `--force` if target ≤ current `passport.template_version`.
  4. Refuse if target's `passport_schema` is newer than what we
     understand (caller must update awf-skills first).
  5. With `--dry-run`, print the change summary and exit.
  6. Apply the overlay; bump `passport.template_version`.
  7. If a git repo, commit "template: vX.Y → vN.M".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME / "lib"))

from project import find_project_root, ProjectNotFound  # noqa: E402
from passport import Passport, SCHEMA_VERSION_CURRENT  # noqa: E402
from awf_home import find_awf_home, AwfHomeNotFound  # noqa: E402
from templates import (  # noqa: E402
    resolve_template,
    apply_overlay,
    TemplateNotFound,
    TemplateError,
    _version_tuple,
)


GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def _color(s: str, c: str) -> str:
    return f"{c}{s}{RESET}" if sys.stdout.isatty() else s


def parse_args(argv: list[str]) -> dict:
    args = argv[1:]
    out: dict = {"to": None, "dry_run": False, "force": False}
    while args:
        a = args.pop(0)
        if a == "--to":
            out["to"] = args.pop(0) if args else None
        elif a == "--dry-run":
            out["dry_run"] = True
        elif a == "--force":
            out["force"] = True
        else:
            print(f"unknown argument: {a}", file=sys.stderr)
            print(
                "usage: update.py [--to <name|dir>] [--dry-run] [--force]",
                file=sys.stderr,
            )
            sys.exit(2)
    return out


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    try:
        project_root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        awf_home = find_awf_home()
    except AwfHomeNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    passport = Passport.load(project_root)

    try:
        target = resolve_template(awf_home, name_or_dir=opts["to"])
    except (TemplateNotFound, TemplateError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    current_v = passport.template_version or "0.0"
    print(f"- current template_version: {current_v}")
    print(f"- target template:          {target.name} v{target.version}")

    # Schema-compatibility check (A11).
    if _version_tuple(target.passport_schema) > _version_tuple(SCHEMA_VERSION_CURRENT):
        print(
            f"error: target template requires passport schema "
            f"{target.passport_schema}, but this awf-skills understands "
            f"{SCHEMA_VERSION_CURRENT}. Update awf-skills first.",
            file=sys.stderr,
        )
        return 1

    # Version comparison.
    try:
        is_upgrade = _version_tuple(target.version) > _version_tuple(current_v)
    except ValueError:
        is_upgrade = True  # malformed current version; treat as upgradable

    if not is_upgrade and not opts["force"]:
        print(
            _color(
                f"- nothing to do: target v{target.version} ≤ current v{current_v}. "
                f"Pass --force to re-apply or downgrade.",
                YELLOW,
            )
        )
        return 0

    changes = apply_overlay(target, project_root, dry_run=True)
    by_kind: dict[str, list[str]] = {}
    for c in changes:
        by_kind.setdefault(c.kind, []).append(c.relpath)

    print()
    for kind in ("create", "modify", "preserved", "unchanged"):
        files = by_kind.get(kind, [])
        if not files:
            continue
        print(f"  {kind:10s} ({len(files)})")
        if kind in ("create", "modify"):
            for rel in sorted(files)[:20]:
                print(f"    {rel}")
            if len(files) > 20:
                print(f"    … and {len(files) - 20} more")
    print()

    if opts["dry_run"]:
        print(_color("dry-run; no files changed.", YELLOW))
        return 0

    if (by_kind.get("create") or by_kind.get("modify")):
        apply_overlay(target, project_root, dry_run=False)

    passport.template_version = target.version
    passport.save(project_root)
    print(_color(f"- template_version: {current_v} → {target.version}", GREEN))

    # Git commit (best-effort).
    if shutil.which("git") and (project_root / ".git").exists():
        try:
            subprocess.run(["git", "add", "."], cwd=project_root, check=True)
            r = subprocess.run(
                ["git", "diff", "--cached", "--quiet"], cwd=project_root,
            )
            if r.returncode != 0:  # there are staged changes
                subprocess.run(
                    ["git", "commit", "-q", "-m",
                     f"template: v{current_v} → v{target.version}"],
                    cwd=project_root, check=True,
                )
                print(f"- git: committed template upgrade")
        except subprocess.CalledProcessError as e:
            print(f"- WARN: git commit failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
