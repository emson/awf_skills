#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///

"""awf-log — CLI window onto the awf event log.

Sub-commands: tail, session, find, diff, note, replay, sessions.

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

    # Set project ContextVars manually (we're outside a log.session)
    tok_root = log_lib._current_project_root.set(root)
    tok_slug = log_lib._current_project_slug.set(anchor.slug)
    tok_stage = log_lib._current_stage.set(anchor.stage)
    tok_actor = log_lib._current_actor.set("cli")
    try:
        log_lib.note(args.text, by="human")
    finally:
        log_lib._current_project_root.reset(tok_root)
        log_lib._current_project_slug.reset(tok_slug)
        log_lib._current_stage.reset(tok_stage)
        log_lib._current_actor.reset(tok_actor)

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

    _render_replay(sid, session_events)
    return 0


def _render_replay(sid: str, events: list[dict[str, Any]]) -> None:
    """Print a narrative replay of a session to stdout."""
    start_ev = next((e for e in events if e.get("type") == "session.start"), None)
    end_ev = next((e for e in events if e.get("type") == "session.end"), None)

    composer = start_ev["data"].get("composer", "unknown") if start_ev else "unknown"
    target = start_ev["data"].get("target_stage", "unknown") if start_ev else "unknown"
    started_at = start_ev.get("ts", "unknown")[:19].replace("T", " ") if start_ev else "unknown"

    print(f"# Session replay: {sid}")
    print(f"  composer : {composer}")
    print(f"  target   : {target}")
    print(f"  started  : {started_at}")

    if end_ev:
        ended_at = end_ev.get("ts", "unknown")[:19].replace("T", " ")
        duration = end_ev.get("duration_ms")
        result = end_ev.get("result", "unknown")
        print(f"  ended    : {ended_at}")
        if duration is not None:
            print(f"  duration : {duration} ms")
        print(f"  result   : {result}")
    else:
        print("  ended    : no session.end recorded")

    print()
    print("## Steps")

    skills_seen: list[str] = []
    gates: list[str] = []
    errors: list[str] = []

    for ev in events:
        etype = ev.get("type")
        if etype == "skill.invoke":
            skill_name = ev.get("skill", "unknown")
            result = ev.get("result", "")
            # Find the matching complete event
            skills_seen.append(skill_name)
        elif etype == "gate.hit":
            gates.append(ev["data"].get("gate_name", "unknown"))
        elif etype == "error":
            errors.append(ev["data"].get("message", "unknown"))

    for i, skill_name in enumerate(skills_seen, 1):
        # Find result from skill.complete event
        complete_ev = next(
            (
                e
                for e in events
                if e.get("type") == "skill.complete" and e.get("skill") == skill_name
            ),
            None,
        )
        step_result = complete_ev.get("result", "?") if complete_ev else "pending"
        print(f"  {i:2}. {skill_name:<40} [{step_result}]")

    if not skills_seen:
        # No skill.invoke events — just list other notable events
        non_session = [e for e in events if e.get("type") not in ("session.start", "session.end")]
        for ev in non_session:
            _print_human(ev)

    if gates:
        print()
        print("## Gates hit")
        for g in gates:
            print(f"  - {g}")

    if errors:
        print()
        print("## Errors")
        for err in errors:
            print(f"  - {err}")

    # Narrative summary sentence
    print()
    if end_ev:
        result = end_ev.get("result", "unknown")
        print(
            f"Session {sid[:12]}... ran {len(skills_seen)} skill(s) "
            f"with {len(gates)} gate(s) and {len(errors)} error(s); "
            f"result={result}."
        )
    else:
        print(
            f"Session {sid[:12]}... ran {len(skills_seen)} skill(s) "
            f"with {len(gates)} gate(s) and {len(errors)} error(s); "
            f"no session.end recorded."
        )


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

    # replay
    p_replay = sub.add_parser("replay", help="Render narrative summary of a session")
    p_replay.add_argument(
        "session_id", nargs="?", default="last",
        metavar="ID", help="Session ULID or 'last' (default: last)"
    )

    # sessions
    p_sessions = sub.add_parser("sessions", help="List sessions from cross-project index")
    p_sessions.add_argument(
        "--days", type=int, default=30, metavar="N",
        help="Limit to sessions started in the last N days (0 = all; default: 30)"
    )
    p_sessions.add_argument("--json", action="store_true", help="Emit raw JSONL")

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
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _SUBCOMMAND_MAP[args.subcommand]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
