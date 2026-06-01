#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-doctor — validate runtime, CLIs, credentials, OAuth tokens.

Pure read. No network calls except to the auth status of CLIs that are
already authed locally (`wrangler whoami`, `gh auth status`).

Flags
-----
--json              Machine-readable JSON output.
--for-stage NAME    Run only subsystems needed at that stage.
--for-skill NAME    Run only subsystems needed by that skill. Empty → exit 0.

Exit codes
----------
0  All required checks in scope pass (warnings allowed).
1  At least one required check in scope failed.
2  Usage error (unknown flag, unknown stage/skill name, mutual exclusion).
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from awf_home import find_awf_home, AwfHomeNotFound  # noqa: E402
from config import Config  # noqa: E402
from lib.log import tail_events  # noqa: E402
from lib.stages import SUBSYSTEMS, stage_subsystems, skill_subsystems, STAGE_ORDER  # noqa: E402
from lib.state import ProjectAnchor  # noqa: E402
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
    lead_with_header: str = ""   # advisory header text (if any)
    scope_note: str = ""         # note on scoping (e.g. "Checking only mvp-play subsystems")

    def add(self, c: Check) -> None:
        self.checks.append(c)

    def required_failures(self) -> list[Check]:
        return [c for c in self.checks if c.required and c.status == FAIL]


# ── Scope helpers ────────────────────────────────────────────────────────────


@dataclass
class ScopeDefault:
    """Run every entry in SUBSYSTEM_CHECKS."""
    wanted: list[str] = field(default_factory=list)   # empty = all


@dataclass
class ScopeStage:
    name: str
    wanted: list[str]


@dataclass
class ScopeSkill:
    name: str
    wanted: list[str]


Scope = ScopeDefault | ScopeStage | ScopeSkill


# ── Individual checks (building blocks) ─────────────────────────────────────


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


# ── Per-subsystem check functions ────────────────────────────────────────────
# Each function maps to a subsystem id in lib/stages.SUBSYSTEMS.
# The default path calls all of them; scoped paths call only the wanted subset.


