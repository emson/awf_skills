#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31"]
# ///

"""awf-submit-bing — push the project's URLs to Bing IndexNow.

Two stages, gated to keep manual steps explicit (A8):

  1. **Generate key** — if `passport.indexnow_key` is empty, generate
     a UUID-hex key, write it to `static/<key>.txt`, patch the
     passport. The user must then `awf-deploy` so the key file is
     reachable at `https://<domain>/<key>.txt`. The script exits 3
     (manual gate) until the key file is verifiably live.

  2. **Submit** — once the key file is live AND the user has confirmed
     the Bing-Webmaster property exists (manual browser step, gated
     via `--confirm-imported` flag or pre-existing
     `passport.launch.gates.bing_imported`), fetch the sitemap and
     submit URLs to IndexNow.

Idempotent: re-running after a successful submit is safe (IndexNow
accepts repeat submissions). The key file is regenerated only with
`--regenerate-key`.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME / "lib"))

from project import find_project_root, ProjectNotFound  # noqa: E402
from passport import Passport  # noqa: E402
from slug import normalize_domain  # noqa: E402
from bing.indexnow import (  # noqa: E402
    IndexNowError,
    generate_key,
    fetch_sitemap_urls,
    submit_urls,
    key_file_reachable,
    key_file_path_in_project,
)


GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def _color(s: str, c: str) -> str:
    return f"{c}{s}{RESET}" if sys.stdout.isatty() else s


# ── Args ───────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> dict:
    args = argv[1:]
    out = {"confirm_imported": False, "regenerate_key": False}
    bad = [a for a in args if a not in ("--confirm-imported", "--regenerate-key")]
    if bad:
        print(f"unknown argument(s): {' '.join(bad)}", file=sys.stderr)
        print("usage: submit.py [--confirm-imported] [--regenerate-key]", file=sys.stderr)
        sys.exit(2)
    if "--confirm-imported" in args:
        out["confirm_imported"] = True
    if "--regenerate-key" in args:
        out["regenerate_key"] = True
    return out


# ── Stage 1: key + key file ────────────────────────────────────────────


def ensure_key(project_root: Path, passport: Passport, regenerate: bool) -> str:
    """Return the IndexNow key, generating + writing if absent or
    `--regenerate-key`. Always ensures the static/<key>.txt file
    matches what's in passport.indexnow_key.
    """
    if regenerate or not (passport.indexnow_key or "").strip():
        key = generate_key()
        passport.indexnow_key = key
        passport.save(project_root)
        print(_color(f"- generated IndexNow key: {key}", GREEN))
    else:
        key = passport.indexnow_key

    key_file = key_file_path_in_project(project_root, key)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if not key_file.is_file() or key_file.read_text().strip() != key:
        key_file.write_text(key + "\n", encoding="utf-8")
        print(f"- wrote {key_file.relative_to(project_root)}")
    else:
        print(f"- {key_file.relative_to(project_root)} already matches key")

    return key


# ── Stage 2: submit ────────────────────────────────────────────────────


def gate_bing_imported(passport: Passport, confirm_flag: bool) -> bool:
    """Returns True if the manual Bing-Webmaster import gate is
    cleared. Side-effect: marks the gate when `--confirm-imported`
    is passed."""
    if passport.gate_completed("bing_imported"):
        return True
    if confirm_flag:
        passport.mark_gate(
            "bing_imported",
            meta={"confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        )
        return True
    return False


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    try:
        project_root = find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    passport = Passport.load(project_root)
    domain = normalize_domain(passport.domain)

    # ── Stage 1 ────────────────────────────────────────────────────
    key = ensure_key(project_root, passport, opts["regenerate_key"])

    # Verify the key file is live on the deployed site.
    reachable, detail = key_file_reachable(domain, key)
    if not reachable:
        print(_color(f"- key file not reachable: {detail}", YELLOW))
        print(
            f"\nThe IndexNow key file is in your project at "
            f"static/{key}.txt but Bing can't see it yet — you need to "
            f"redeploy.\n"
            f"  cd {project_root}\n"
            f"  awf-deploy\n"
            f"\nThen run `awf-submit-bing` again.",
            file=sys.stderr,
        )
        return 3  # manual gate

    print(_color(f"- key file live: {detail}", GREEN))

    # ── Manual Bing-Webmaster gate ────────────────────────────────
    if not gate_bing_imported(passport, opts["confirm_imported"]):
        print(
            "\nBing Webmaster requires a one-time browser registration that\n"
            "this skill cannot automate (Microsoft OAuth-only):\n\n"
            "  1. Open https://www.bing.com/webmasters\n"
            "  2. Sign in with the Microsoft account that owns this site\n"
            f"  3. Add the property by importing it from Google Search Console\n"
            f"  4. Confirm the sitemap submission for {domain}\n\n"
            "Once done, re-run with --confirm-imported to proceed.",
            file=sys.stderr,
        )
        return 3  # manual gate

    # ── Stage 2 ────────────────────────────────────────────────────
    try:
        urls = fetch_sitemap_urls(domain)
    except IndexNowError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not urls:
        print(_color("- sitemap is empty; nothing to submit", YELLOW))
        passport.mark_gate("submit_bing", meta={"submitted": 0, "outcome": "noop"})
        passport.save(project_root)
        return 0

    print(f"- found {len(urls)} URL(s) in sitemap")
    try:
        submitted = submit_urls(domain, key, urls)
    except IndexNowError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(_color(f"- submitted {submitted} URL(s) to IndexNow", GREEN))
    passport.mark_gate(
        "submit_bing",
        meta={"submitted": submitted, "outcome": "ok"},
    )
    passport.save(project_root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
