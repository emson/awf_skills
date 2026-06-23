#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///

"""awf-log — CLI window onto the awf event log.

Sub-commands: tail, session, find, diff, note, replay, sessions,
rebuild-index, inventory, where, history.

Exit codes:
    0  — success (including empty results)
    1  — no project found (when required: note sub-command)
    4  — invalid input (bad regex, unknown session, bad arg value)
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Bootstrap: locate AWF_HOME and put lib/ on sys.path ─────────────────────
# Script lives at: <AWF_HOME>/skills/awf-log/scripts/log.py
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME))
sys.path.insert(0, str(AWF_HOME / "lib"))

from lib import log as log_lib  # noqa: E402
from lib.awf_home import user_config_dir  # noqa: E402
from lib.log import (  # noqa: E402
    LOG_DIRNAME,
    LOG_FILENAME,
    SESSIONS_INDEX_FILENAME,
    find_session_bounds,
    iter_sessions,
    latest_session_id,
    read_events,
    tail_events,
)
from lib.project import ProjectNotFound, find_project_root  # noqa: E402
from lib.state import ProjectAnchor  # noqa: E402
from lib import inventory as inventory_lib  # noqa: E402
from lib import revisions as revisions_lib  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _emit_events(
    events: list[dict[str, Any]],
    *,
    as_json: bool,
    banner: str | None = None,
) -> None:
    """Print events to stdout in human or JSONL mode."""
    if as_json:
        for ev in events:
            print(jsonlib.dumps(ev, separators=(",", ":"), ensure_ascii=False))
    else:
        if banner:
            print(banner)
        for ev in events:
            _print_human(ev)


def _print_human(ev: dict[str, Any]) -> None:
    """Print a single event in a compact human-readable line."""
    ts = ev.get("ts", "")[:19].replace("T", " ")
    etype = ev.get("type", "?")
    result = ev.get("result", "")
    skill = ev.get("skill", "")
    data = ev.get("data", {})

    parts = [ts, etype]
    if skill:
        parts.append(f"skill={skill}")
    if result:
        parts.append(f"result={result}")

    # Type-specific data snippet
    if etype == "note":
        text = data.get("text", "")
        if text:
            parts.append(f"text={text[:80]!r}")
    elif etype == "error":
        msg = data.get("message", "")
        if msg:
            parts.append(f"msg={msg[:80]!r}")
    elif etype == "gate.hit":
        gate_name = data.get("gate_name", "")
        if gate_name:
            parts.append(f"gate={gate_name}")
    elif etype == "api.call":
        provider = data.get("provider", "")
        method = data.get("method", "")
        path_ = data.get("path", "")
        status = data.get("status_code", "")
        if provider:
            parts.append(f"{provider} {method} {path_} → {status}")
    elif etype in ("session.start", "session.end"):
        composer = data.get("composer", "")
        target = data.get("target_stage", "")
        if composer:
            parts.append(f"composer={composer} target={target}")
    elif etype == "state.change":
        file_ = data.get("file", "")
        if file_:
            parts.append(f"file={file_}")

    print("  ".join(parts))


def _resolve_log_path(root: Path) -> Path:
    """Return the project log path for *root*."""
    return root / LOG_DIRNAME / LOG_FILENAME


def _find_project_root_optional(cwd: Path | None = None) -> Path | None:
    """Walk up from cwd to find project root; return None if not found."""
    try:
        return find_project_root(cwd)
    except ProjectNotFound:
        return None


# ---------------------------------------------------------------------------
# Sub-command: tail
# ---------------------------------------------------------------------------


def cmd_tail(args: argparse.Namespace) -> int:
    """Print the last N events from the project log."""
    cwd = Path.cwd()
    root = _find_project_root_optional(cwd)

    if root is None:
        print("error: no .awf/project.json found in any ancestor directory", file=sys.stderr)
        return 1

    log_path = _resolve_log_path(root)

    n = args.n
    events = tail_events(log_path, n)

    if not events:
        if not log_path.exists():
            print("# no log file found (project has no recorded events)", file=sys.stderr)
        else:
            print("# log file is empty", file=sys.stderr)
        return 0

    banner = None if args.json else f"# last {len(events)} events (oldest first):"
    _emit_events(events, as_json=args.json, banner=banner)
    return 0


# ---------------------------------------------------------------------------
# Sub-command: session
# ---------------------------------------------------------------------------


def cmd_session(args: argparse.Namespace) -> int:
    """Print all events for a session (by id or 'last')."""
    cwd = Path.cwd()
    root = _find_project_root_optional(cwd)

    if root is None:
        print("error: no .awf/project.json found in any ancestor directory", file=sys.stderr)
        return 1

    log_path = _resolve_log_path(root)
    all_events = list(read_events(log_path))

    session_arg = args.session_id  # "last" or a ULID

    if session_arg == "last":
        sid = latest_session_id(log_path)
        if sid is None:
            print("error: no sessions found in log", file=sys.stderr)
            return 4
    else:
        sid = session_arg

    bounds = find_session_bounds(all_events, sid)
    if bounds is None:
        print(f"error: session {sid!r} not found in log", file=sys.stderr)
        return 4

    start_idx, end_idx = bounds
    session_events = all_events[start_idx : end_idx + 1]

    banner = None if args.json else f"# session {sid} ({len(session_events)} events):"
    _emit_events(session_events, as_json=args.json, banner=banner)
    return 0


# ---------------------------------------------------------------------------
# Sub-command: find
# ---------------------------------------------------------------------------


def cmd_find(args: argparse.Namespace) -> int:
    """Regex search across all events."""
    try:
        pattern = re.compile(args.pattern)
    except re.error as e:
        print(f"error: invalid regex {args.pattern!r}: {e}", file=sys.stderr)
        return 4

    cwd = Path.cwd()
    root = _find_project_root_optional(cwd)

    if root is None:
        print("error: no .awf/project.json found in any ancestor directory", file=sys.stderr)
        return 1

    log_path = _resolve_log_path(root)
    type_filter: str | None = getattr(args, "type", None)

    matches: list[dict[str, Any]] = []
    for ev in read_events(log_path):
        if type_filter and ev.get("type") != type_filter:
            continue
        serialised = jsonlib.dumps(ev, ensure_ascii=False)
        if pattern.search(serialised):
            matches.append(ev)

    if not matches:
        if not args.json:
            print("# no matching events", file=sys.stderr)
        return 0

    # find always outputs JSONL (structured grep); --json flag is a consistency alias
    for ev in matches:
        print(jsonlib.dumps(ev, separators=(",", ":"), ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Sub-command: diff
# ---------------------------------------------------------------------------


def cmd_diff(_args: argparse.Namespace) -> int:
    """Stub: drift detection lands in awf-status (plan_012)."""
    print(
        "drift detection lands in awf-status (plan_012); "
        "for now use `awf-status` (when available) or inspect via "
        "`awf-log session last`"
    )
    return 0


# ---------------------------------------------------------------------------
# Sub-command: note
# ---------------------------------------------------------------------------


def cmd_note(args: argparse.Namespace) -> int:
    """Append a note event to the project log."""
    cwd = Path.cwd()
    root = _find_project_root_optional(cwd)

    if root is None:
        print("error: no .awf/project.json found — cannot append note outside a project", file=sys.stderr)
        return 1

    # Load anchor to populate ContextVars so the event lands in the right file
    try:
        anchor = ProjectAnchor.load(start=root)
    except Exception as e:
        print(f"error: could not load project anchor: {e}", file=sys.stderr)
        return 1

    # Set project context via the public helper (avoids touching private ContextVars).
    # set_project_context mints a session ULID so we can echo it in --json output.
    sid = log_lib.set_project_context(
        root=root,
        slug=anchor.slug,
        stage=anchor.stage,
        actor="cli",
    )

    log_lib.note(args.text, by="human")

    if getattr(args, "json", False):
        print(jsonlib.dumps({"action": "noted", "session": sid}, separators=(",", ":")))
    else:
        print(f"note appended to {root / LOG_DIRNAME / LOG_FILENAME}")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: replay
# ---------------------------------------------------------------------------


def cmd_replay(args: argparse.Namespace) -> int:
    """Render a narrative summary of a session."""
    cwd = Path.cwd()
    root = _find_project_root_optional(cwd)

    if root is None:
        print("error: no .awf/project.json found in any ancestor directory", file=sys.stderr)
        return 1

    log_path = _resolve_log_path(root)
    all_events = list(read_events(log_path))

    session_arg = args.session_id

    if session_arg == "last":
        sid = latest_session_id(log_path)
        if sid is None:
            print("error: no sessions found in log", file=sys.stderr)
            return 4
    else:
        sid = session_arg

    bounds = find_session_bounds(all_events, sid)
    if bounds is None:
        print(f"error: session {sid!r} not found in log", file=sys.stderr)
        return 4

    start_idx, end_idx = bounds
    session_events = all_events[start_idx : end_idx + 1]

    _render_replay(sid, session_events, as_json=getattr(args, "json", False))
    return 0


def _build_replay_data(
    sid: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the structured replay data dict from session events.

    Returns a dict with keys: session, composer, target, result,
    started_at, duration_ms, narrative, steps.
    """
    start_ev = next((e for e in events if e.get("type") == "session.start"), None)
    end_ev = next((e for e in events if e.get("type") == "session.end"), None)

    composer = start_ev["data"].get("composer", "unknown") if start_ev else "unknown"
    target = start_ev["data"].get("target_stage", "unknown") if start_ev else "unknown"
    started_at = start_ev.get("ts", "") if start_ev else ""

    if end_ev:
        result = end_ev.get("result", "unknown")
        duration_ms: int | None = end_ev.get("duration_ms")
    else:
        result = "in-progress"
        duration_ms = None

    # Collect steps
    skills_seen: list[str] = []
    gates: list[str] = []
    errors: list[str] = []
    steps: list[dict[str, Any]] = []

    for ev in events:
        etype = ev.get("type")
        if etype == "skill.invoke":
            skill_name = ev.get("skill", "unknown")
            skills_seen.append(skill_name)
            steps.append({"type": "skill", "skill": skill_name, "action": "invoke", "result": None})
        elif etype == "skill.complete":
            skill_name = ev.get("skill", "unknown")
            step_result = ev.get("result", "?")
            # Update the matching invoke step
            for step in reversed(steps):
                if step.get("type") == "skill" and step.get("skill") == skill_name and step.get("result") is None:
                    step["result"] = step_result
                    step["duration_ms"] = ev.get("duration_ms")
                    break
        elif etype == "gate.hit":
            gate_name = ev["data"].get("gate_name", "unknown")
            gates.append(gate_name)
            steps.append({"type": "gate", "skill": None, "action": gate_name, "result": "gate"})
        elif etype == "error":
            msg = ev["data"].get("message", "unknown")
            errors.append(msg)
            steps.append({"type": "error", "skill": None, "action": msg, "result": "fail"})
        elif etype == "note":
            text = ev.get("data", {}).get("text", "")
            steps.append({"type": "note", "skill": None, "action": text, "result": None})

    # Build narrative following the spec template:
    # "Composer <C> targeting <T> started at <ts>. <N> atomic skills ran (<list>).
    #  <K> gates hit. <Result> in <duration_ms>ms."
    started_short = started_at[:19].replace("T", " ") if started_at else "unknown"
    skill_list = ", ".join(sorted(set(skills_seen))) if skills_seen else "none"
    n_skills = len(skills_seen)

    if end_ev and duration_ms is not None:
        ending = f"{result} in {duration_ms}ms."
    elif end_ev:
        ending = f"{result}."
    else:
        ending = "still running (no session.end recorded)."

    error_clause = ""
    if result == "fail" and errors:
        error_clause = f" Errors: {'; '.join(errors[:3])}."

    narrative = (
        f"Composer {composer} targeting {target} started at {started_short}. "
        f"{n_skills} atomic skill(s) ran ({skill_list}). "
        f"{len(gates)} gate(s) hit. "
        f"{ending}"
        f"{error_clause}"
    )

    return {
        "session": sid,
        "composer": composer,
        "target": target,
        "result": result,
        "started_at": started_at,
        "duration_ms": duration_ms,
        "narrative": narrative,
        "steps": steps,
    }