def _check_cloudflare(report: Report, cfg: Config, project_root: Path | None) -> None:
    for k in ("CLOUDFLARE_EMAIL", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"):
        check_credential(report, cfg, group="cloudflare", key=k)


def _check_namecheap(report: Report, cfg: Config, project_root: Path | None) -> None:
    for k in ("NAMECHEAP_API_USER", "NAMECHEAP_API_KEY", "NAMECHEAP_USERNAME", "NAMECHEAP_CLIENT_IP"):
        check_credential(report, cfg, group="namecheap", key=k)


def _check_fathom(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_credential(report, cfg, group="fathom", key="FATHOM_API_KEY")


def _check_gsc(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_credential(report, cfg, group="google", key="GOOGLE_APPLICATION_CREDENTIALS")
    check_google_token(report, cfg, project_root)


def _check_bing(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_credential(report, cfg, group="bing", key="BING_WEBMASTER_API_KEY")


def _check_hetzner(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_credential(report, cfg, group="hetzner", key="HETZNER_API_TOKEN")


def _check_neon(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_credential(report, cfg, group="neon", key="NEON_API_KEY")


def _check_ghcr(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_credential(report, cfg, group="ghcr", key="GHCR_TOKEN")


def _check_ssh(report: Report, cfg: Config, project_root: Path | None) -> None:
    # Check for SSH key presence (the default id_rsa or id_ed25519).
    ssh_key_candidates = [
        Path.home() / ".ssh" / "id_ed25519",
        Path.home() / ".ssh" / "id_rsa",
    ]
    found_key = next((p for p in ssh_key_candidates if p.is_file()), None)
    if found_key:
        report.add(Check("ssh", "ssh_key", OK, detail=str(found_key), required=False))
    else:
        report.add(Check("ssh", "ssh_key", WARN,
                         detail="no id_ed25519 or id_rsa found in ~/.ssh/",
                         hint="generate a key: ssh-keygen -t ed25519",
                         required=False))


def _check_kamal_cli(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_cli(report, "kamal", hint="gem install kamal  (or bundle add kamal)", required=True)


def _check_google_oauth(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_credential(report, cfg, group="google", key="GOOGLE_APPLICATION_CREDENTIALS")
    check_google_token(report, cfg, project_root)


def _check_git(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_cli(report, "git", hint="install git")
    check_git_clean(report)


def _check_node(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_node_versions(report)


def _check_wrangler(report: Report, cfg: Config, project_root: Path | None) -> None:
    check_cli(report, "wrangler", hint="npm i -g wrangler")
    check_cli_subcmd(report, "wrangler", ["whoami"], hint="run: wrangler login")


# SUBSYSTEM_CHECKS dispatch table.
# Keys must exactly match the ids in lib/stages.SUBSYSTEMS.
# check.py owns the behaviour layer; lib/stages.py owns the data layer.
SubsystemCheckFn = Callable[[Report, Config, Path | None], None]

SUBSYSTEM_CHECKS: dict[str, SubsystemCheckFn] = {
    "cloudflare":  _check_cloudflare,
    "namecheap":   _check_namecheap,
    "fathom":      _check_fathom,
    "gsc":         _check_gsc,
    "bing":        _check_bing,
    "hetzner":     _check_hetzner,
    "neon":        _check_neon,
    "ghcr":        _check_ghcr,
    "ssh":         _check_ssh,
    "kamal_cli":   _check_kamal_cli,
    "google_oauth": _check_google_oauth,
    "git":         _check_git,
    "node":        _check_node,
    "wrangler":    _check_wrangler,
}

# Default subsystem run order for the full sweep.
_DEFAULT_SUBSYSTEM_ORDER = [
    "git", "node", "wrangler",
    "cloudflare", "namecheap", "fathom", "gsc", "bing",
    "hetzner", "neon", "ghcr", "ssh", "kamal_cli",
]


# ── Recent-error surfacing ────────────────────────────────────────────────────


def _infer_subsystem(skill_name: str) -> str | None:
    """Infer a credentialed subsystem from a skill name.

    Returns the first subsystem for the skill if it is credentialed
    (not git/node/ssh alone), else None.
    """
    from lib.stages import SKILL_REQUIREMENTS
    subs = SKILL_REQUIREMENTS.get(skill_name, [])
    non_infra = [s for s in subs if s not in ("git", "node", "ssh")]
    return non_infra[0] if non_infra else None


def detect_credential_error_subsystem(anchor: ProjectAnchor) -> str | None:
    """Tail last 50 events; return first credentialed subsystem with a
    401/403/auth error, or None.

    Only triggers on events with type == "error" and a note matching
    {401, 403, auth} (case-insensitive) per spec § C4 / T2.
    """
    # anchor._path is the path to .awf/project.json; its parent is .awf/,
    # its grandparent is the project root.
    anchor_path: Path | None = anchor._path
    if anchor_path is None:
        return None
    log_path = anchor_path.parent / "log.jsonl"
    if not log_path.exists():
        return None
    events = tail_events(log_path, n=50)
    for ev in reversed(events):                # most-recent first
        if ev.get("type") != "error":
            continue
        msg = (ev.get("note") or "").lower()
        if not any(token in msg for token in ("401", "403", "auth")):
            continue
        # Try explicit subsystem field first, then infer from skill name.
        subsystem = ev.get("subsystem") or _infer_subsystem(ev.get("skill", ""))
        if subsystem and subsystem in SUBSYSTEMS:
            return subsystem
    return None


# ── Orchestration ─────────────────────────────────────────────────────────────


def _run_default_checks(report: Report, cfg: Config, project_root: Path | None) -> None:
    """Run the original full sweep (unchanged default behaviour)."""
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


def _run_subsystem_checks(
    report: Report,
    cfg: Config,
    project_root: Path | None,
    wanted: list[str],
    lead_with: str | None,
) -> None:
    """Run checks for the given subsystem ids, optionally leading with one."""
    # Build ordered run list: lead_with first (if in scope), then rest.
    if lead_with and lead_with in wanted:
        order = [lead_with] + [s for s in wanted if s != lead_with]
        report.lead_with_header = (
            f"Recent error suggests credential issue — checking {lead_with} first."
        )
    else:
        order = list(wanted)

    seen: set[str] = set()
    for sub in order:
        if sub in seen:
            continue
        seen.add(sub)
        fn = SUBSYSTEM_CHECKS.get(sub)
        if fn is not None:
            fn(report, cfg, project_root)


def build_report(
    scope: Scope | None = None,
    lead_with: str | None = None,
) -> Report:
    """Build a doctor report.

    Parameters
    ----------
    scope:
        None or ScopeDefault → full sweep (byte-for-byte backwards-compat).
        ScopeStage → check only STAGE_REQUIREMENTS subsystems.
        ScopeSkill → check only SKILL_REQUIREMENTS subsystems.
    lead_with:
        Subsystem id to move to the front of the run order (from
        recent-error detection). If outside scope, emits advisory only.
    """
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

    if scope is None or isinstance(scope, ScopeDefault):
        # Default: full sweep, byte-for-byte same as original.
        _run_default_checks(report, cfg, project_root)
        # Advisory only if lead_with outside the default scope
        # (lead_with is ignored in default mode; the full sweep covers it).
    elif isinstance(scope, ScopeStage):
        report.scope_note = f"Checking only {scope.name!r} stage subsystems."
        if lead_with and lead_with not in scope.wanted:
            report.lead_with_header = (
                f"Recent error in {lead_with!r} noted but outside "
                f"--for-stage {scope.name!r} scope. "
                f"Run /awf-doctor (no flag) to investigate."
            )
        _run_subsystem_checks(report, cfg, project_root, scope.wanted, lead_with)
    elif isinstance(scope, ScopeSkill):
        report.scope_note = f"Checking only {scope.name!r} skill subsystems."
        if lead_with and lead_with not in scope.wanted:
            report.lead_with_header = (
                f"Recent error in {lead_with!r} noted but outside "
                f"--for-skill {scope.name!r} scope. "
                f"Run /awf-doctor (no flag) to investigate."
            )
        _run_subsystem_checks(report, cfg, project_root, scope.wanted, lead_with)

    return report


# ── Output ────────────────────────────────────────────────────────────────────

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

    if report.lead_with_header:
        lines.append(f"[note] {report.lead_with_header}")
        lines.append("")

    if report.scope_note:
        lines.append(f"[scope] {report.scope_note}")
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


def render_json(
    report: Report,
    *,
    preflight_required: bool = True,
    preflight_note: str = "",
) -> str:
    data: dict[str, Any] = {
        "awf_home": report.awf_home,
        "project_root": report.project_root,
        "checks": [asdict(c) for c in report.checks],
        "required_failures": [c.name for c in report.required_failures()],
    }
    if not preflight_required:
        data["preflight_required"] = False
    if preflight_note:
        data["preflight_note"] = preflight_note
    if report.lead_with_header:
        data["lead_with_header"] = report.lead_with_header
    if report.scope_note:
        data["scope_note"] = report.scope_note
    return jsonlib.dumps(data, indent=2)


# ── Entry ─────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="check.py",
        description="awf-doctor: validate runtime, CLIs, credentials, OAuth tokens.",
        add_help=False,
    )
    p.add_argument("--json", action="store_true", dest="as_json")
    scope_group = p.add_mutually_exclusive_group()
    scope_group.add_argument("--for-stage", metavar="NAME", default=None)
    scope_group.add_argument("--for-skill", metavar="NAME", default=None)
    p.add_argument("--help", "-h", action="help")

    try:
        return p.parse_args(argv[1:])
    except SystemExit:
        sys.exit(2)


def _validate_scope_args(
    for_stage: str | None,
    for_skill: str | None,
) -> tuple[Scope, str | None]:
    """Resolve and validate scope args.

    Returns (scope, no_preflight_note).
    Exits 2 on unknown names.
    """
    if for_stage is not None:
        if for_stage not in STAGE_ORDER:
            valid = ", ".join(STAGE_ORDER)
            print(f"error: unknown stage {for_stage!r}. Valid stages: {valid}", file=sys.stderr)
            sys.exit(2)
        return ScopeStage(for_stage, stage_subsystems(for_stage)), None

    if for_skill is not None:
        # Check if it's a known skill (any skill in SKILL_REQUIREMENTS, or any
        # skill dir under $AWF_HOME/skills/).
        from lib.stages import SKILL_REQUIREMENTS
        skill_dirs = {p.name for p in (AWF_HOME / "skills").iterdir() if p.is_dir()} \
            if (AWF_HOME / "skills").is_dir() else set()
        known_skills = set(SKILL_REQUIREMENTS.keys()) | skill_dirs
        if for_skill not in known_skills:
            print(
                f"error: unknown skill {for_skill!r}. Use a skill name under "
                f"$AWF_HOME/skills/ or one with explicit SKILL_REQUIREMENTS.",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            wanted = skill_subsystems(for_skill)
        except KeyError:
            # Skill exists as a directory but not in SKILL_REQUIREMENTS.
            wanted = []
        if not wanted:
            return ScopeSkill(for_skill, []), "No preflight checks required for this skill."
        return ScopeSkill(for_skill, wanted), None

    return ScopeDefault(), None


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    scope, no_preflight_note = _validate_scope_args(args.for_stage, args.for_skill)

    # Empty-requirements skill → exit 0 with note.
    if no_preflight_note:
        if args.as_json:
            print(jsonlib.dumps({"preflight_required": False,
                                 "preflight_note": no_preflight_note}, indent=2))
        else:
            print(no_preflight_note)
        return 0

    # Recent-error surfacing (always-on).
    lead_with: str | None = None
    try:
        anchor = ProjectAnchor.load(start=Path.cwd())
        lead_with = detect_credential_error_subsystem(anchor)
    except Exception:
        pass  # no project anchor, or log-reading error — not fatal

    report = build_report(scope=scope, lead_with=lead_with)

    if args.as_json:
        out = render_json(report)
    else:
        out = render_human(report, color=sys.stdout.isatty())
    print(out)
    return 1 if report.required_failures() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
