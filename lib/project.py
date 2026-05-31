"""Locate the website project from the current working directory.

Per A1: the project is the cwd; the contract is `passport.json`. We walk
up looking for it.
"""

from __future__ import annotations

from pathlib import Path


class ProjectNotFound(RuntimeError):
    pass


PASSPORT_FILENAME = "passport.json"


def find_project_root(start: Path | None = None, *, optional: bool = False) -> Path | None:
    """Walk up from `start` (default: cwd) to find a directory containing
    `passport.json`. Returns the project root, or raises (or returns None
    if `optional`).
    """
    here = (start or Path.cwd()).resolve()
    for parent in [here, *here.parents]:
        if (parent / PASSPORT_FILENAME).is_file():
            return parent
    if optional:
        return None
    raise ProjectNotFound(
        "No passport.json found in the current directory or any parent. "
        "Run `awf-create-project` from where you want the project, or "
        "`cd` into an existing project."
    )


if __name__ == "__main__":
    root = find_project_root(optional=True)
    print(root or "(not in a project)")