def _render_replay(sid: str, events: list[dict[str, Any]], *, as_json: bool = False) -> None:
    """Print a narrative replay of a session to stdout."""
    data = _build_replay_data(sid, events)

    if as_json:
        print(jsonlib.dumps(data, separators=(",", ":"), ensure_ascii=False))
        return

    # Human-readable output
    end_ev = next((e for e in events if e.get("type") == "session.end"), None)
    started_short = data["started_at"][:19].replace("T", " ") if data["started_at"] else "unknown"

    print(f"# Session replay: {sid}")
    print(f"  composer : {data['composer']}")
    print(f"  target   : {data['target']}")
    print(f"  started  : {started_short}")

    if end_ev:
        ended_at = end_ev.get("ts", "unknown")[:19].replace("T", " ")
        print(f"  ended    : {ended_at}")
        if data["duration_ms"] is not None:
            print(f"  duration : {data['duration_ms']} ms")
        print(f"  result   : {data['result']}")
    else:
        print("  ended    : no session.end recorded")

    print()
    print("## Narrative")
    print(f"  {data['narrative']}")

    print()
    print("## Steps")

    skill_steps = [s for s in data["steps"] if s["type"] == "skill"]
    if skill_steps:
        for i, step in enumerate(skill_steps, 1):
            step_result = step.get("result") or "pending"
            print(f"  {i:2}. {step['skill']:<40} [{step_result}]")
    else:
        # No skill.invoke events — list other notable events
        non_session = [e for e in events if e.get("type") not in ("session.start", "session.end")]
        for ev in non_session:
            _print_human(ev)

    gate_steps = [s for s in data["steps"] if s["type"] == "gate"]
    if gate_steps:
        print()
        print("## Gates hit")
        for step in gate_steps:
            print(f"  - {step['action']}")

    error_steps = [s for s in data["steps"] if s["type"] == "error"]
    if error_steps:
        print()
        print("## Errors")
        for step in error_steps:
            print(f"  - {step['action']}")


