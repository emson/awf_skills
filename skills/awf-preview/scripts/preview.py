#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-preview — deploy an artifact to a Cloudflare preview URL.

Auth-aware: the mode is chosen automatically from the wrangler session state.

  Unauthenticated  →  `wrangler deploy --temporary`
                       Zero credentials, 60-min window, claim-to-keep.
                       Requires wrangler ≥ 4.102.0.

  Authenticated    →  `wrangler deploy`
                       Your Cloudflare account, permanent until deleted.
                       Prints the exact `wrangler delete` command to clean up.

One concern regardless of mode: get a live *.workers.dev URL fast.
Stateless — no passport, no project root, no .awf writes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_WRANGLER_TEMPORARY = (4, 102, 0)
COMPAT_DATE = "2026-06-01"

_AWF_HOME = os.environ.get("AWF_HOME")
if _AWF_HOME:
    sys.path.insert(0, str(Path(_AWF_HOME) / "lib"))
try:
    from lib import log  # type: ignore
except Exception:  # noqa: BLE001
    log = None


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "awf-preview"


def _wrangler_cmd() -> list[str] | None:
    if shutil.which("wrangler"):
        return ["wrangler"]
    if shutil.which("npx"):
        return ["npx", "wrangler"]
    return None


def _version(cmd: list[str]) -> tuple[int, int, int] | None:
    try:
        r = subprocess.run(
            [*cmd, "--version"], capture_output=True, text=True, timeout=60
        )
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", (r.stdout or "") + (r.stderr or ""))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _logged_in(cmd: list[str]) -> bool:
    """True only when whoami exits 0 AND shows a recognised account indicator.

    A non-zero exit code means the session is broken or expired — treat as
    logged out so the temporary path is used instead of failing silently.
    """
    try:
        r = subprocess.run(
            [*cmd, "whoami"], capture_output=True, text=True, timeout=30
        )
    except Exception:  # noqa: BLE001
        return False
    # Non-zero exit = auth check failed (expired, revoked, network error).
    if r.returncode != 0:
        return False
    out = (r.stdout or "") + (r.stderr or "")
    low = out.lower()
    if "not authenticated" in low or "not logged in" in low:
        return False
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", out):
        return True
    if "account name" in low or "account id" in low:
        return True
    return False


def _generated_config(assets_dir: Path, name: str) -> dict:
    return {
        "name": name,
        "compatibility_date": COMPAT_DATE,
        "assets": {"directory": str(assets_dir)},
    }


def _find_config(root: Path) -> Path | None:
    for fn in ("wrangler.jsonc", "wrangler.json", "wrangler.toml"):
        if (root / fn).is_file():
            return root / fn
    return None


def _extract_urls(text: str) -> tuple[str | None, str | None]:
    urls = re.findall(r"https?://[^\s)>'\"]+", text)
    live = next((u for u in urls if "workers.dev" in u), None)
    claim = next((u for u in urls if "claim" in u.lower()), None)
    if claim is None:
        claim = next(
            (u for u in urls if "dash.cloudflare.com" in u and u != live), None
        )
    return live, claim


