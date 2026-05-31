"""Locate the website project from the current working directory.

Per A1: the project is the cwd; the contract is `passport.json` or
`.awf/project.json`. We walk up looking for either, preferring the
anchor when both are present in the same directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    # deferred import: avoid cycle with lib.state at module import
    from lib.state import ProjectAnchor


class ProjectNotFound(RuntimeError):
    pass


PASSPORT_FILENAME = "passport.json"
AWF_DIRNAME = ".awf"
PROJECT_FILENAME = "project.json"


@overload
def find_project_root(
    start: Path | None = ..., *, optional: Literal[False] = ...
) -> Path: ...


@overload
def find_project_root(
    start: Path | None = ..., *, optional: Literal[True]
) -> Path | None: ...


def find_project_root(
    start: Path | None = None, *, optional: bool = False
) -> Path | None:
    """Walk up from `start` (default: cwd) to find a directory containing
    `.awf/project.json` or `passport.json`. Returns the project root,
    or raises (or returns None if `optional`).

    Prefers `.awf/project.json` when both exist in the same directory.
    The first matching directory on the walk wins; "preference" is
    per-directory only.
    """
    here = (start or Path.cwd()).resolve()
    walked: list[Path] = [here, *here.parents]
    for parent in walked:
        if (parent / AWF_DIRNAME / PROJECT_FILENAME).is_file():
            return parent
        if (parent / PASSPORT_FILENAME).is_file():
            return parent
    if optional:
        return None
    walked_str = ", ".join(str(p) for p in walked[:5])
    raise ProjectNotFound(
        f"No .awf/project.json or passport.json found from {here} "
        f"(walked {len(walked)} directories: {walked_str}...). "
        "Run `awf-create-project` from where you want the project, or "
        "`cd` into an existing project."
    )


def find_anchor_state(start: Path | None = None) -> tuple[Path, bool]:
    """Return (root, anchor_missing) for the project found from start.

    Raises ProjectNotFound if no project root is found (same as
    find_project_root with optional=False).

    anchor_missing=True means only passport.json was found; the
    .awf/project.json anchor has not been created yet.
    anchor_missing=False means .awf/project.json exists at root.
    """
    root = find_project_root(start)
    anchor = root / AWF_DIRNAME / PROJECT_FILENAME
    return root, not anchor.is_file()


def ensure_anchor(root: Path) -> ProjectAnchor:
    """Idempotent: create .awf/project.json from passport.json if missing.

    Returns the loaded (or newly created) ProjectAnchor. Does not
    mutate passport.json.

    Raises:
        ProjectNotFound: if neither .awf/project.json nor passport.json
            exists at root.
    """
    # deferred import: avoid cycle with lib.state at module import
    from lib.state import AWF_VERSION, HasFlags, ProjectAnchor  # noqa: PLC0415

    anchor_path = root / AWF_DIRNAME / PROJECT_FILENAME
    if anchor_path.is_file():
        return ProjectAnchor.load(start=root)

    passport_path = root / PASSPORT_FILENAME
    if not passport_path.is_file():
        raise ProjectNotFound(
            f"ensure_anchor: neither .awf/project.json nor passport.json "
            f"found at {root}"
        )

    # deferred import: avoid cycle with lib.passport at module import
    from lib.passport import Passport  # noqa: PLC0415

    passport = Passport.load(root)

    def _now_utc() -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    anchor = ProjectAnchor(
        awf_version=AWF_VERSION,
        domain=passport.domain,
        slug=passport.project_name,
        stage="landing",
        created=_now_utc(),
        has=HasFlags(passport=True),
    )
    # Same private-attr assignment as in ProjectAnchor.load() — established
    # deviation per plan_001 code review N3, accepted because ProjectAnchor
    # has no public path property. Not a bug; do not refactor here.
    anchor._path = anchor_path
    anchor.save()
    return anchor


if __name__ == "__main__":
    try:
        root, anchor_missing = find_anchor_state()
        print(f"{root}  anchor_missing={anchor_missing}")
    except ProjectNotFound:
        print("(not in a project)")