# ---------------------------------------------------------------------------
# Sub-command: sessions
# ---------------------------------------------------------------------------


def cmd_sessions(args: argparse.Namespace) -> int:
    """List sessions from the cross-project index."""
    index_path = user_config_dir() / SESSIONS_INDEX_FILENAME
    days = args.days
    cutoff: datetime | None = None
    if days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    all_sessions = list(iter_sessions(index_path))

    if cutoff is not None:
        filtered = []
        for s in all_sessions:
            started = s.get("started_at", "")
            if started:
                try:
                    # Parse ISO 8601 with Z suffix
                    dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if dt >= cutoff:
                        filtered.append(s)
                except ValueError:
                    filtered.append(s)  # include unparseable entries
            else:
                filtered.append(s)
        sessions = filtered
    else:
        sessions = all_sessions

    if not sessions:
        if not args.json:
            print("# no sessions found" + (f" in the last {days} days" if days > 0 else ""))
        return 0

    if args.json:
        for s in sessions:
            print(jsonlib.dumps(s, separators=(",", ":"), ensure_ascii=False))
        return 0

    # Human-readable table
    header = f"{'started':<20}  {'project':<20}  {'composer':<28}  {'result':<8}  {'events':>6}  {'gates':>5}"
    print(header)
    print("-" * len(header))
    for s in sessions:
        started = s.get("started_at", "")[:19].replace("T", " ")
        project = s.get("project_slug", "")[:20]
        composer = s.get("composer", "")[:28]
        result = s.get("result", "")[:8]
        events_count = s.get("events_count", 0)
        gates = s.get("gates_hit", 0)
        print(f"{started:<20}  {project:<20}  {composer:<28}  {result:<8}  {events_count:>6}  {gates:>5}")

    return 0