def _name_from_url(url: str) -> str | None:
    """Extract the worker name from a *.workers.dev URL (first subdomain)."""
    m = re.match(r"https?://([^.]+)\.", url or "")
    return m.group(1) if m else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="awf-preview", add_help=True)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--assets", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--keep-config", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1

    cmd = _wrangler_cmd()
    if cmd is None:
        print(
            "error: neither `wrangler` nor `npx` on PATH. Install Wrangler.",
            file=sys.stderr,
        )
        return 1

    # Mode is chosen by auth state, not by flags.
    logged_in = _logged_in(cmd)
    if logged_in:
        mode = "authenticated"
    else:
        # Unauthenticated path needs wrangler >= 4.102.0 for --temporary.
        ver = _version(cmd)
        if ver is None:
            print("error: could not determine wrangler version.", file=sys.stderr)
            return 1
        if ver < MIN_WRANGLER_TEMPORARY:
            got = ".".join(map(str, ver))
            need = ".".join(map(str, MIN_WRANGLER_TEMPORARY))
            print(
                f"error: wrangler {got} < {need} (required for --temporary).\n"
                f"Upgrade: npm i -g wrangler@latest",
                file=sys.stderr,
            )
            return 1
        mode = "temporary"

    # Resolve config: use existing wrangler config or generate one from --assets.
    generated: Path | None = None
    deploy_cwd = root
    extra: list[str] = []
    worker_name: str | None = None

    assets_dir = Path(args.assets).resolve() if args.assets else None
    if assets_dir is None and _find_config(root) is None:
        # No config and no --assets: treat the directory itself as assets.
        assets_dir = root

    if assets_dir is not None:
        if not assets_dir.is_dir():
            print(f"error: --assets is not a directory: {assets_dir}", file=sys.stderr)
            return 1
        worker_name = _slug(args.name or assets_dir.name)
        cfg = _generated_config(assets_dir, worker_name)
        fd, tmp = tempfile.mkstemp(prefix="wrangler.preview.", suffix=".json", dir=root)
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=2)
        generated = Path(tmp)
        extra = ["--config", str(generated)]
        print(f"- generated config: {generated.name} (assets: {assets_dir})")
    else:
        if args.name:
            print("- note: --name ignored when an existing wrangler config is present")

    if mode == "temporary":
        deploy_cmd = [*cmd, "deploy", "--temporary", *extra]
        print("- mode: temporary (unauthenticated, ~60 min, claim to keep)")
    else:
        deploy_cmd = [*cmd, "deploy", *extra]
        print("- mode: authenticated (permanent until deleted)")
    print(f"- cwd: {deploy_cwd}")

    captured: list[str] = []
    try:
        proc = subprocess.Popen(
            deploy_cmd,
            cwd=deploy_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            captured.append(line)
        rc = proc.wait()
    finally:
        if generated is not None and not args.keep_config:
            generated.unlink(missing_ok=True)

    blob = "".join(captured)
    if rc != 0:
        print(f"\nerror: wrangler deploy failed (exit {rc})", file=sys.stderr)
        low = blob.lower()
        if "10000" in blob or "authentication error" in low:
            print(
                "hint: OAuth token is revoked or expired server-side.\n"
                "Run `wrangler login` to re-authenticate, then retry.",
                file=sys.stderr,
            )
        elif mode == "temporary" and "already authenticated" in low:
            print(
                "hint: wrangler has a local session that blocks --temporary.\n"
                "Run `wrangler login` to refresh it (authenticated mode),\n"
                "or `wrangler logout` to clear it (temporary mode).",
                file=sys.stderr,
            )
        elif mode == "temporary" and "rate" in low and "limit" in low:
            print(
                "hint: temporary-account creation is rate-limited. "
                "Wait and retry, or log in and re-run (will use authenticated mode).",
                file=sys.stderr,
            )
        return rc

    live, claim = _extract_urls(blob)

    # Resolve worker name from URL if not already known (existing config case).
    if live and worker_name is None:
        worker_name = _name_from_url(live)

    print()
    if mode == "temporary":
        print("— preview ready (temporary, ~60 min) —")
        print(f"  live:   {live or '(see output above)'}")
        print(f"  claim:  {claim or '(see output above)'}")
    else:
        print("— preview ready (authenticated) —")
        print(f"  live:   {live or '(see output above)'}")
        if worker_name:
            print(f"  name:   {worker_name}")
            print(f"  delete: wrangler delete --name {worker_name}")

    if log:
        log.note(
            f"awf-preview: mode={mode} live={live}",
            by="awf-preview",
        )

    if args.json:
        out: dict = {"mode": mode, "live_url": live, "worker_name": worker_name}
        if mode == "temporary":
            out["claim_url"] = claim
        else:
            out["delete_cmd"] = (
                f"wrangler delete --name {worker_name}" if worker_name else None
            )
        out["raw"] = blob
        print(json.dumps(out, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
