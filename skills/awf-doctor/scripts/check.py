#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-doctor — validate runtime, CLIs, credentials, OAuth tokens.

Pure read. No network calls except to the auth status of CLIs that are
already authed locally (`wrangler whoami`, `gh auth status`).
"""

from __future__ import annotations

import json as jsonlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME / "lib"))

from awf_home import find_awf_home, AwfHomeNotFound  # noqa: E402
from config import Config  # noqa: E402
from project import find_project_root  # noqa: E402


# ── Result model ────────────────────────────────────────────────────────────

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    category: str
    name: str
    status: str  # OK | WARN | FAIL
    detail: str = ""
    hint: str = ""
    required: bool = True


@dataclass
class Report:
    awf_home: str = ""
    project_root: str | None = None
    checks: list[Check] = field(default_factory=list)

    def add(self, c: Check) -> None:
        self.checks.append(c)

    def required_failures(self) -> list[Check]:
        return [c for c in self.checks if c.required and c.status == FAIL]


# ── Individual checks ───────────────────────────────────────────────────────


def check_cli(report: Report, name: str, *, hint: str, required: bool = True) -> bool:
    path = shutil.which(name)
    if path:
        report.add(Check("cli", name, OK, detail=path, required=required))
        return True
    report.add(Check("cli", name, FAIL, detail="not on PATH", hint=hint, required=required))
    return False


def check_cli_subcmd(
    report: Report,
    name: str,
    args: list[str],
    *,
    hint: str,
    required: bool = True,
) -> None:
    if not shutil.which(name):
        report.add(Check("auth", f"{name} {args[0] if args else ''}", FAIL,
                         detail=f"{name} not installed", hint=hint, required=required))
        return
    try:
        out = subprocess.run(
            [name, *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if out.returncode == 0:
            first = next((ln for ln in out.stdout.splitlines() if ln.strip()), "")
            report.add(Check("auth", f"{name} {' '.join(args)}", OK,
                             detail=first[:120], required=required))
        else:
            stderr_first = next((ln for ln in out.stderr.splitlines() if ln.strip()), "")
            report.add(Check("auth", f"{name} {' '.join(args)}", FAIL,
                             detail=stderr_first[:120], hint=hint, required=required))
    except Exception as e:
        report.add(Check("auth", f"{name} {' '.join(args)}", FAIL,
                         detail=str(e)[:120], hint=hint, required=required))


def check_credential(
    report: Report,
    cfg: Config,
    *,
    group: str,
    key: str,
    required: bool = True,
) -> None:
    val = cfg.get(key)
    src = cfg.source(key)
    if val:
        report.add(Check(f"creds:{group}", key, OK, detail=f"set ({src})", required=required))
    else:
        report.add(Check(f"creds:{group}", key, FAIL,
                         detail="not set",
                         hint=f"set {key} in one of: env, ./.env, $AWF_HOME/.env, ~/.config/awf/.env",
                         required=required))


def check_google_token(report: Report, cfg: Config, project_root: Path | None) -> None:
    creds_path = cfg.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        return  # already reported by check_credential
    p = Path(creds_path).expanduser()
    if not p.is_file():
        report.add(Check("auth", "google_oauth_client_json", FAIL,
                         detail=f"{p} does not exist",
                         hint="download desktop-client OAuth JSON from GCP and point GOOGLE_APPLICATION_CREDENTIALS at it"))
        return
    report.add(Check("auth", "google_oauth_client_json", OK, detail=str(p)))

    # token.json — look in project root (if any), then $AWF_HOME, then home
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root / "token.json")
    candidates.append(AWF_HOME / "token.json")
    candidates.append(Path("~/.config/awf/token.json").expanduser())
    found = next((c for c in candidates if c.is_file()), None)
    if found is None:
        report.add(Check("auth", "google_token_json", WARN,
                         detail="no cached token; awf-setup-gsc will trigger OAuth on first run",
                         required=False))
    else:
        report.add(Check("auth", "google_token_json", OK, detail=str(found),
                         required=False))


def check_node_versions(report: Report) -> None:
    for name in ("node", "npm", "npx"):
        check_cli(report, name, hint="install Node.js (https://nodejs.org/)")


def check_git_clean(report: Report) -> None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode != 0:
            report.add(Check("git", "in_repo", WARN,
                             detail="cwd is not inside a git repo",
                             required=False))
            return
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if st.stdout.strip():
            n = len(st.stdout.strip().splitlines())
            report.add(Check("git", "clean", WARN,
                             detail=f"{n} uncommitted changes",
                             hint="commit or stash before deploying",
                             required=False))
        else:
            report.add(Check("git", "clean", OK, detail="working tree clean", required=False))
    except FileNotFoundError:
        # git CLI absence already reported by check_cli
        return


# ── Orchestration ───────────────────────────────────────────────────────────


def build_report() -> Report:
    report = Report()

    try:
        report.awf_home = str(find_awf_home())
    except AwfHomeNotFound as e:
        report.awf_home = ""
        report.add(Check("runtime", "AWF_HOME", FAIL, detail=str(e),
                         hint="export AWF_HOME=/path/to/awf_skills"))

    project_root = find_project_root(optional=True)
    report.project_root = str(project_root) if project_root else None

    cfg = Config.layered(
        project_root=project_root,
        awf_home=Path(report.awf_home) if report.awf_home else None,
    )

    # Required CLIs
    check_cli(report, "git",      hint="install git")
    check_cli(report, "uv",       hint="curl -LsSf https://astral.sh/uv/install.sh | sh")
    check_node_versions(report)
    check_cli(report, "wrangler", hint="npm i -g wrangler")
    check_cli(report, "gh",       hint="brew install gh   (or https://cli.github.com)", required=False)

    # CLI auth status
    check_cli_subcmd(report, "wrangler", ["whoami"],
                     hint="run: wrangler login")
    check_cli_subcmd(report, "gh", ["auth", "status"],
                     hint="run: gh auth login", required=False)

    # Credentials
    for k in ("CLOUDFLARE_EMAIL", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"):
        check_credential(report, cfg, group="cloudflare", key=k)
    for k in ("NAMECHEAP_API_USER", "NAMECHEAP_API_KEY", "NAMECHEAP_USERNAME", "NAMECHEAP_CLIENT_IP"):
        check_credential(report, cfg, group="namecheap", key=k)
    check_credential(report, cfg, group="fathom", key="FATHOM_API_KEY")
    check_credential(report, cfg, group="google", key="GOOGLE_APPLICATION_CREDENTIALS")

    # Google OAuth artefacts
    check_google_token(report, cfg, project_root)

    # Git hygiene (only when in a repo)
    check_git_clean(report)

    return report


# ── Output ──────────────────────────────────────────────────────────────────

GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def _icon(status: str, *, color: bool) -> str:
    if not color:
        return {OK: "[OK]  ", WARN: "[WARN]", FAIL: "[FAIL]"}[status]
    return {
        OK:   f"{GREEN}[OK]  {RESET}",
        WARN: f"{YELLOW}[WARN]{RESET}",
        FAIL: f"{RED}[FAIL]{RESET}",
    }[status]


def render_human(report: Report, *, color: bool = True) -> str:
    lines: list[str] = []
    lines.append("awf-doctor — runtime check")
    lines.append("─" * 60)
    lines.append(f"AWF_HOME     : {report.awf_home or '(unresolved)'}")
    lines.append(f"project_root : {report.project_root or '(none — not inside a project)'}")
    lines.append("")

    by_cat: dict[str, list[Check]] = {}
    for c in report.checks:
        by_cat.setdefault(c.category, []).append(c)

    for cat in sorted(by_cat):
        lines.append(cat)
        for c in by_cat[cat]:
            req = "" if c.required else " (optional)"
            line = f"  {_icon(c.status, color=color)}  {c.name}{req}"
            if c.detail:
                line += f"  — {c.detail}"
            lines.append(line)
            if c.status == FAIL and c.hint:
                lines.append(f"          ↳ {c.hint}")
        lines.append("")

    failures = report.required_failures()
    if failures:
        lines.append(f"{RED if color else ''}{len(failures)} required check(s) failed.{RESET if color else ''}")
    else:
        warns = sum(1 for c in report.checks if c.status == WARN)
        lines.append(f"{GREEN if color else ''}All required checks passed.{RESET if color else ''}"
                     + (f" ({warns} warning(s))" if warns else ""))
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return jsonlib.dumps(
        {
            "awf_home": report.awf_home,
            "project_root": report.project_root,
            "checks": [asdict(c) for c in report.checks],
            "required_failures": [c.name for c in report.required_failures()],
        },
        indent=2,
    )


# ── Entry ───────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    as_json = "--json" in argv[1:]
    if any(a not in ("--json",) for a in argv[1:]):
        print("usage: check.py [--json]", file=sys.stderr)
        return 2

    report = build_report()
    out = render_json(report) if as_json else render_human(report, color=sys.stdout.isatty())
    print(out)
    return 1 if report.required_failures() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
