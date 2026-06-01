"""Structured append-only event logger for awf-skills.

References:
  - ADR D-002 (decisions.md): locked logging model
  - docs/08-logging.md: full event schema, redaction policy, concurrency rules
  - spec.md § A3: public API and acceptance criteria

Public API (module-as-namespace; import as ``from lib import log``):

  log.session(composer, target)           context manager → yields session_id str
  log.invoke(skill, args)                 context manager
  log.api(provider, method, path, status_code, resource_id)
  log.state_change(file, key, before, after)
  log.gate(name, reason, instructions)
  log.error(msg, hint)
  log.intent(action, impact)              gated: AWF_LOG_INTENTS=1 OR set_dry_run(True)
  log.note(text, by)
  log.safe_log(value)                     public redaction helper
  log.set_dry_run(active)                 toggle intent-event gating
  log.file_write(path, bytes)             for filesystem mutations (no state.change emitted)

Design decisions implemented here (locked in plan_003):
  1. ContextVar threading: session_id, skill, etc. thread automatically.
     Two ContextVars own ``session`` context; one owns ``invoke`` context.
  2. safe_log is the only redaction point; all public API paths call it.
  3. ``_write_event`` is the only file-I/O sink; all public API helpers route
     through it. Never raises — OSError → stderr warning.
  4. ULID implemented inline (~25 lines), no new dependency.
  5. Atomic append via O_APPEND for writes ≤4 KB; fcntl.flock above.
  6. ``state_change`` caps before/after at 2 KB each; oversized sides are
     replaced with sha256 hash + pointer.
  7. Intent events are gated at call time (not module load).
  8. ``_current_summary`` is the correct sentinel for "inside a session" —
     not ``_current_session_id``, which is auto-minted for atomic skills
     called outside any composer.
  9. No ``log`` attribute defined inside this module. Canonical import is
     ``from lib import log``. The ``from lib.log import log`` idiom (shown
     informally in 08-logging.md) also works in Python (yields the module
     object itself) but is non-idiomatic; see module docstring note above.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterator

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

LOG_DIRNAME = ".awf"
LOG_FILENAME = "log.jsonl"
SESSIONS_INDEX_FILENAME = "sessions.jsonl"
ATOMIC_APPEND_THRESHOLD = 4096       # bytes (POSIX PIPE_BUF)
STATE_CHANGE_SIDE_CAP = 2048         # bytes per side before cap-and-hash
REDACTION_PLACEHOLDER = "***"

# Crockford base32 alphabet (omits I, L, O, U)
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# ---------------------------------------------------------------------------
# Redaction regexes (compiled once at module load)
# ---------------------------------------------------------------------------

# Key-name denylist: case-insensitive match. Allowlist checked first.
_KEY_DENYLIST_RE = re.compile(
    r"^(authorization|.*_token|.*_secret|.*_password|.*_key)$",
    re.IGNORECASE,
)
# Allowlist suffixes: keys ending with these are NOT redacted even if
# they would match the denylist (e.g., API_KEY_ID is an identifier, not a secret).
_KEY_ALLOWLIST_SUFFIXES = ("_key_id",)

# Value scrubber: bearer tokens and well-known prefixed secrets.
_VALUE_SCRUB_RE = re.compile(
    r"(Bearer\s+\S+"
    r"|\bsk-[A-Za-z0-9_-]{10,}\b"
    r"|\bpat-[A-Za-z0-9_-]{10,}\b"
    r"|\bhk-[A-Za-z0-9_-]{10,}\b"
    r"|\btok-[A-Za-z0-9_-]{10,}\b)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Module-level mutable state
# ---------------------------------------------------------------------------

# Process-global dry-run flag (not a ContextVar: dry-run is a CLI mode,
# not a per-call attribute). Toggle via set_dry_run().
_dry_run_active: bool = False

# ---------------------------------------------------------------------------
# ContextVars (seven total)
# ---------------------------------------------------------------------------

_current_session_id: ContextVar[str | None] = ContextVar(
    "awf_log_session_id", default=None
)
_current_project_slug: ContextVar[str] = ContextVar(
    "awf_log_project_slug", default=""
)
_current_project_root: ContextVar[Path | None] = ContextVar(
    "awf_log_project_root", default=None
)
_current_stage: ContextVar[str] = ContextVar("awf_log_stage", default="")
_current_skill: ContextVar[str | None] = ContextVar(
    "awf_log_skill", default=None
)
_current_actor: ContextVar[str] = ContextVar(
    "awf_log_actor", default="claude-code"
)
# Per-session accounting bucket. Set by session() context manager; None
# outside any session. Used as the sentinel for "are we inside a session?"
# (not _current_session_id, which is auto-minted by _build_record for
# atomic skills called outside any composer — see design decision above).
_current_summary: ContextVar[dict[str, Any] | None] = ContextVar(
    "awf_log_summary", default=None
)

# ---------------------------------------------------------------------------
# ULID — inline Crockford base32 implementation (~25 lines)
# ---------------------------------------------------------------------------


def _ulid() -> str:
    """Generate a 26-character Crockford base32 ULID.

    Structure: 48-bit millisecond timestamp (10 chars) + 80-bit randomness
    (16 chars) = 26 chars total. Lexicographic sort order matches creation
    order at millisecond resolution.

    No external dependency; algorithm follows the ULID spec exactly.
    """
    ts_ms = time.time_ns() // 1_000_000   # 48-bit millisecond timestamp
    rand_bytes = secrets.token_bytes(10)   # 80 bits of randomness
    # Pack into a 128-bit integer: top 48 bits = timestamp, bottom 80 = random
    rand_int = int.from_bytes(rand_bytes, "big")
    value = (ts_ms << 80) | rand_int
    # Encode as 26 Crockford base32 characters (most-significant first)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def safe_log(value: Any) -> Any:
    """Recursively redact secrets from value before logging.

    - dict: redact values whose key matches the denylist (allowlist checked
      first); recurse into non-redacted values.
    - list/tuple: recurse element-wise.
    - str: scrub bearer tokens and sk-/pat-/hk-/tok- prefixed secrets.
    - other: return unchanged.

    This is a pure function: returns a new object, does not mutate input.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            if _should_redact_key(k):
                result[k] = REDACTION_PLACEHOLDER
            else:
                result[k] = safe_log(v)
        return result
    if isinstance(value, list):
        return [safe_log(item) for item in value]
    if isinstance(value, tuple):
        return tuple(safe_log(item) for item in value)
    if isinstance(value, str):
        return _VALUE_SCRUB_RE.sub(REDACTION_PLACEHOLDER, value)
    return value


