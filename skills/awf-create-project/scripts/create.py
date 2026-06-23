#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-create-project — scaffold a new website project.

Steps:
  1. Validate `domain`; derive slug.
  2. Pick the target directory: `<cwd>/<slug>` by default, or `--in <dir>`.
  3. If a `passport.json` already exists there, refuse (unless `--force`).
  4. Resolve the template (default: latest `landing-page-v*`). If no
     templates exist, create a minimal project (passport + empty dirs)
     and warn — useful for testing the rest of the pipeline before a
     template lands.
  5. Apply the template (preserve-list-aware overlay).
  6. Write `passport.json` via `Passport.new(domain)`, pinning
     `template_version`.
  7. Unless `--no-git`, `git init` + initial commit.
  8. Print the next-step hint.

Idempotent: re-running on an existing project errors unless `--force`,
which re-applies the template (preserving the listed paths).
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
# Repo root first (so `from lib import log` shares passport.py's lib.log
# module + ContextVars), then lib/ for sibling imports.
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log  # noqa: E402
from awf_home import find_awf_home, AwfHomeNotFound  # noqa: E402
from passport import Passport, PASSPORT_FILENAME  # noqa: E402
from slug import normalize_domain, domain_to_project_name  # noqa: E402
from templates import (  # noqa: E402
    resolve_template,
    apply_overlay,
    TemplateNotFound,
    Template,
)


GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def _color(s: str, c: str) -> str:
    return f"{c}{s}{RESET}" if sys.stdout.isatty() else s


# ── Args ───────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> dict:
    args = argv[1:]
    if not args or args[0].startswith("-"):
        print(
            "usage: create.py <domain> [--template <name>] [--in <dir>] "
            "[--force] [--no-git]",
            file=sys.stderr,
        )
        sys.exit(2)
    domain = args.pop(0)
    out: dict = {
        "domain": domain,
        "template": None,
        "in_dir": None,
        "force": False,
        "no_git": False,
    }
    while args:
        a = args.pop(0)
        if a == "--template":
            out["template"] = args.pop(0) if args else None
        elif a == "--in":
            out["in_dir"] = args.pop(0) if args else None
        elif a == "--force":
            out["force"] = True
        elif a == "--no-git":
            out["no_git"] = True
        else:
            print(f"unknown argument: {a}", file=sys.stderr)
            sys.exit(2)
    return out


# ── Steps ──────────────────────────────────────────────────────────────


def resolve_target_dir(slug: str, in_dir: str | None) -> Path:
    if in_dir:
        return Path(in_dir).expanduser().resolve()
    return (Path.cwd() / slug).resolve()


def init_git(target_dir: Path) -> None:
    if (target_dir / ".git").exists():
        print(f"- git repo already present at {target_dir}")
        return
    if shutil.which("git") is None:
        print("- WARN: git not on PATH; skipping git init")
        return
    subprocess.run(["git", "init", "-q"], cwd=target_dir, check=True)
    # The template ships its own .gitignore typically; if not, add a minimal one.
    gi = target_dir / ".gitignore"
    if not gi.is_file():
        gi.write_text(
            "node_modules/\n.svelte-kit/\n.wrangler/\ndist/\n.DS_Store\n*creds.json\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "add", "."], cwd=target_dir, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "scaffold: awf-create-project"],
        cwd=target_dir, check=True,
    )
    print(_color(f"- git: initial commit", GREEN))


def write_minimal_project(target_dir: Path, passport: Passport) -> None:
    """No template available — write only `passport.json`. The user must
    add code manually (this is a development-time fallback)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    passport.save(target_dir)
    print(_color(
        f"- WARN: no template at {AWF_HOME}/templates/. "
        f"Wrote {PASSPORT_FILENAME} only; add project files manually.",
        YELLOW,
    ))


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    try:
        domain = normalize_domain(opts["domain"])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    slug = domain_to_project_name(domain)
    target_dir = resolve_target_dir(slug, opts["in_dir"])

    try:
        awf_home = find_awf_home()
    except AwfHomeNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Refuse if there's already a passport here unless --force.
    if (target_dir / PASSPORT_FILENAME).is_file() and not opts["force"]:
        print(
            f"error: {target_dir / PASSPORT_FILENAME} already exists. "
            "Re-run with --force to re-apply the template (preserves "
            "content per the template's preserve-list).",
            file=sys.stderr,
        )
        return 1

    # Resolve template (graceful fallback if none exists).
    template: Template | None
    try:
        template = resolve_template(awf_home, name_or_dir=opts["template"])
        print(f"- template: {template.name} v{template.version} ({template.path})")
    except TemplateNotFound as e:
        if opts["template"] is not None:
            # User asked for a specific template by name — hard error.
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(_color(f"- {e}", YELLOW))
        template = None

    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"- target: {target_dir}")

    # Scaffolding is the project's birth. We open the session with
    # start=target_dir so the session summary lands in the central index and
    # subsequent saves route to <target_dir>/.awf/log.jsonl. Note (D-011): on a
    # truly-fresh project there is no project marker at session entry, so the
    # very first passport write may route to the orphan log — an accepted
    # birth-event limitation; git also records the scaffold, and re-runs
    # (--force) log to the project.
    with log.session(composer="awf-create-project", target="landing", start=target_dir):
        with log.invoke(
            skill="awf-create-project",
            args={"domain": domain, "slug": slug, "force": opts["force"]},
        ):
            # Build passport before any template overlay so existing-passport
            # behaviour (with --force) preserves it via the overlay's
            # preserve-list (which should include passport.json).
            if (target_dir / PASSPORT_FILENAME).is_file():
                passport = Passport.load(target_dir)
                # Refresh derived fields in case the user manually wrote a stub.
                passport.domain = domain
                passport.project_name = slug
                passport.site_url = f"https://{domain}"
            else:
                passport = Passport.new(
                    domain=domain,
                    template_version=template.version if template else "0.0",
                )

            if template is None:
                write_minimal_project(target_dir, passport)
            else:
                # Apply template, then write/refresh passport (template may have
                # shipped a passport.json template, but ours wins).
                changes = apply_overlay(template, target_dir, dry_run=False)
                n_create = sum(1 for c in changes if c.kind == "create")
                n_modify = sum(1 for c in changes if c.kind == "modify")
                n_preserve = sum(1 for c in changes if c.kind == "preserved")
                n_unchanged = sum(1 for c in changes if c.kind == "unchanged")
                print(
                    f"- overlay: {n_create} created, {n_modify} modified, "
                    f"{n_preserve} preserved, {n_unchanged} unchanged"
                )
                passport.template_version = template.version
                passport.save(target_dir)
                print(f"- wrote {PASSPORT_FILENAME}")

            if not opts["no_git"]:
                try:
                    init_git(target_dir)
                except subprocess.CalledProcessError as e:
                    print(f"- WARN: git init/commit failed: {e}")

    print()
    print(_color(f"Project scaffolded at: {target_dir}", GREEN))
    print()
    print("Next:")
    if target_dir != Path.cwd():
        print(f"  cd {target_dir}")
    print(f"  awf-doctor                 # confirm credentials are set")
    print(f"  awf-setup-domain           # establish Cloudflare zone + Pages")
    print(f"  awf-setup-nameservers      # point Namecheap at Cloudflare")
    print(f"  awf-setup-analytics        # provision Fathom site")
    print(f"  awf-generate-content ...   # write site copy + FAQs")
    print(f"  awf-review-passport --mark-reviewed")
    print(f"  awf-install && awf-deploy")
    print(f"  awf-setup-gsc && awf-verify-gsc")
    print(f"  awf-submit-bing --confirm-imported")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
