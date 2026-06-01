#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///

"""awf-app-dockerize — scaffold Dockerfile, .dockerignore, /up route, lib/db.ts.

Writes four files into the project tree to prepare it for Kamal deployment.
Template content is versioned as DOCKERIZE_VERSION. Files with matching
on-disk content are skipped; user edits are never clobbered (drift reported).

Exit codes:
    0  — success (created or skipped)
    1  — project not found (no .awf/project.json walking up)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-app-dockerize/scripts/app_dockerize.py
#   parents[0] = .../awf-app-dockerize/scripts
#   parents[1] = .../awf-app-dockerize
#   parents[2] = .../skills
#   parents[3] = AWF_HOME (repo root)
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402
from lib.state import ProjectAnchor  # noqa: E402

# ---------------------------------------------------------------------------
# Template constants (versioned)
# ---------------------------------------------------------------------------

DOCKERIZE_VERSION = "1"

# Embedded templates — version string in Dockerfile first-line comment so a
# future skill iteration can detect "v1 file, safe to upgrade to v2".
DOCKERFILE_TEMPLATE = """\
# awf-dockerize v{ver}
FROM node:{node}-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .
EXPOSE {port}
CMD ["node", "build"]
"""

DOCKERIGNORE = "node_modules\n.git\n.svelte-kit\nbuild\n.env*\n"

UP_ROUTE = "export const GET = () => new Response('OK');\n"

DB_TS = (
    "import pg from 'pg';\n"
    "export const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-app-dockerize",
        description="Scaffold Dockerfile, .dockerignore, /up route, and lib/db.ts.",
    )
    parser.add_argument("--port", default="3000", help="Port interpolated into EXPOSE.")
    parser.add_argument("--node-version", default="20", dest="node_version",
                        help="Node.js version for FROM node:<version>-slim.")
    parser.add_argument("--json", action="store_true", default=False,
                        help="Emit machine-readable JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Locate project root OUTSIDE session so missing-project doesn't open an
    # orphan session.start/session.end pair.
    try:
        anchor = ProjectAnchor.load()
    except ProjectNotFound as exc:
        print(f"awf-app-dockerize: project not found: {exc}", file=sys.stderr)
        return 1

    root = anchor._path.parent.parent  # type: ignore[union-attr]  # .awf/project.json → project root

    safe_args = {
        "port": args.port,
        "node_version": args.node_version,
    }

    dockerfile_content = DOCKERFILE_TEMPLATE.format(
        ver=DOCKERIZE_VERSION,
        node=args.node_version,
        port=args.port,
    )

    plan = [
        ("Dockerfile", dockerfile_content),
        (".dockerignore", DOCKERIGNORE),
        ("src/routes/up/+server.ts", UP_ROUTE),
        ("lib/db.ts", DB_TS),
    ]

    wrote: list[str] = []
    skipped: list[str] = []
    drift: list[str] = []

    with log.session(composer="awf-app-dockerize", target="app-files"):
        with log.invoke(skill="awf-app-dockerize", args=safe_args):
            for rel, content in plan:
                p = root / rel
                if p.exists():
                    if p.read_text(encoding="utf-8") == content:
                        skipped.append(rel)
                    else:
                        drift.append(rel)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")
                    log.file_write(path=rel, bytes=len(content.encode("utf-8")))
                    wrote.append(rel)

    action = "created" if wrote else "skip"

    if as_json:
        print(jsonlib.dumps({
            "action": action,
            "wrote": wrote,
            "skipped": skipped,
            "drift": drift,
            "dockerize_version": DOCKERIZE_VERSION,
        }))
    else:
        if wrote:
            for f in wrote:
                print(f"  wrote: {f}")
        if skipped:
            for f in skipped:
                print(f"  skipped: {f}")
        if drift:
            for f in drift:
                print(f"  drift (user edit preserved): {f}")
        print(f"action: {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