def _should_redact_key(key: str) -> bool:
    """Return True if key should be redacted (denylist minus allowlist)."""
    key_lower = key.lower()
    for suffix in _KEY_ALLOWLIST_SUFFIXES:
        if key_lower.endswith(suffix):
            return False
    return bool(_KEY_DENYLIST_RE.match(key))

# ---------------------------------------------------------------------------
# Event record builder
# ---------------------------------------------------------------------------


def _build_record(
    type_: str,
    *,
    skill: str | None = None,
    result: str | None = None,
    duration_ms: int | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serialisable event record.

    Atomic skills called outside any composer auto-mint a one-event
    session ULID (op rule 3 from 08-logging.md).
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "session": _current_session_id.get() or _ulid(),
        "project": _current_project_slug.get(),
        "stage": _current_stage.get(),
        "actor": _current_actor.get(),
        "type": type_,
    }
    # Omit skill key entirely when no skill is active (not null — absent)
    effective_skill = skill or _current_skill.get()
    if effective_skill is not None:
        record["skill"] = effective_skill
    if result is not None:
        record["result"] = result
    if duration_ms is not None:
        record["duration_ms"] = duration_ms
    record["data"] = safe_log(data or {})
    return record

# ---------------------------------------------------------------------------
# File I/O sink (the only I/O path in this module)
# ---------------------------------------------------------------------------