# ---------------------------------------------------------------------------
# Sub-command: rebuild-index (refresh the applied-state inventory cache)
# ---------------------------------------------------------------------------


def cmd_rebuild_index(args: argparse.Namespace) -> int:
    """Re-scan known projects and rewrite the inventory cache (D-012)."""
    config_dir = user_config_dir()
    # Include the current project even if it has no session in the index yet.
    extra: list[Path] = []
    cur = _find_project_root_optional()
    if cur is not None:
        extra.append(cur)
    records = inventory_lib.rebuild_inventory(config_dir, extra_roots=extra)
    n_projects = len({r.project_path for r in records})
    if args.json:
        print(jsonlib.dumps({"action": "rebuilt", "resources": len(records), "projects": n_projects}))
    else:
        print(f"- rebuilt inventory: {len(records)} resources across {n_projects} project(s)")
        print(f"  {config_dir / inventory_lib.INVENTORY_FILENAME}")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: inventory (list applied resources across projects)
# ---------------------------------------------------------------------------


def _load_or_build_inventory() -> list:
    """Load the cache; if empty/absent, build it once so the view is never blank."""
    config_dir = user_config_dir()
    records = inventory_lib.load_inventory(config_dir)
    if not records:
        extra: list[Path] = []
        cur = _find_project_root_optional()
        if cur is not None:
            extra.append(cur)
        records = inventory_lib.rebuild_inventory(config_dir, extra_roots=extra)
    return records


