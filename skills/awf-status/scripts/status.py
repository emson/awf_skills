#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2", "httpx>=0.27", "hcloud>=2", "cloudflare>=3"]
# ///

"""awf-status — canonical "where am I" surface (plan_012 / D-007).

Emits a fixed-order status block:
  Project / Stage / Drift / Recent / Next

Exit codes:
    0  — status produced (drift present or none; also no-project case)
    4  — argument validation failure (unknown flag)

Corrupt .awf/project.json propagates as an unhandled exception (traceback)
because local-state corruption is not provider-side uncertainty.
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-status/scripts/status.py
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib.log import tail_events  # noqa: E402
from lib.project import ProjectNotFound  # noqa: E402
from lib.state import ProjectAnchor, Infra  # noqa: E402

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

IDLE_THRESHOLD_DAYS = 90

# Stage → next composer mapping.
# Local for now; plan_013 (awf-help) will promote to lib/stages.py.
NEXT_COMPOSERS: dict[str, str | None] = {
    "landing": "awf-stage-demo",
    "demo": "awf-stage-mvp-play",
    "mvp-play": "awf-stage-prescale",
    "prescale": "awf-stage-scale",
    "scale": None,
}

NEXT_HINTS: dict[str, str] = {
    "landing": "promote to demo stage when ready",
    "demo": "promote to mvp-play stage when ready",
    "mvp-play": "promote to prescale stage when ready",
    "prescale": "promote to scale stage when ready",
    "scale": "at terminal stage; operate via atomic skills",
}

# v1 deferred checks — updated when a later plan implements each one.
# TODO(plan_013): remove dns_records when DNS drift check ships.
# TODO(plan_013): remove cloudflare_pages when Pages drift check ships.
# TODO(plan_013): remove fathom when Fathom drift check ships.
# TODO(plan_013): remove gsc when GSC drift check ships.
# TODO(plan_014): remove hetzner_lb when S4 LB drift check ships.
NOT_YET_CHECKED: list[str] = [
    "dns_records",
    "cloudflare_pages",
    "fathom",
    "gsc",
    "hetzner_lb",
]

# Re-converge skill suggestions keyed by (provider, resource_type).
# T3 advisory (Pass 1): explicit SUGGEST dict makes coupling visible.
SUGGEST: dict[tuple[str, str], str] = {
    ("cloudflare", "zone"): "awf-setup-domain",
    ("hetzner", "server"): "awf-hetzner-provision",
    ("neon", "project"): "awf-neon-provision",
    ("neon", "branch"): "awf-neon-provision",
}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DriftEntry:
    """A single drift divergence between local state and provider world."""

    provider: str       # "cloudflare" | "hetzner" | "neon"
    resource: str       # "zone" | "server:<id>" | "project" | "branch"
    state_value: str    # what local state says
    world_value: str    # what provider says ("missing" | "unknown" | actual)
    suggested_skill: str  # atomic skill to re-converge (empty if unknown)
    note: str = ""      # one-line human explanation
    _error_msg: str = field(default="", repr=False)  # captured for --verbose


@dataclass
class IdleInfo:
    """Idle warning data."""
    days_since_last_session: int


@dataclass
class StatusReport:
    """Intermediate report consumed by both render functions."""

    # Project identity
    slug: str
    domain: str
    root: Path
    stage: str

    # Core data
    drift: list[DriftEntry] = field(default_factory=list)
    recent: list[dict[str, Any]] = field(default_factory=list)
    next_composer: str | None = None
    next_hint: str = ""
    idle: IdleInfo | None = None
    not_yet_checked: list[str] = field(default_factory=list)

    # Verbose extras
    verbose: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Drift checks
# ---------------------------------------------------------------------------


def _check_cloudflare(
    anchor: ProjectAnchor,
    infra: Infra | None,
    *,
    verbose: bool,
) -> list[DriftEntry]:
    """Check Cloudflare zone drift against live provider."""
    entries: list[DriftEntry] = []

    # Read zone_id from passport (legacy field); domain from anchor.
    # passport.cloudflare is a dict — look for "zone_id" key.
    zone_id_state = ""
    try:
        from lib.project import find_project_root
        root = find_project_root(Path(str(anchor._path)).parent.parent if anchor._path else None)
        passport_path = root / "passport.json"
        if passport_path.exists():
            import json as _j
            passport_data = _j.loads(passport_path.read_text(encoding="utf-8"))
            cf_block = passport_data.get("cloudflare", {})
            zone_id_state = cf_block.get("zone_id", "") if isinstance(cf_block, dict) else ""
    except Exception:
        zone_id_state = ""

    # Build CF client; missing credentials → unknown entry.
    try:
        from lib.cf.client import CloudflareClient, CloudflareConfig
        from lib.config import Config
        cfg = Config.layered()
        missing_keys = [k for k in ("CLOUDFLARE_EMAIL", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID") if not cfg.get(k)]
        if missing_keys:
            entries.append(DriftEntry(
                provider="cloudflare",
                resource="zone",
                state_value=zone_id_state or anchor.domain,
                world_value="unknown",
                suggested_skill="",
                note=f"Credentials not configured: {', '.join(missing_keys)}",
            ))
            return entries
        client = CloudflareClient(CloudflareConfig(
            api_email=cfg.require("CLOUDFLARE_EMAIL"),
            api_key=cfg.require("CLOUDFLARE_API_KEY"),
            account_id=cfg.require("CLOUDFLARE_ACCOUNT_ID"),
        ))
    except Exception as exc:
        entries.append(DriftEntry(
            provider="cloudflare",
            resource="zone",
            state_value=zone_id_state or anchor.domain,
            world_value="unknown",
            suggested_skill="",
            note="Cloudflare client init failed.",
            _error_msg=str(exc),
        ))
        return entries

    # Look up zone by domain name (stable handle per plan spec).
    try:
        from lib.cf.zones import get_zone
        zone = get_zone(client, anchor.domain)
    except Exception as exc:
        entries.append(DriftEntry(
            provider="cloudflare",
            resource="zone",
            state_value=zone_id_state or anchor.domain,
            world_value="unknown",
            suggested_skill="",
            note="Cloudflare API call failed.",
            _error_msg=str(exc),
        ))
        return entries

    if zone is None:
        entries.append(DriftEntry(
            provider="cloudflare",
            resource="zone",
            state_value=zone_id_state or anchor.domain,
            world_value="missing",
            suggested_skill=SUGGEST[("cloudflare", "zone")],
            note=f"Zone for {anchor.domain} not found in Cloudflare.",
        ))
    elif zone_id_state and str(zone.id) != zone_id_state:
        entries.append(DriftEntry(
            provider="cloudflare",
            resource="zone",
            state_value=zone_id_state,
            world_value=f"zone_id={zone.id}",
            suggested_skill=SUGGEST[("cloudflare", "zone")],
            note="Zone ID in passport does not match live Cloudflare zone.",
        ))
    # else: zone matches or no local id stored → no drift

    return entries


def _check_hetzner(
    anchor: ProjectAnchor,
    infra: Infra,
    *,
    verbose: bool,
) -> list[DriftEntry]:
    """Check Hetzner server drift for each server in infra.hetzner.servers."""
    entries: list[DriftEntry] = []

    if not infra.hetzner.servers:
        return entries

    # Build Hetzner client; missing credentials → unknown entries.
    try:
        from lib.config import Config
        cfg = Config.layered()
        token = cfg.get("HETZNER_API_TOKEN")
        if not token:
            for srv in infra.hetzner.servers:
                entries.append(DriftEntry(
                    provider="hetzner",
                    resource=f"server:{srv.id}",
                    state_value=srv.id,
                    world_value="unknown",
                    suggested_skill="",
                    note="HETZNER_API_TOKEN not configured.",
                ))
            return entries

        from lib.hetzner import HetznerClient, HetznerConfig
        hz = HetznerClient(HetznerConfig(api_token=token))
    except Exception as exc:
        for srv in infra.hetzner.servers:
            entries.append(DriftEntry(
                provider="hetzner",
                resource=f"server:{srv.id}",
                state_value=srv.id,
                world_value="unknown",
                suggested_skill="",
                note="Hetzner client init failed.",
                _error_msg=str(exc),
            ))
        return entries

    # Check each server by ID using a read-only GET.
    for srv in infra.hetzner.servers:
        if not srv.id:
            continue
        try:
            server = _hetzner_get_by_id(hz, srv.id)
        except Exception as exc:
            entries.append(DriftEntry(
                provider="hetzner",
                resource=f"server:{srv.id}",
                state_value=srv.id,
                world_value="unknown",
                suggested_skill="",
                note="Hetzner API call failed.",
                _error_msg=str(exc),
            ))
            continue

        if server is None:
            entries.append(DriftEntry(
                provider="hetzner",
                resource=f"server:{srv.id}",
                state_value=srv.id,
                world_value="missing",
                suggested_skill=SUGGEST[("hetzner", "server")],
                note=f"Server {srv.id} not found in Hetzner.",
            ))
        else:
            # Server SDK object has .status attribute
            status = getattr(server, "status", "unknown")
            if str(status) != "running":
                entries.append(DriftEntry(
                    provider="hetzner",
                    resource=f"server:{srv.id}",
                    state_value=srv.id,
                    world_value=f"status={status}",
                    suggested_skill=SUGGEST[("hetzner", "server")],
                    note=f"Server {srv.id} is {status}, expected running.",
                ))

    return entries


def _hetzner_get_by_id(hz: Any, server_id: str) -> Any:
    """Return a Hetzner server by numeric ID, or None if not found (404).

    Uses the hcloud Client directly for a read-only ID lookup.
    Added for plan_012 drift check (get_by_name is not usable when
    we only have the ID from infra.json).

    Raises any non-404 exception so the caller can record it as
    world_value='unknown'.  Only genuine 404 (server doesn't exist)
    returns None.
    """
    from lib.hetzner.errors import HetznerNotFound
    try:
        return hz._client.servers.get_by_id(int(server_id))
    except HetznerNotFound:
        return None
    # All other exceptions propagate to the caller for unknown handling


def _check_neon(
    anchor: ProjectAnchor,
    infra: Infra,
    *,
    verbose: bool,
) -> list[DriftEntry]:
    """Check Neon project + branch drift."""
    entries: list[DriftEntry] = []

    project_id = infra.neon.project_id
    branch_id = infra.neon.branch_id

    if not project_id:
        # Nothing in infra yet; no drift to report.
        return entries

    # Build Neon client; missing credentials → unknown entries.
    try:
        from lib.config import Config
        cfg = Config.layered()
        token = cfg.get("NEON_API_KEY")
        if not token:
            entries.append(DriftEntry(
                provider="neon",
                resource="project",
                state_value=project_id,
                world_value="unknown",
                suggested_skill="",
                note="NEON_API_KEY not configured.",
            ))
            if branch_id:
                entries.append(DriftEntry(
                    provider="neon",
                    resource="branch",
                    state_value=branch_id,
                    world_value="unknown",
                    suggested_skill="",
                    note="NEON_API_KEY not configured.",
                ))
            return entries

        from lib.neon.client import NeonClient, NeonConfig
        nc = NeonClient(NeonConfig(api_key=token))
    except Exception as exc:
        entries.append(DriftEntry(
            provider="neon",
            resource="project",
            state_value=project_id,
            world_value="unknown",
            suggested_skill="",
            note="Neon client init failed.",
            _error_msg=str(exc),
        ))
        return entries

    # Check project
    try:
        project = nc.projects.get(project_id)
    except Exception as exc:
        entries.append(DriftEntry(
            provider="neon",
            resource="project",
            state_value=project_id,
            world_value="unknown",
            suggested_skill="",
            note="Neon API call failed.",
            _error_msg=str(exc),
        ))
        return entries

    if project is None:
        entries.append(DriftEntry(
            provider="neon",
            resource="project",
            state_value=project_id,
            world_value="missing",
            suggested_skill=SUGGEST[("neon", "project")],
            note=f"Neon project {project_id} not found.",
        ))
        # Missing project subsumes missing branch — skip branch check.
        return entries

    # Check branch (only if we have a branch_id and the project exists)
    if branch_id:
        try:
            branch = nc.branches.get(project_id, branch_id)
        except Exception as exc:
            entries.append(DriftEntry(
                provider="neon",
                resource="branch",
                state_value=branch_id,
                world_value="unknown",
                suggested_skill="",
                note="Neon branches API call failed.",
                _error_msg=str(exc),
            ))
            return entries

        if branch is None:
            entries.append(DriftEntry(
                provider="neon",
                resource="branch",
                state_value=branch_id,
                world_value="missing",
                suggested_skill=SUGGEST[("neon", "branch")],
                note=f"Neon branch {branch_id} not found on project {project_id}.",
            ))

    return entries


def run_drift_checks(
    anchor: ProjectAnchor,
    infra: Infra | None,
    *,
    verbose: bool,
) -> list[DriftEntry]:
    """Run all v1 drift checks; return list of DriftEntry objects.

    Each check is wrapped so no exception escapes — any failure is
    captured as world_value="unknown".
    """
    entries: list[DriftEntry] = []

    # CF zone check — runs even without infra (zone exists from S1)
    try:
        entries.extend(_check_cloudflare(anchor, infra, verbose=verbose))
    except Exception as exc:
        entries.append(DriftEntry(
            provider="cloudflare",
            resource="zone",
            state_value=anchor.domain,
            world_value="unknown",
            suggested_skill="",
            note="Unexpected error in CF drift check.",
            _error_msg=str(exc),
        ))

    # Hetzner + Neon — only when infra exists (S3+)
    if infra is not None:
        try:
            entries.extend(_check_hetzner(anchor, infra, verbose=verbose))
        except Exception as exc:
            entries.append(DriftEntry(
                provider="hetzner",
                resource="servers",
                state_value="",
                world_value="unknown",
                suggested_skill="",
                note="Unexpected error in Hetzner drift check.",
                _error_msg=str(exc),
            ))

        try:
            entries.extend(_check_neon(anchor, infra, verbose=verbose))
        except Exception as exc:
            entries.append(DriftEntry(
                provider="neon",
                resource="project",
                state_value="",
                world_value="unknown",
                suggested_skill="",
                note="Unexpected error in Neon drift check.",
                _error_msg=str(exc),
            ))

    return entries


# ---------------------------------------------------------------------------
# Idle detection
# ---------------------------------------------------------------------------


def check_idle(log_path: Path) -> IdleInfo | None:
    """Return IdleInfo if most-recent session.end is >90 days ago, else None.

    Reads backward through the log for efficiency. If no session.end is
    found (fresh project or all sessions crashed), returns None — treat
    as "no idle data", not as "idle since epoch".
    """
    if not log_path.exists():
        return None

    # Read a reasonable tail to find the most recent session.end
    for batch_n in (200, 1000, 5000):
        events = tail_events(log_path, batch_n)
        for ev in reversed(events):
            if ev.get("type") == "session.end":
                ts_str = ev.get("ts", "")
                if not ts_str:
                    continue
                try:
                    # Parse ISO timestamp
                    ts_str_clean = ts_str.replace("Z", "+00:00")
                    ended_at = datetime.fromisoformat(ts_str_clean)
                    now = datetime.now(timezone.utc)
                    delta_days = (now - ended_at).days
                    if delta_days > IDLE_THRESHOLD_DAYS:
                        return IdleInfo(days_since_last_session=delta_days)
                    return None  # Recent session — not idle
                except (ValueError, TypeError):
                    continue
        if len(events) < batch_n:
            break

    return None


# ---------------------------------------------------------------------------
# Next composer
# ---------------------------------------------------------------------------


def next_composer_for_stage(stage: str) -> tuple[str | None, str]:
    """Return (composer_name, hint) for the stage after the current one."""
    composer = NEXT_COMPOSERS.get(stage)
    hint = NEXT_HINTS.get(stage, "")
    return composer, hint


# ---------------------------------------------------------------------------
# Verbose details builder
# ---------------------------------------------------------------------------


def _build_verbose_details(
    anchor: ProjectAnchor,
    infra: Infra | None,
    drift: list[DriftEntry],
) -> dict[str, Any]:
    """Build per-provider state detail for --verbose output."""
    details: dict[str, Any] = {}

    # Passport cloudflare block
    try:
        awf_path = anchor._path
        if awf_path:
            root = awf_path.parent.parent
            passport_path = root / "passport.json"
            if passport_path.exists():
                import json as _j
                pd = _j.loads(passport_path.read_text(encoding="utf-8"))
                details["cloudflare"] = {
                    "zone_id": pd.get("cloudflare", {}).get("zone_id", "") if isinstance(pd.get("cloudflare"), dict) else "",
                    "fathom_site_id": pd.get("fathom_site_id", ""),
                }
    except Exception:
        pass

    if infra is not None:
        details["hetzner"] = {
            "servers": [
                {"id": s.id, "ip": s.ip, "role": s.role}
                for s in infra.hetzner.servers
            ],
            "lb_id": infra.hetzner.lb_id,
        }
        details["neon"] = {
            "project_id": infra.neon.project_id,
            "branch_id": infra.neon.branch_id,
            "branch_name": infra.neon.branch_name,
        }
        details["kamal"] = {
            "last_deploy_image": infra.kamal.last_deploy_image,
        }

    # Captured error messages for unknown entries
    error_msgs = {
        f"{e.provider}/{e.resource}": e._error_msg
        for e in drift
        if e._error_msg
    }
    if error_msgs:
        details["provider_errors"] = error_msgs

    return details


# ---------------------------------------------------------------------------
# Short-form event line
# ---------------------------------------------------------------------------


def _event_short(ev: dict[str, Any]) -> str:
    """Return a compact single-line representation of one log event."""
    ts = ev.get("ts", "")[:19].replace("T", " ")
    etype = ev.get("type", "?")
    skill = ev.get("skill", "")
    result = ev.get("result", "")
    data = ev.get("data", {}) or {}

    parts = [ts, etype]
    if skill:
        parts.append(f"skill={skill}")
    if result:
        parts.append(f"result={result}")

    if etype == "note":
        text = data.get("text", "")
        if text:
            parts.append(f"text={text[:80]!r}")
    elif etype == "error":
        msg = data.get("message", "")
        if msg:
            parts.append(f"msg={msg[:60]!r}")
    elif etype in ("session.start", "session.end"):
        composer = data.get("composer", "")
        target = data.get("target_stage", "")
        if composer:
            parts.append(f"composer={composer} target={target}")

    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------


def render_human(report: StatusReport) -> str:
    """Render a StatusReport to a human-readable fixed-order string."""
    lines: list[str] = []

    lines.append(f"Project: {report.slug} ({report.domain})")
    lines.append(f"Stage:   {report.stage}")

    # Drift block
    if not report.drift:
        lines.append("Drift:   none")
    else:
        first = True
        for entry in report.drift:
            if first:
                lines.append(
                    f"Drift:   [{entry.provider}/{entry.resource}] "
                    f"state={entry.state_value!r} world={entry.world_value!r}"
                    f"{f'  → {entry.suggested_skill}' if entry.suggested_skill else ''}"
                    f"{f'  ({entry.note})' if entry.note else ''}"
                )
                first = False
            else:
                lines.append(
                    f"         [{entry.provider}/{entry.resource}] "
                    f"state={entry.state_value!r} world={entry.world_value!r}"
                    f"{f'  → {entry.suggested_skill}' if entry.suggested_skill else ''}"
                    f"{f'  ({entry.note})' if entry.note else ''}"
                )

    # Recent block
    if not report.recent:
        lines.append("Recent:  (no events)")
    else:
        first = True
        for ev in report.recent:
            if first:
                lines.append(f"Recent:  {_event_short(ev)}")
                first = False
            else:
                lines.append(f"         {_event_short(ev)}")

    # Next block
    if report.next_composer:
        lines.append(f"Next:    {report.next_composer} — {report.next_hint}")
    else:
        lines.append(f"Next:    {report.next_hint}")

    # Idle warning (soft, informational)
    if report.idle is not None:
        lines.append(
            f"Idle:    last session ended {report.idle.days_since_last_session} days ago"
            " — project may be stale."
        )

    lines.append("")
    lines.append(
        "Not yet checked: " + ", ".join(report.not_yet_checked)
    )

    if report.verbose:
        lines.append("")
        lines.append("--- details ---")
        lines.append(jsonlib.dumps(report.details, indent=2))

    return "\n".join(lines)


def render_json(report: StatusReport) -> str:
    """Render a StatusReport to a JSON string conforming to STATUS_JSON_SCHEMA."""
    obj: dict[str, Any] = {
        "schema_version": 1,
        "project": {
            "slug": report.slug,
            "domain": report.domain,
            "root": str(report.root),
        },
        "stage": report.stage,
        "drift": [
            {
                "provider": e.provider,
                "resource": e.resource,
                "state_value": e.state_value,
                "world_value": e.world_value,
                "suggested_skill": e.suggested_skill,
                "note": e.note,
            }
            for e in report.drift
        ],
        "recent": report.recent,
        "next_composer": report.next_composer,
        "idle": (
            {"days_since_last_session": report.idle.days_since_last_session}
            if report.idle is not None
            else None
        ),
        "not_yet_checked": report.not_yet_checked,
        "verbose": report.verbose,
    }
    if report.verbose and report.details:
        obj["details"] = report.details
    return jsonlib.dumps(obj, indent=2, ensure_ascii=False)


def render_no_project_human() -> str:
    """Render the no-project case."""
    return (
        "Stage:   none\n"
        "Hint:    No .awf/project.json found. Run /awf-help or /awf-create-project."
    )


def render_no_project_json() -> str:
    """Render the no-project case as JSON."""
    return jsonlib.dumps(
        {
            "schema_version": 1,
            "project": None,
            "stage": "none",
            "hint": "No .awf/project.json found. Run /awf-help or /awf-create-project.",
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code (0 or 4)."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="awf-status",
        description="Report the current stage, drift, and next actions for this project.",
        add_help=True,
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON (conforms to STATUS_JSON_SCHEMA in lib/state.py).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Extend event tail 5→20 and print per-provider state details.",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 4

    as_json: bool = args.as_json
    verbose: bool = args.verbose
    n_events = 20 if verbose else 5

    # 1. Locate project anchor (optional=True → None if not found)
    anchor: ProjectAnchor | None = None
    try:
        anchor = ProjectAnchor.load()
    except ProjectNotFound:
        anchor = None
    # Any other exception (StateCorruptError, StateValidationError) propagates
    # per T5 decision: local-state corruption is not provider-side uncertainty.

    if anchor is None:
        print(render_no_project_json() if as_json else render_no_project_human())
        return 0

    # 2. Load infra (optional — absent pre-S3)
    infra: Infra | None = None
    try:
        infra = Infra.load()
    except FileNotFoundError:
        infra = None
    # StateCorruptError / StateValidationError propagate per T5.

    # Derive project root from anchor path
    assert anchor._path is not None
    root = anchor._path.parent.parent

    # 3. Drift checks
    drift = run_drift_checks(anchor, infra, verbose=verbose)

    # 4. Recent events
    log_path = root / ".awf" / "log.jsonl"
    recent = tail_events(log_path, n_events)

    # 5. Next composer
    next_composer, next_hint = next_composer_for_stage(anchor.stage)

    # 6. Idle detection
    idle = check_idle(log_path)

    # 7. Verbose details
    details: dict[str, Any] = {}
    if verbose:
        details = _build_verbose_details(anchor, infra, drift)

    report = StatusReport(
        slug=anchor.slug,
        domain=anchor.domain,
        root=root,
        stage=anchor.stage,
        drift=drift,
        recent=recent,
        next_composer=next_composer,
        next_hint=next_hint,
        idle=idle,
        not_yet_checked=list(NOT_YET_CHECKED),
        verbose=verbose,
        details=details,
    )

    output = render_json(report) if as_json else render_human(report)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
