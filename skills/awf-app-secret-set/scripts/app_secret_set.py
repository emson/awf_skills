#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///

"""awf-app-secret-set — upsert one KEY=value line in .kamal/secrets.

Never logs the secret value (defence-in-depth). The log event records only
key and source (literal / env / file).

Exit codes:
    0  — success (created, updated, or skipped)
    1  — project not found (no .awf/project.json walking up)
    2  — input/env error (--from-env VAR not set; --from-file PATH not found)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import re
import sys
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402
from lib.state import ProjectAnchor  # noqa: E402

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRETS_REL = Path(".kamal") / "secrets"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="awf-app-secret-set",
        description="Upsert one KEY=value entry in .kamal/secrets.",
    )
    parser.add_argument("--key", required=True, help="Secret key name (e.g. DATABASE_URL).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--value", help="Literal secret value.")
    group.add_argument("--from-env", metavar="VAR", dest="from_env",
                       help="Read value from named environment variable.")
    group.add_argument("--from-file", metavar="PATH", dest="from_file",
                       help="Read value from a file (trailing whitespace stripped).")
    parser.add_argument("--json", action="store_true", default=False,
                        help="Emit machine-readable JSON on stdout.")
    args = parser.parse_args()
    as_json: bool = args.json

    # Locate project root OUTSIDE session.
    try:
        anchor = ProjectAnchor.load()
    except ProjectNotFound as exc:
        print(f"awf-app-secret-set: project not found: {exc}", file=sys.stderr)
        return 1

    root = anchor._path.parent.parent  # type: ignore[union-attr]

    # Resolve the secret value from the chosen source.
    if args.value is not None:
        value = args.value
        source = "literal"
    elif args.from_env is not None:
        env_val = os.environ.get(args.from_env)
        if env_val is None:
            print(
                f"awf-app-secret-set: environment variable {args.from_env!r} is not set",
                file=sys.stderr,
            )
            return 2
        value = env_val
        source = "env"
    else:
        # --from-file
        file_path = Path(args.from_file)
        if not file_path.exists():
            print(
                f"awf-app-secret-set: file not found: {args.from_file}",
                file=sys.stderr,
            )
            return 2
        value = file_path.read_text(encoding="utf-8").rstrip()
        source = "file"

    key = args.key

    # safe_args deliberately omits the value — never log secrets.
    safe_args = {"key": key, "source": source}

    secrets_path = root / _SECRETS_REL
    new_line = f"{key}={value}"

    with log.session(composer="awf-app-secret-set", target="kamal-secrets"):
        with log.invoke(skill="awf-app-secret-set", args=safe_args):
            if secrets_path.exists():
                text = secrets_path.read_text(encoding="utf-8")
                lines = text.splitlines()
            else:
                lines = []

            # Find existing entry for this key.
            idx = next(
                (i for i, ln in enumerate(lines) if ln.split("=", 1)[0] == key),
                None,
            )

            if idx is not None and lines[idx] == new_line:
                action = "skip"
            elif idx is not None:
                lines[idx] = new_line
                # Atomic write via temp-file rename.
                _write_secrets(secrets_path, lines)
                log.file_write(path=str(_SECRETS_REL), bytes=secrets_path.stat().st_size,
                               secret_key_name=key)
                action = "updated"
            else:
                lines.append(new_line)
                _write_secrets(secrets_path, lines)
                log.file_write(path=str(_SECRETS_REL), bytes=secrets_path.stat().st_size,
                               secret_key_name=key)
                action = "created"

    if as_json:
        print(jsonlib.dumps({"action": action, "key": key, "source": source}))
    else:
        print(f"key: {key}")
        print(f"source: {source}")
        print(f"action: {action}")

    return 0


def _write_secrets(path: Path, lines: list[str]) -> None:
    """Atomically write lines to secrets file via temp-file rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    content = "\n".join(lines) + "\n" if lines else ""
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