def cmd_inventory(args: argparse.Namespace) -> int:
    """List applied resources, optionally filtered by provider/project."""
    records = _load_or_build_inventory()
    if args.provider:
        records = [r for r in records if r.provider == args.provider]
    if args.project:
        records = [r for r in records if args.project in (r.project, r.project_path)]

    if args.json:
        for r in records:
            print(jsonlib.dumps(r.__dict__, separators=(",", ":"), ensure_ascii=False))
        return 0

    if not records:
        print("# no applied resources found (run `awf-log rebuild-index`)")
        return 0

    header = f"{'project':<16}  {'provider':<10}  {'type':<13}  {'resource_id':<26}  {'by skill':<22}  applied"
    print(header)
    print("-" * len(header))
    for r in sorted(records, key=lambda x: (x.project, x.provider, x.resource_type)):
        applied = (r.last_applied or "")[:19].replace("T", " ")
        rid = r.resource_id if len(r.resource_id) <= 26 else r.resource_id[:23] + "..."
        print(
            f"{r.project[:16]:<16}  {r.provider[:10]:<10}  {r.resource_type[:13]:<13}  "
            f"{rid:<26}  {(r.applied_by_skill or '-')[:22]:<22}  {applied}"
        )
    return 0


# ---------------------------------------------------------------------------
# Sub-command: where (reverse-lookup a resource id → project)
# ---------------------------------------------------------------------------


def cmd_where(args: argparse.Namespace) -> int:
    """Find which project(s) a resource id belongs to."""
    records = _load_or_build_inventory()
    hits = inventory_lib.where(records, args.resource_id)
    if args.json:
        for r in hits:
            print(jsonlib.dumps(r.__dict__, separators=(",", ":"), ensure_ascii=False))
        return 0
    if not hits:
        print(f"# no resource matching {args.resource_id!r} (try `awf-log rebuild-index`)")
        return 0
    for r in hits:
        print(f"{r.resource_id}  ({r.provider}/{r.resource_type})")
        print(f"    project: {r.project}  @ {r.project_path}")
        if r.applied_by_skill:
            print(f"    applied by {r.applied_by_skill} at {r.last_applied} (session {r.session})")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: history (applied-revisions for a project)
# ---------------------------------------------------------------------------


