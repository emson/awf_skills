#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-deploy — build and deploy a project to Cloudflare Pages.

Steps:
  1. verify wrangler is on PATH and `wrangler whoami` succeeds
  2. warn (not block) if the working tree has uncommitted changes
  3. `npm run build`
  4. `npx wrangler pages deploy`

Pages project name and output directory come from the project's
`wrangler.toml`, which the template ships with.
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
from passport import Passport  # noqa: E402


def _check_wrangler() -> int | None:
    if shutil.which("wrangler") is None and shutil.which("npx") is None:
        print("error: neither wrangler nor npx on PATH.", file=sys.stderr)
        return 1
    # Prefer the global `wrangler` if installed; else use `npx wrangler`.
    cmd = ["wrangler"] if shutil.which("wrangler") else ["npx", "wrangler"]
    r = subprocess.run([*cmd, "whoami"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(
            f"error: `{' '.join(cmd)} whoami` failed:\n{r.stderr.strip()}\n"
            f"Run `wrangler login` and try again.",
            file=sys.stderr,
        )
        return 1
    first = next((ln for ln in r.stdout.splitlines() if ln.strip()), "")
    print(f"- wrangler authed: {first.strip()[:120]}")
    return None


def _git_dirty_warn(root: Path) -> None:
    if shutil.which("git") is None or not (root / ".git").exists():
        return
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root, capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0 and r.stdout.strip():
        n = len(r.stdout.strip().splitlines())
        print(f"- WARN: {n} uncommitted change(s) — deploying anyway")


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: deploy.py", file=sys.stderr)
        return 2

    try:
        root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not (root / "package.json").is_file():
        print(f"error: no package.json in {root}", file=sys.stderr)
        return 1

    # Pages project name comes from passport (derived from domain),
    # not from a per-project wrangler.toml. This keeps the template
    # generic — see docs/06-experimentation-guide.md §why-no-wrangler-toml.
    try:
        passport = Passport.load(root)
    except Exception as e:
        print(f"error: could not load passport: {e}", file=sys.stderr)
        return 1
    project_name = passport.project_name

    rc = _check_wrangler()
    if rc is not None:
        return rc

    _git_dirty_warn(root)

    print(f"- npm run build in {root}")
    r = subprocess.run(["npm", "run", "build"], cwd=root)
    if r.returncode != 0:
        print(f"error: npm run build failed (exit {r.returncode})", file=sys.stderr)
        return r.returncode

    # Pages reads the build output from `dist/` (the template's
    # convention; the build script writes there).
    print(f"- wrangler pages deploy --project-name={project_name} in {root}")
    deploy_cmd = ["wrangler"] if shutil.which("wrangler") else ["npx", "wrangler"]
    r = subprocess.run(
        [*deploy_cmd, "pages", "deploy", "dist",
         f"--project-name={project_name}"],
        cwd=root,
    )
    if r.returncode != 0:
        print(f"error: wrangler pages deploy failed (exit {r.returncode})", file=sys.stderr)
        return r.returncode

    passport.mark_gate("deploy", meta={"outcome": "ok", "project_name": project_name})
    passport.save(root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