def _resolve_log_path() -> Path:
    """Resolve the target log file path for the current context.

    If we are inside a project context, writes to ``<root>/.awf/log.jsonl``.
    Outside any project, falls back to the user-scope orphan log — a
    graceful degradation (events are still recorded, just at user scope).
    """
    root = _current_project_root.get()
    if root is not None:
        return root / LOG_DIRNAME / LOG_FILENAME
    from lib.awf_home import user_config_dir  # deferred: avoid cycle at import
    return user_config_dir() / "orphan-log.jsonl"


def _append_bytes(path: Path, line_bytes: bytes) -> None:
    """Append line_bytes to path using atomic O_APPEND or flock.

    ≤4 KB: POSIX O_APPEND guarantees atomicity (PIPE_BUF bound).
    >4 KB: acquire exclusive flock before writing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(line_bytes) <= ATOMIC_APPEND_THRESHOLD:
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line_bytes)
        finally:
            os.close(fd)
    else:
        with open(path, "ab") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.write(line_bytes)
                fh.flush()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _write_event(record: dict[str, Any]) -> None:
    """Serialise record and append to the log. Never raises.

    Two layers of exception swallowing enforce D-002 op rule 1:
    "logging never raises."
    """
    try:
        line_bytes = (
            json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        path = _resolve_log_path()
        _append_bytes(path, line_bytes)
    except OSError as e:
        print(f"warn: log write failed: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — intentional broad catch
        print(f"warn: log write failed (unexpected): {e}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Central session index
# ---------------------------------------------------------------------------


def _append_session_index(
    summary: dict[str, Any],
    sid: str,
    composer: str,
    target: str,
    started_at: str,
    ended_at: str,
) -> None:
    """Append one summary line to ~/.config/awf/sessions.jsonl.

    Failure writes a stderr warning; the project-local log is unaffected.
    """
    try:
        from lib.awf_home import user_config_dir  # deferred import

        root = _current_project_root.get()
        line: dict[str, Any] = {
            "session_id": sid,
            "project_slug": _current_project_slug.get(),
            "project_path": str(root) if root is not None else "",
            "composer": composer,
            "target": target,
            "started_at": started_at,
            "ended_at": ended_at,
            "events_count": summary["events_count"],
            "gates_hit": summary["gates_hit"],
            "result": summary["result"],
        }
        index_path = user_config_dir() / SESSIONS_INDEX_FILENAME
        line_bytes = (
            json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        _append_bytes(index_path, line_bytes)
    except OSError as e:
        print(f"warn: session index write failed: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — intentional broad catch
        print(f"warn: session index write failed (unexpected): {e}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Context managers
# ---------------------------------------------------------------------------


@contextmanager
def session(
    composer: str,
    target: str,
    *,
    start: Path | None = None,
) -> Generator[str, None, None]:
    """Context manager that emits session.start / session.end with timing.

    Threads session_id, project_slug, project_root, and stage through all
    nested events via ContextVars. On exit, appends a summary line to the
    central sessions index.

    Yields the session ULID string.
    """
    sid = _ulid()

    # Resolve project context optimistically — failure is silent
    root: Path | None = None
    slug = ""
    stage = ""
    try:
        from lib.project import find_project_root  # deferred: avoid cycle

        root = find_project_root(start, optional=True)
    except Exception:  # noqa: BLE001
        root = None

    if root is not None:
        try:
            from lib.state import ProjectAnchor  # deferred: avoid cycle

            anchor = ProjectAnchor.load(start=root)
            slug = anchor.slug
            stage = anchor.stage
        except Exception:  # noqa: BLE001
            slug = ""
            stage = ""

    # Set ContextVars and save tokens for reset in finally
    tok_sid = _current_session_id.set(sid)
    tok_slug = _current_project_slug.set(slug)
    tok_root = _current_project_root.set(root)
    tok_stage = _current_stage.set(stage)

    # Initialise per-session accounting bucket
    summary: dict[str, Any] = {
        "events_count": 0,
        "gates_hit": 0,
        "result": "ok",
    }
    tok_summary = _current_summary.set(summary)

    t_start = time.monotonic()
    started_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

    _write_event(
        _build_record(
            "session.start",
            data={"composer": composer, "target_stage": target, "source_stage": stage},
        )
    )

    try:
        yield sid
    except Exception:
        summary["result"] = "fail"
        raise
    finally:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        ended_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        _write_event(
            _build_record(
                "session.end",
                duration_ms=duration_ms,
                result=summary["result"],
                data={
                    "events_count": summary["events_count"],
                    "gates_hit": summary["gates_hit"],
                    "summary": summary["result"],
                },
            )
        )
        _append_session_index(summary, sid, composer, target, started_at, ended_at)
        # Reset all ContextVars via saved tokens
        _current_summary.reset(tok_summary)
        _current_stage.reset(tok_stage)
        _current_project_root.reset(tok_root)
        _current_project_slug.reset(tok_slug)
        _current_session_id.reset(tok_sid)


@contextmanager
def invoke(
    skill: str,
    args: dict[str, Any] | None = None,
    *,
    result: str | None = None,
) -> Iterator[None]:
    """Context manager that emits skill.invoke / skill.complete with timing.

    Sets _current_skill for the duration so nested api/gate/error events
    carry the skill name.
    """
    tok_skill = _current_skill.set(skill)
    redacted_args = safe_log(args or {})

    _write_event(
        _build_record(
            "skill.invoke",
            skill=skill,
            result="pending",
            data={"args": redacted_args},
        )
    )
    t_start = time.monotonic()

    def _emit_complete(r: str) -> None:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        _write_event(
            _build_record(
                "skill.complete",
                skill=skill,
                result=r,
                duration_ms=duration_ms,
                data={"args": redacted_args},
            )
        )

    try:
        yield
    except Exception:
        _emit_complete("fail")
        raise
    else:
        _emit_complete("ok")
    finally:
        _current_skill.reset(tok_skill)

# ---------------------------------------------------------------------------
# state_change cap helpers
# ---------------------------------------------------------------------------


def _cap_side(
    value: dict[str, Any],
    event_id: str,
    file: str,
    side: str,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Check one side (before or after) against the 2 KB cap.

    Returns (embedded_value, hash_str, pointer_str).
    If the value fits, embedded_value is the dict and hash/pointer are None.
    If oversized, embedded_value is None and hash/pointer carry the forensic info.
    """
    serialised = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    encoded = serialised.encode("utf-8")
    if len(encoded) <= STATE_CHANGE_SIDE_CAP:
        return value, None, None
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    hash_str = f"sha256:{digest}"
    pointer_str = f"{file}@{event_id}"
    return None, hash_str, pointer_str