def cmd_history(args: argparse.Namespace) -> int:
    """Show a project's applied-state revisions (D-012; `helm history` model)."""
    root = revisions_lib.resolve_project_root(user_config_dir(), args.project)
    if root is None:
        target = args.project or "the current directory"
        print(f"error: could not resolve a project for {target!r}", file=sys.stderr)
        return 1

    revisions = revisions_lib.project_revisions(root)

    if args.revision is not None:
        rev = next((r for r in revisions if r.n == args.revision), None)
        if rev is None:
            print(f"error: project has no revision {args.revision}", file=sys.stderr)
            return 4
        if args.json:
            print(jsonlib.dumps(revisions_lib.as_dict(rev), separators=(",", ":"), ensure_ascii=False))
            return 0
        print(f"# revision {rev.n} — {root.name}")
        print(f"  when:    {rev.ts}")
        print(f"  by:      {rev.skill or '-'}  (session {rev.session or '-'})")
        print(f"  file:    {rev.file}")
        for label, keys in (("added", rev.added), ("removed", rev.removed), ("changed", rev.changed)):
            for k in keys:
                print(f"  {label:<8} {k}")
        return 0

    if args.json:
        for rev in revisions:
            print(jsonlib.dumps(revisions_lib.as_dict(rev), separators=(",", ":"), ensure_ascii=False))
        return 0

    if not revisions:
        print(f"# {root.name}: no applied revisions yet")
        return 0

    print(f"# {root.name}: {len(revisions)} applied revision(s) — newest last")
    header = f"{'rev':>4}  {'when':<20}  {'by skill':<22}  {'change':<10}  keys"
    print(header)
    print("-" * len(header))
    for rev in revisions:
        when = (rev.ts or "")[:19].replace("T", " ")
        keys = ", ".join(rev.touched())
        if len(keys) > 40:
            keys = keys[:37] + "..."
        print(f"{rev.n:>4}  {when:<20}  {(rev.skill or '-')[:22]:<22}  {rev.summary():<10}  {keys}")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="awf-log",
        description="CLI window onto the awf event log.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # tail
    p_tail = sub.add_parser("tail", help="Print last N events from the project log")
    p_tail.add_argument("-n", type=int, default=50, metavar="N", help="Number of events (default: 50)")
    p_tail.add_argument("--json", action="store_true", help="Emit raw JSONL")

    # session
    p_session = sub.add_parser("session", help="Print events for a session")
    p_session.add_argument(
        "session_id", nargs="?", default="last",
        metavar="ID", help="Session ULID or 'last' (default: last)"
    )
    p_session.add_argument("--json", action="store_true", help="Emit raw JSONL")

    # find
    p_find = sub.add_parser("find", help="Regex search across events")
    p_find.add_argument("pattern", help="Regex pattern to search for")
    p_find.add_argument("--type", dest="type", metavar="EVENT_TYPE", help="Filter to event type first")
    p_find.add_argument("--json", action="store_true", help="Emit raw JSONL (default for find)")

    # diff
    sub.add_parser("diff", help="Drift detection stub (see awf-status)")

    # note
    p_note = sub.add_parser("note", help="Append a manual note event")
    p_note.add_argument("text", help="Note text to append")
    p_note.add_argument("--json", action="store_true", help='Emit {"action":"noted","session":"<id>"}')

    # replay
    p_replay = sub.add_parser("replay", help="Render narrative summary of a session")
    p_replay.add_argument(
        "session_id", nargs="?", default="last",
        metavar="ID", help="Session ULID or 'last' (default: last)"
    )
    p_replay.add_argument("--json", action="store_true", help='Emit {"narrative":"...","steps":[...]}')

    # sessions
    p_sessions = sub.add_parser("sessions", help="List sessions from cross-project index")
    p_sessions.add_argument(
        "--days", type=int, default=30, metavar="N",
        help="Limit to sessions started in the last N days (0 = all; default: 30)"
    )
    p_sessions.add_argument("--json", action="store_true", help="Emit raw JSONL")

    # rebuild-index
    p_rebuild = sub.add_parser(
        "rebuild-index", help="Re-scan known projects and refresh the applied-state inventory"
    )
    p_rebuild.add_argument("--json", action="store_true", help="Emit a JSON summary")

    # inventory
    p_inv = sub.add_parser("inventory", help="List applied resources across all projects")
    p_inv.add_argument("--provider", metavar="NAME", help="Filter by provider (cloudflare, neon, ...)")
    p_inv.add_argument("--project", metavar="SLUG", help="Filter by project slug or path")
    p_inv.add_argument("--json", action="store_true", help="Emit raw JSONL")

    # where
    p_where = sub.add_parser("where", help="Find which project a resource id belongs to")
    p_where.add_argument("resource_id", help="Full or prefix resource id to look up")
    p_where.add_argument("--json", action="store_true", help="Emit raw JSONL")

    # history
    p_history = sub.add_parser(
        "history", help="Show a project's applied-state revisions (helm-history style)"
    )
    p_history.add_argument(
        "project", nargs="?", default=None,
        metavar="SLUG", help="Project slug or path (default: the current project)"
    )
    p_history.add_argument(
        "--revision", type=int, metavar="N", help="Show the detail of revision N"
    )
    p_history.add_argument("--json", action="store_true", help="Emit raw JSONL")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_SUBCOMMAND_MAP = {
    "tail": cmd_tail,
    "session": cmd_session,
    "find": cmd_find,
    "diff": cmd_diff,
    "note": cmd_note,
    "replay": cmd_replay,
    "sessions": cmd_sessions,
    "rebuild-index": cmd_rebuild_index,
    "inventory": cmd_inventory,
    "where": cmd_where,
    "history": cmd_history,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _SUBCOMMAND_MAP[args.subcommand]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
