"""Locate the awf-skills repo at runtime.

Resolution order:
  1. $AWF_HOME (if set and exists)
  2. ~/.claude/awf-skills/ (conventional install path)
  3. Walk up from the caller's file looking for our own marker
     (a directory containing both `lib/awf_home.py` and `skills/`).
  4. Raise.

Imported by every uv-script via the bootstrap block. Keep dependency-free
and standard-library-only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class AwfHomeNotFound(RuntimeError):
    pass


def _looks_like_repo(p: Path) -> bool:
    return (p / "lib" / "awf_home.py").is_file() and (p / "skills").is_dir()


def find_awf_home(start: Path | None = None) -> Path:
    env = os.environ.get("AWF_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        if _looks_like_repo(p):
            return p

    conventional = Path("~/.claude/awf-skills").expanduser().resolve()
    if _looks_like_repo(conventional):
        return conventional

    # Walk up from caller. The caller's file path is the most reliable
    # anchor when running via `uv run` from anywhere.
    anchor = (start or Path(sys.argv[0])).resolve()
    for parent in [anchor, *anchor.parents]:
        if _looks_like_repo(parent):
            return parent

    raise AwfHomeNotFound(
        "Could not locate awf-skills. Set $AWF_HOME, install at "
        "~/.claude/awf-skills/, or run from inside the repo."
    )


if __name__ == "__main__":
    print(find_awf_home())
