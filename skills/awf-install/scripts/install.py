#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-install — `npm install` in the project root.

Streams output (no capture) — installs are slow and the user should
see progress live.
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


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: install.py", file=sys.stderr)
        return 2

    try:
        root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not (root / "package.json").is_file():
        print(
            f"error: no package.json in {root}. Was this project scaffolded "
            "via awf-create-project?",
            file=sys.stderr,
        )
        return 1

    if shutil.which("npm") is None:
        print("error: npm not on PATH. Install Node.js (https://nodejs.org).", file=sys.stderr)
        return 1

    print(f"- npm install in {root}")
    proc = subprocess.run(["npm", "install"], cwd=root)
    if proc.returncode != 0:
        print(f"error: npm install failed (exit {proc.returncode})", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