def _build_diff_summary(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Compute a lightweight key-level diff (values are never included).

    Returns a dict with:
      added:   list of keys present in after but not before
      removed: list of keys present in before but not after
      changed: list of keys present in both but with differing values
    """
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed = sorted(
        k for k in before_keys & after_keys if before[k] != after[k]
    )
    return {"added": added, "removed": removed, "changed": changed}

# ---------------------------------------------------------------------------
# One-shot event helpers
# ---------------------------------------------------------------------------


def api(
    provider: str,
    method: str,
    path: str,
    status_code: int,
    resource_id: str | None = None,
) -> None:
    """Emit an api.call event.

    Note: the parameter is named ``status_code`` (matching spec.md § A3).
    The informal example in 08-logging.md that uses ``status=...`` is a
    doc error — do not call this function with ``status=`` or you will
    receive a TypeError. A follow-up doc fix to 08-logging.md is pending
    (out of scope for plan 003).
    """
    try:
        event_result = "ok" if status_code < 400 else "fail"
        data: dict[str, Any] = {
            "provider": provider,
            "method": method,
            "path": path,
            "status_code": status_code,
        }
        if resource_id is not None:
            data["resource_id"] = resource_id
        _write_event(_build_record("api.call", result=event_result, data=data))
        if (s := _current_summary.get()) is not None:
            s["events_count"] += 1
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.api failed: {e}", file=sys.stderr)


def state_change(
    file: str,
    key: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    """Emit a state.change event with cap-and-hash for oversized sides.

    Signature exactly matches what lib/state.py:_emit_state_change calls:
      log.state_change(file=str(p), key="", before=before, after=after)

    key="" signals a whole-file save; per-key emissions are reserved for
    future fine-grained call sites.
    """
    try:
        # Mint an event ID now so the cap pointer can reference it
        event_id = _ulid()

        diff = _build_diff_summary(before, after)

        embedded_before, before_hash, before_pointer = _cap_side(
            before, event_id, file, "before"
        )
        embedded_after, after_hash, after_pointer = _cap_side(
            after, event_id, file, "after"
        )

        data: dict[str, Any] = {"file": file, "key": key, "diff": diff}
        if embedded_before is not None:
            data["before"] = embedded_before
        else:
            data["before_hash"] = before_hash
            data["before_pointer"] = before_pointer
        if embedded_after is not None:
            data["after"] = embedded_after
        else:
            data["after_hash"] = after_hash
            data["after_pointer"] = after_pointer

        # Build record manually so we can inject the pre-minted event_id
        record = _build_record("state.change", data=data)
        record["session"] = _current_session_id.get() or event_id
        _write_event(record)
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.state_change failed: {e}", file=sys.stderr)


def gate(
    name: str,
    reason: str,
    instructions: str = "",
) -> None:
    """Emit a gate.hit event and increment the session gates_hit counter."""
    try:
        _write_event(
            _build_record(
                "gate.hit",
                result="gate",
                data={"gate_name": name, "reason": reason, "instructions": instructions},
            )
        )
        if (s := _current_summary.get()) is not None:
            s["gates_hit"] += 1
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.gate failed: {e}", file=sys.stderr)


def error(
    msg: str,
    hint: str | None = None,
) -> None:
    """Emit an error event and mark the surrounding session result as 'fail'."""
    try:
        data: dict[str, Any] = {"message": msg}
        if hint is not None:
            data["hint"] = hint
        _write_event(_build_record("error", result="fail", data=data))
        if (s := _current_summary.get()) is not None:
            s["result"] = "fail"
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.error failed: {e}", file=sys.stderr)


def intent(
    action: str,
    impact: str,
) -> None:
    """Emit an intent event (dry-run preview).

    Gated: emits only when AWF_LOG_INTENTS=1 env var is set OR the
    process-global _dry_run_active flag is True. Silent otherwise.
    """
    if not (os.environ.get("AWF_LOG_INTENTS") == "1" or _dry_run_active):
        return
    try:
        _write_event(
            _build_record(
                "intent",
                result="pending",
                data={"action": action, "impact": impact},
            )
        )
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.intent failed: {e}", file=sys.stderr)


def note(
    text: str,
    by: str = "human",
) -> None:
    """Emit a manual annotation event."""
    try:
        _write_event(_build_record("note", data={"text": text, "by": by}))
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.note failed: {e}", file=sys.stderr)


def process(
    cmd: list[str],
    exit_code: int,
    duration_ms: int,
    cwd: str,
) -> None:
    """Emit a process.invoke event for a subprocess invocation.

    Analogous to ``log.api`` for HTTP round-trips: one event per subprocess
    call, with the same redaction guarantees. Stdout/stderr bodies are NOT
    logged (they may contain registry auth artefacts).

    Args:
        cmd:         Full command list including the binary (e.g. ["kamal", "deploy"]).
        exit_code:   Process return code (0 = success).
        duration_ms: Wall-clock time in milliseconds.
        cwd:         Working directory as a string.

    This function never raises (D-002 op rule 1).
    """
    try:
        event_result = "ok" if exit_code == 0 else "fail"
        data: dict[str, Any] = {
            "cmd": cmd,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "cwd": cwd,
        }
        _write_event(_build_record("process.invoke", result=event_result, data=data))
        if (s := _current_summary.get()) is not None:
            s["events_count"] += 1
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.process failed: {e}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Dry-run toggle
# ---------------------------------------------------------------------------


def file_write(
    path: str,
    bytes: int,
    **kwargs: Any,
) -> None:
    """Emit a file.write event for a local filesystem mutation.

    Used by skills that mutate project files (not state files) and
    therefore have no ``state.change`` event to emit. One event per
    touched path; byte count is the file size written.

    This function never raises (D-002 op rule 1).

    Args:
        path:   Relative path of the file written (never absolute to avoid
                leaking home-dir paths into the log).
        bytes:  Byte count of the content written.
        **kwargs: Optional extra fields (e.g. ``redacted_key`` for secrets).
    """
    try:
        data: dict[str, Any] = {"path": path, "bytes": bytes, **kwargs}
        _write_event(_build_record("file.write", data=data))
        if (s := _current_summary.get()) is not None:
            s["events_count"] += 1
    except Exception as e:  # noqa: BLE001
        print(f"warn: log.file_write failed: {e}", file=sys.stderr)


def set_project_context(
    root: Path | None,
    slug: str | None,
    stage: str | None,
    actor: str | None = "human",
    session_id: str | None = None,
) -> str:
    """Set the project ContextVars for the current context.

    Public helper for callers (e.g. CLI skill scripts) that need to emit
    events outside a ``log.session()`` context manager — such as a bare
    ``log.note()`` call — without touching private module internals.

    All parameters are optional; pass ``None`` to leave the corresponding
    ContextVar at its current value (i.e. only set what you provide).

    This function does **not** return reset tokens. The caller is
    responsible for restoring prior state if required. For well-scoped
    mutations (emit one event then exit) that is not necessary.

    Returns the session_id that was set (either the one supplied or a
    freshly-minted ULID). Callers that need to echo the id (e.g.
    ``awf-log note --json``) can capture this return value.

    Args:
        root:       Project root directory, or ``None`` to leave unchanged.
        slug:       Project slug string, or ``None`` to leave unchanged.
        stage:      Current pipeline stage string, or ``None`` to leave
                    unchanged.
        actor:      Actor identifier (default ``"human"``), or ``None`` to
                    leave unchanged.
        session_id: Explicit session ULID to stamp on subsequent events.
                    If ``None``, a fresh ULID is minted and set so that
                    the caller can capture it for JSON output (e.g.
                    ``awf-log note --json``).
    """
    if root is not None:
        _current_project_root.set(root)
    if slug is not None:
        _current_project_slug.set(slug)
    if stage is not None:
        _current_stage.set(stage)
    if actor is not None:
        _current_actor.set(actor)
    effective_sid = session_id if session_id is not None else _ulid()
    _current_session_id.set(effective_sid)
    return effective_sid


def set_dry_run(active: bool) -> None:
    """Set the process-global dry-run flag.

    When True, intent() emits events regardless of AWF_LOG_INTENTS.
    Skills that parse their own ``--dry-run`` argument call this once at
    start; testing code uses an autouse fixture to reset it after each test.
    """
    global _dry_run_active  # noqa: PLW0603 — intentional process-global flag
    _dry_run_active = active


# ------------------------------------------------------------------------------ #
# Read API
# ------------------------------------------------------------------------------ #


def read_events(path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed events from a JSONL log file; skip malformed lines with stderr warning.

    Never raises. If the file does not exist, yields nothing.
    """
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError as e:
                    print(
                        f"warn: skipping malformed log line {lineno} in {path}: {e}",
                        file=sys.stderr,
                    )
    except OSError as e:
        print(f"warn: cannot read log file {path}: {e}", file=sys.stderr)


def tail_events(path: Path, n: int) -> list[dict[str, Any]]:
    """Return the last *n* events from *path*, oldest-of-tail first.

    Uses an efficient read-from-end approach: reads the file in reverse
    chunks to find line boundaries, avoiding loading the entire file.
    Falls back to read_events on small files or when chunk reads fail.

    Returns an empty list if the file does not exist.
    """
    if not path.exists():
        return []
    if n <= 0:
        return []

    # Efficient tail: collect up to n complete JSON lines from the end.
    try:
        size = path.stat().st_size
        if size == 0:
            return []

        CHUNK = 8192
        lines: list[str] = []
        remaining = size
        buf = b""

        with open(path, "rb") as fh:
            while remaining > 0 and len(lines) <= n:
                read_size = min(CHUNK, remaining)
                remaining -= read_size
                fh.seek(remaining)
                chunk = fh.read(read_size)
                buf = chunk + buf
                # Split on newlines; leftmost segment may be incomplete
                parts = buf.split(b"\n")
                # The first part may be a fragment (no leading newline guarantee)
                # Keep it in buf for the next iteration
                buf = parts[0]
                # Remaining parts are complete lines (except possibly empty)
                for part in reversed(parts[1:]):
                    stripped = part.strip()
                    if stripped:
                        lines.append(stripped.decode("utf-8", errors="replace"))
                    if len(lines) >= n:
                        break
                if len(lines) >= n:
                    break

            # Process any remaining buf content (first line of file or final fragment)
            if remaining == 0 and buf.strip():
                lines.append(buf.strip().decode("utf-8", errors="replace"))

        # lines is in reverse order; take first n, then reverse to oldest-first
        tail_lines = lines[:n]
        tail_lines.reverse()

        result: list[dict[str, Any]] = []
        for raw in tail_lines:
            try:
                result.append(json.loads(raw))
            except json.JSONDecodeError as e:
                print(f"warn: skipping malformed tail line: {e}", file=sys.stderr)
        return result

    except OSError as e:
        print(f"warn: tail_events read failed for {path}: {e}", file=sys.stderr)
        return []


def iter_sessions(path: Path) -> Iterator[dict[str, Any]]:
    """Yield session summary objects from a sessions.jsonl index file.

    Each line is expected to be a session summary dict written by
    _append_session_index(). Skips malformed lines with stderr warning.
    """
    yield from read_events(path)


def find_session_bounds(
    events: list[dict[str, Any]], session_id: str
) -> tuple[int, int] | None:
    """Return (start_idx, end_idx) of *session_id* in the events list.

    Searches for the ``session.start`` event with the matching session field,
    then the paired ``session.end``. Returns None if the session is not found.
    If no ``session.end`` is found (in-progress session), end_idx is the last
    event index in the list.
    """
    start_idx: int | None = None
    for i, ev in enumerate(events):
        if ev.get("type") == "session.start" and ev.get("session") == session_id:
            start_idx = i
            break
    if start_idx is None:
        return None

    end_idx = len(events) - 1  # default: no session.end found
    for i in range(start_idx + 1, len(events)):
        ev = events[i]
        if ev.get("type") == "session.end" and ev.get("session") == session_id:
            end_idx = i
            break

    return (start_idx, end_idx)


def latest_session_id(path: Path) -> str | None:
    """Return the most recent ``session.start`` session id from *path*.

    Reads the file from the end for efficiency. Returns None if no
    ``session.start`` event is found.
    """
    if not path.exists():
        return None
    # Read a reasonable tail to find the latest session.start
    # Most sessions fit within 500 events; start with 200 from tail
    for batch_n in (200, 1000, 5000):
        events = tail_events(path, batch_n)
        # Scan backwards for session.start
        for ev in reversed(events):
            if ev.get("type") == "session.start":
                sid = ev.get("session")
                if sid:
                    return str(sid)
        # If we read fewer events than requested, we've seen the whole file
        if len(events) < batch_n:
            break
    return None
