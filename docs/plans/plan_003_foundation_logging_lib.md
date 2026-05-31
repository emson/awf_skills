# Plan 003 — Foundation: `lib/log.py` logging library

**Status:** ready
**Phase:** A
**Spec refs:** [`spec.md` § A3](../spec.md#a3-liblogpy-d-002), [`decisions.md` D-002](../decisions.md#d-002--logging-model), [`docs/08-logging.md`](../08-logging.md)
**Owner (current):** Lead
**Created:** 2026-05-31
**Updated:** 2026-05-31

## Goal

Deliver `lib/log.py`: the library half of the logging contract locked
by D-002 and detailed in `08-logging.md`. This plan ships the public
Python API (`session`, `invoke`, `api`, `state_change`, `gate`,
`error`, `intent`, `note`, plus the internal `safe_log` redactor) that
every subsequent skill uses to emit structured events into
`<project>/.awf/log.jsonl`, with the session-summary line appended to
`~/.config/awf/sessions.jsonl`.

It is the third foundation brick: plan 001 left `lib/state.py` calling
a not-yet-existent `lib.log.state_change`; plan 002 left the dual-walk
project locator in place; this plan makes `_emit_state_change`'s lazy
import succeed and gives every later skill a tested logging primitive.

The CLI surface (`awf-log tail / session / find / replay / sessions /
…`) is **plan 019** in Phase C, not here. This plan ships the
library only.

## Context

- Spec: [`docs/spec.md` § A3](../spec.md#a3-liblogpy-d-002) — public
  API list, behaviour summary, acceptance criteria.
- ADR: [D-002](../decisions.md#d-002--logging-model) — the locked
  logging model (project-local jsonl, central index, redaction,
  never-raises, ULID, intent gating).
- Full contract: [`docs/08-logging.md`](../08-logging.md) — event
  schema, types, redaction policy, concurrency/durability,
  operational rules. This is the load-bearing document; every design
  decision in this plan traces back to a line of `08-logging.md`.
- Plan 001 ([plan_001](plan_001_foundation_state_schema.md))
  established the `_emit_state_change` shim in `lib/state.py` that
  calls `from lib import log; log.state_change(file=str(p), key="",
  before=…, after=…)`. **This plan must keep that signature
  working** — the keyword argument order and `key=""` whole-file
  convention are the contract `state.py` already depends on.
- Plan 002 ([plan_002](plan_002_foundation_project_locator.md))
  established the dual-walk `find_project_root()` and the
  `ensure_anchor()` migration helper, both of which are used by this
  plan's `session` context manager to resolve the project root for
  log writes.
- `lib/awf_home.py` already exposes `user_config_dir()` (added in
  plan 001 / step 7a). This plan re-uses it to resolve
  `~/.config/awf/sessions.jsonl`.
- `lib/state.py` exposes `ProjectAnchor` (this plan reads
  `domain`/`slug`/`stage` from it for event header fields when a
  session is opened inside a project).
- Principles:
  - [A6 — layered config](../01-principles.md): the user-scope index
    path resolves via `lib.awf_home.user_config_dir()`, never
    hardcoded.
  - [A11 — resumability](../01-principles.md): the log is the audit
    track that lets composers replay and resume across sessions; this
    is why it must never lose events on a crash and must never break
    a skill by raising.
  - [A14 — manual gates are first-class](../01-principles.md): the
    `gate.hit` event type is the substrate that lets resumable
    composers detect when they last hit a gate; this API must be
    callable from any skill.

### Design decision — `ContextVar` is the only acceptable way to thread `session_id`

`08-logging.md` says context managers thread `session_id` "through
nested events without the caller passing it." Python's `threading.local`
is wrong here because async/await is on the table (no asyncio in the
codebase today, but a `contextvars.ContextVar` is the only mechanism
that works correctly under both threads and the future possibility of
`async with log.session(...)`). The implementation uses two
`ContextVar`s — `_current_session_id` and `_current_skill` — both
declared at module scope, both with `default=None`, both manipulated
via `ContextVar.set(...) → token → ContextVar.reset(token)` around the
context-manager body. Each `__exit__` resets via the saved token, so
re-entry across siblings is safe.

This decision is recorded here rather than as a new D-NNN because D-002
already locks "context managers that thread session_id automatically"
— this is the implementation choice, not a divergence.

### Design decision — error semantics: `safe_log` is the only swallow point

D-002 op rule 6 ("logging never raises") and `08-logging.md` ("the
helper catches `OSError`, prints a one-line warning to stderr, and
continues") both describe an outer try/except. **All public API
methods are wrappers around a single private `_write_event(record:
dict)`**. `_write_event` is the only function that performs file I/O,
and it is wrapped in `try/except OSError as e: print("warn: log
write failed: ...", file=sys.stderr); return`. The public API methods
**also** wrap their body in `try/except Exception` and convert any
unexpected exception (e.g., serialisation error) to a stderr warning
— so a malformed `args` dict from a skill never propagates. The cost
of "double swallow" is one extra try/except per public method; the
benefit is that no logging call site can ever break its caller.
This is the literal reading of "logging never raises" and matches
the test surface (`test_log_hook_failures_become_stderr_warnings`
already lives in plan 001).

### Design decision — cap implementation in `state_change`

`08-logging.md` says: "Diff size in `state.change` is capped at 2 KB
per side; larger diffs record a hash + pointer instead." The shim
in `lib/state.py` passes raw `before`/`after` dicts; the cap lives
**here**. Implementation:

1. Compute a top-level diff: `added` (keys in `after` not in `before`),
   `removed` (keys in `before` not in `after`), `changed` (keys whose
   values differ between `before` and `after`). All three are dicts
   (or, for `changed`, a dict of `{key: {"before": …, "after": …}}`).
2. Serialise `before` and `after` separately. Measure the UTF-8 byte
   length of each.
3. If `len(json.dumps(before).encode("utf-8")) <= 2048` and likewise
   for `after`, embed both verbatim in `data`.
4. Otherwise, compute `sha256(json.dumps(value, sort_keys=True).encode())`
   for the side(s) over budget; record
   `data.before_hash = "sha256:<hex>"` and
   `data.before_pointer = str(file) + "@<event_ulid>"` instead of the
   verbatim value. (The pointer is `file@event_id` so a future reader
   can locate the surrounding event; we do not materialise an
   external blob file — the design accepts that the over-cap content
   is forensically gone, which is fine because the on-disk state file
   itself remains.)
5. The top-level `diff` summary (added/removed/changed key lists,
   not values) is always recorded — it tells the reader *what*
   changed even when the *what-to* is hashed.

This cap is the only data-transforming step in the library; every
other event type embeds `data` verbatim after `safe_log` redaction.

### Design decision — `intent` gating is checked at call time, not at module load

### Design decision — `lib.log` is module-as-namespace; no `log` attribute defined inside

Callers import the module as a namespace: `from lib import log` followed
by `log.session(...)`, `log.api(...)`, etc. This matches the existing
call site in `lib/state.py:_emit_state_change`
(`from lib import log; log.state_change(...)`). **No `log` attribute is
defined anywhere inside `lib/log.py`** — no module-level logger object,
no re-export named `log`. The `from lib.log import log` form shown
informally in `08-logging.md` happens to work in Python (it yields the
module object itself, because Python falls back to the parent module's
attribute lookup of the same name) but is an unusual idiom; the module
docstring notes that the canonical import is `from lib import log` and
that any other form is at the caller's risk. The functions and context
managers (`session`, `invoke`, `api`, `state_change`, `gate`, `error`,
`intent`, `note`, `safe_log`, `set_dry_run`) live directly at module
scope and are accessed via the module name.

### Design decision — `_current_summary` guard idiom

Every helper that mutates the per-session summary uses the same
sentinel check against `_current_summary`, not against
`_current_session_id`. The reason: `_build_record` auto-mints a
session ULID for atomic skills called outside any composer (op rule
3), so `_current_session_id` may be non-None inside the builder while
no real session is open. Only `_current_summary` is set by the
`session()` context manager and reset on exit, so it is the correct
sentinel for "are we inside a session right now?"

Canonical pattern (used in `api`, `gate`, `error`, and the
`session.start`/`session.end` accounting):

```python
summary = _current_summary.get()
if summary is not None:
    summary["events_count"] += 1   # or gates_hit, etc.
```

Outside a session, `_current_summary.get()` returns `None` and the
helpers no-op the summary update. The event itself **still emits** —
the summary update is purely accounting for the eventual `session.end`
record. Tests verify that `api()`, `gate()`, and `error()` called
outside any session do not raise.

### Design decision — `intent` gating is checked at call time, not at module load

`08-logging.md` Q3: "intent events fire only with `--dry-run`. Opt-in
via `AWF_LOG_INTENTS=1`." Implementation: `log.intent(...)` checks at
call time whether `os.environ.get("AWF_LOG_INTENTS") == "1"` **or** a
process-global flag `_dry_run_active` is True. The flag is set by an
internal helper `set_dry_run(active: bool)` callable from a skill that
parsed `--dry-run` from its own args. (This avoids `lib.log` having to
parse anyone else's argv.) When neither condition holds, `intent`
returns silently — no event, no warning. The check happens at every
`intent()` call so that mid-skill toggling works.

### Design decision — ULID inline (~25 lines)

D-002 Q4: ULID, inline, no new dep. Algorithm: 48-bit ms timestamp +
80-bit randomness, encoded in Crockford base32. Lexicographic sort
order matches creation order at millisecond resolution. Implemented
as a single `_ulid() -> str` function using `time.time_ns()` and
`secrets.token_bytes(10)`. Output is `26` characters, all uppercase,
matching the ULID spec. We do **not** import a `python-ulid`
dependency.

### Design decision — atomic 4 KB threshold check

`08-logging.md`: "Atomic append is guaranteed by POSIX `O_APPEND`
for writes under `PIPE_BUF` (4 KB). … For events that exceed 4 KB
… `lib/log.py` switches to `fcntl.flock`." Implementation: serialise
the event with a trailing `\n` and a UTF-8 encode; measure
`len(line_bytes)`. If `<= 4096`, write via
`os.write(fd, line_bytes)` against a file opened with
`os.open(path, O_WRONLY | O_APPEND | O_CREAT, 0o644)`. If `> 4096`,
open via `open(path, "ab")`, take an `fcntl.flock(fd, LOCK_EX)`, write,
release. This is the only file-write path; both branches go through
`_write_event`.

### Design decision — `session.end` central-index write

`08-logging.md` and D-002: "each `session.end` writes one summary
line to `~/.config/awf/sessions.jsonl`." Implementation: at session
exit, in addition to the normal `session.end` event written to the
project log, also append a one-line summary JSON object to
`user_config_dir() / "sessions.jsonl"`. The summary line carries
`session_id`, `project_slug`, `project_path`, `composer`, `target`,
`started_at`, `ended_at`, `events_count`, `gates_hit`, `result`.
Same atomic-append discipline (write under 4 KB; flock above). If
the central index write fails, it is a stderr warning — the
project-local log already succeeded, so the session is still
forensically recoverable.

## Out of scope

- **The `awf-log` skill** (CLI surface: `tail`, `session`, `find`,
  `replay`, `diff`, `note`, `sessions`). That is plan 019 in Phase C
  per `multi_agent_prompt.md` table. This plan ships the library
  only.
- **Retrofitting existing skills** in `skills/` to call the new
  logging API. D-002 op rule "Existing S1 skills will be retrofitted
  opportunistically, not urgently" governs; retrofits happen one at
  a time in the plan that touches each skill. This plan does not
  touch any file under `skills/`.
- **Log rotation / archival.** `08-logging.md`: deferred until
  >10 MB. Not implemented.
- **Remote log shipping.** Deferred per `08-logging.md`.
- **`awf-status` integration** (printing last-5 events at top of
  status output). That is plan 020 in Phase C.
- **Trace-tree visualisation / cost aggregation.** Deferred per
  `08-logging.md`.
- **Schema migrations** beyond "tolerate extra keys." The event
  schema is forward-compatible by adding fields; readers ignore
  unknown fields. No migration tooling required at this stage.
- **`state.py` changes.** The existing `_emit_state_change` shim
  already calls the canonical signature this plan delivers; nothing
  in `lib/state.py` needs to change. Tests added by this plan
  exercise the integration **through** the shim, not by editing it.

## Dependencies

- **Plan 001 (`lib/state.py`)** — accepted. The shim in
  `_emit_state_change` already calls
  `log.state_change(file=str(p), key="", before=…, after=…)`. This
  plan's `state_change` function **must** accept exactly that
  keyword-argument signature, or the existing 21 tests in
  `tests/lib/test_state.py` will fail (specifically
  `test_save_emits_state_change_via_log_hook` and
  `test_save_when_log_unavailable_does_not_raise`).
- **Plan 002 (`lib/project.py` dual-walk + `ensure_anchor`)** —
  accepted. This plan's `session` context manager calls
  `find_project_root(optional=True)` to populate the `project` and
  `stage` event header fields when invoked inside a project. Outside
  any project (e.g., `awf-doctor` run from `/tmp`), header fields
  fall back to empty strings.
- **External / user prerequisites:** none. No new credentials, no
  new env vars (the optional `AWF_LOG_INTENTS=1` is gated opt-in).
  No new package dependencies — ULID is implemented inline.
- **Code prerequisites:** `lib/awf_home.py:user_config_dir()`
  (exists from plan 001 step 7a), `lib/project.py:find_project_root`
  (exists, dual-walk via plan 002), `lib/state.py:ProjectAnchor`
  (exists from plan 001).

## Implementation steps

Each step is small enough to verify independently. Steps 1–11 are
the `lib/log.py` body; step 12 is the test surface; step 13 is the
final lint/type gate.

1. **Module skeleton.** Create `lib/log.py` with a module docstring
   that references D-002 and `08-logging.md`, lists the public API,
   and states the design decisions logged above (ContextVar threading,
   `safe_log` denylist, ULID inline, 4 KB threshold, intent gating,
   cap behaviour). Declare module-level constants:

   ```python
   LOG_DIRNAME = ".awf"
   LOG_FILENAME = "log.jsonl"
   SESSIONS_INDEX_FILENAME = "sessions.jsonl"
   ATOMIC_APPEND_THRESHOLD = 4096    # bytes (POSIX PIPE_BUF)
   STATE_CHANGE_SIDE_CAP = 2048      # bytes per side, before cap-and-hash
   REDACTION_PLACEHOLDER = "***"
   ```

   Module imports are stdlib-only: `json`, `os`, `re`, `sys`,
   `time`, `secrets`, `hashlib`, `fcntl`, `contextvars`, `pathlib`,
   `typing`, `contextlib` (for `contextmanager`), `datetime`.

2. **ULID implementation.** Implement `_ulid() -> str` per Crockford
   base32 ULID spec:
   - 48-bit timestamp from `time.time_ns() // 1_000_000`.
   - 80-bit randomness from `secrets.token_bytes(10)`.
   - Encode as 26 characters using
     `"0123456789ABCDEFGHJKMNPQRSTVWXYZ"` (Crockford base32, omits
     I/L/O/U).
   - Verify by unit test that two consecutive calls produce
     lexicographically ordered strings (sortability is the property
     under test, not the exact value).

3. **`safe_log` redaction.** Implement
   `safe_log(value: Any) -> Any` recursively:
   - For dicts: walk keys; if key matches the **denylist regex**,
     replace value with `"***"`; else recurse into value.
   - For lists/tuples: recurse element-wise.
   - For strings: apply the **bearer/prefix scrubber regex**
     (`Bearer\s+\S+`, `\bsk-[A-Za-z0-9_-]{10,}\b`,
     `\bpat-[A-Za-z0-9_-]{10,}\b`, `\bhk-[A-Za-z0-9_-]{10,}\b`,
     `\btok-[A-Za-z0-9_-]{10,}\b`) → replace match with `"***"`.
   - Other types: return unchanged.

   **Denylist regex for keys** (case-insensitive):
   ```python
   _KEY_DENYLIST_RE = re.compile(
       r"^(authorization|.*_token|.*_secret|.*_password|.*_key)$",
       re.IGNORECASE,
   )
   _KEY_ALLOWLIST_SUFFIXES = ("_key_id",)  # _KEY_ID is safe; it's an identifier, not a secret
   ```
   The allowlist is checked *first*: if a key ends in any allowlist
   suffix (case-insensitive), it bypasses the denylist match.

   The two regexes (`_KEY_DENYLIST_RE`, the bearer/prefix
   `_VALUE_SCRUB_RE`) are module-level constants, compiled once.
   Adding a new pattern is a one-line edit, per `08-logging.md`.

4. **Event-record builder.** Implement
   `_build_record(type_: str, *, skill: str | None = None,
   result: str | None = None, duration_ms: int | None = None,
   data: dict | None = None) -> dict`:
   - `ts`: `datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")`.
   - `session`: `_current_session_id.get() or _ulid()` (atomic
     skills called outside any composer auto-mint a one-event
     session ID; this matches `08-logging.md` op rule 3).
   - `project`: from `_current_project_slug.get()` or `""`.
   - `stage`: from `_current_stage.get()` or `""`.
   - `actor`: from `_current_actor.get()` or `"claude-code"` (a
     fourth ContextVar; defaulted to `claude-code`, overridable by
     a future `awf-log note --by …`).
   - `type`: as passed.
   - `skill`: `skill` or `_current_skill.get()` (whichever non-None;
     omit the key entirely if both are None).
   - `result`: included only if not None.
   - `duration_ms`: included only if not None.
   - `data`: `safe_log(data or {})`.

   The function returns a dict ready to JSON-serialise; it never
   raises (a builder bug would be a stderr warning at the
   `_write_event` boundary, not a caller-visible exception).

5. **`_write_event` (the only I/O sink).** Implement
   `_write_event(record: dict) -> None`:
   - Resolve target path: `_current_project_root.get()` (a fifth
     ContextVar) ` / LOG_DIRNAME / LOG_FILENAME`. If
     `_current_project_root` is None (call from outside any
     project), fall back to `user_config_dir() / "orphan-log.jsonl"`
     — orphan events are still recorded but at the user scope.
     This is a graceful degradation, not a feature; the warning
     stays silent (the user explicitly ran outside any project).
   - Ensure parent directory exists (`path.parent.mkdir(parents=True,
     exist_ok=True)`).
   - Serialise: `line_bytes = (json.dumps(record, separators=(",", ":"),
     ensure_ascii=False) + "\n").encode("utf-8")`.
   - **Branch on size:**
     - `len(line_bytes) <= ATOMIC_APPEND_THRESHOLD`:
       `fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644);`
       `os.write(fd, line_bytes); os.close(fd)`.
     - else: open with `open(path, "ab")`, take `fcntl.flock(LOCK_EX)`,
       write, flush, release lock, close (use a `try/finally` to
       release the lock on any error path).
   - Wrap the entire body in `try / except OSError as e`: print
     `"warn: log write failed: {e}"` to stderr and return.
   - Wrap a second outer `try / except Exception as e`: same
     stderr warning; covers any serialisation surprise. This
     enforces "logging never raises."

6. **ContextVars at module scope.** Declare:

   ```python
   _current_session_id: ContextVar[str | None] = ContextVar("awf_log_session_id", default=None)
   _current_project_slug: ContextVar[str] = ContextVar("awf_log_project_slug", default="")
   _current_project_root: ContextVar[Path | None] = ContextVar("awf_log_project_root", default=None)
   _current_stage: ContextVar[str] = ContextVar("awf_log_stage", default="")
   _current_skill: ContextVar[str | None] = ContextVar("awf_log_skill", default=None)
   _current_actor: ContextVar[str] = ContextVar("awf_log_actor", default="claude-code")
   _current_summary: ContextVar[dict | None] = ContextVar("awf_log_summary", default=None)
   ```

   That is **seven** ContextVars total. `_current_summary` is the
   per-session accounting bucket: `session()` sets it on entry to
   `{"events_count": 0, "gates_hit": 0, "result": "ok"}`, mutators
   (`gate`, `error`, `api`, etc.) update it via the guard idiom
   above, and `session.end` reads it to build the summary payload.
   Outside a session it stays `None` and the mutators no-op (see
   the `_current_summary` guard idiom design decision above).

   Each context manager sets only the vars it owns (`session` sets
   `session_id`, `project_slug`, `project_root`, `stage`, `summary`;
   `invoke` sets `skill`) and resets them in `finally` via the token
   returned by `set()`. **No global mutable state outside ContextVars**
   apart from the documented `_dry_run_active` flag (step 11) — they
   are otherwise the entire state surface.

7. **`session` context manager.** Implement
   `@contextmanager def session(composer: str, target: str, *,
   start: Path | None = None) -> Iterator[str]`:
   1. Mint `sid = _ulid()`.
   2. Resolve project context optimistically:
      ```python
      try:
          from lib.project import find_project_root
          root = find_project_root(start, optional=True)
      except Exception:
          root = None
      ```
      If `root` is not None: try to load `ProjectAnchor.load(start=root)`
      to get `slug` and `stage`. **Any exception here is swallowed**
      (project anchor may be malformed; we still want to log). On
      failure, `slug = ""`, `stage = ""`.
   3. `set(...)` the five ContextVars (`session_id`, `project_slug`,
      `project_root`, `stage`, and implicitly leave `skill = None`).
      Capture all returned tokens.
   4. Record `t_start = time.monotonic()` and
      `started_at = datetime.now(timezone.utc).isoformat(...)`.
   5. Initialise per-session counters in a local dict
      `summary = {"events_count": 0, "gates_hit": 0, "result": "ok"}`
      and stash it on the seventh ContextVar `_current_summary`
      (declared in step 6) so sibling events (`gate`, `error`, `api`)
      can mutate it via the guard idiom. Token saved for `finally`
      reset.
   6. Emit `session.start` with `data = {"composer": composer,
      "target_stage": target, "source_stage": stage}`.
   7. `try: yield sid; except Exception as e: summary["result"] = "fail";
      raise` — re-raise after marking the session failed.
   8. `finally:` compute `duration_ms = int((time.monotonic() - t_start) * 1000)`,
      ended_at, emit `session.end` event with
      `data = {"events_count": summary["events_count"],
      "gates_hit": summary["gates_hit"],
      "summary": summary["result"]}` and `duration_ms`. Then call
      `_append_session_index(summary, sid, composer, target,
      started_at, ended_at)` (step 8). Then reset all tokens.

   The yielded value is the session ULID — callers rarely use it,
   but tests assert it.

8. **Central session-index append.** Implement
   `_append_session_index(summary: dict, sid: str, composer: str,
   target: str, started_at: str, ended_at: str) -> None`:
   - Build one line: `{"session_id": sid, "project_slug": slug,
     "project_path": str(root) or "", "composer": composer,
     "target": target, "started_at": started_at, "ended_at":
     ended_at, "events_count": summary["events_count"], "gates_hit":
     summary["gates_hit"], "result": summary["result"]}`.
   - Append to `user_config_dir() / SESSIONS_INDEX_FILENAME` using
     the same size-branched logic as `_write_event` (atomic under
     4 KB; flock above). Wrap in `try/except` → stderr warning;
     never propagate.

9. **`invoke` context manager.** Implement
   `@contextmanager def invoke(skill: str, args: dict | None = None,
   *, result: str | None = None) -> Iterator[None]`:
   - `set(_current_skill, skill)` → token.
   - Emit `skill.invoke` with `data = {"args": safe_log(args or {})}`
     and `result="pending"`.
   - `t_start = time.monotonic()`.
   - `try: yield; except Exception: emit_complete("fail"); raise;
     else: emit_complete("ok")`; where `emit_complete(r)` writes
     `skill.complete` with `duration_ms` and `result = r`. Mirror
     the args in `data` so a single search returns invoke+complete
     pairs.
   - `finally: _current_skill.reset(token)`.

10. **One-shot event helpers.** Implement, each a one-line wrapper
    around `_write_event(_build_record(...))`:
    - `api(provider: str, method: str, path: str, status_code: int,
      resource_id: str | None = None) -> None`. **The keyword
      parameter is named `status_code`** — matching `spec.md § A3`
      exactly. The on-disk event `data` field is also named
      `status_code` (not `status`). The informal example in
      `08-logging.md` that uses `status=...` is a doc error; this
      plan does **not** rename the parameter to `status` and does
      **not** add a `status` alias. Step 13 (lint/type gate) flags
      a follow-up doc fix to `08-logging.md` as a separate, minor
      task — out of scope for this plan's code changes but called
      out so a future reader does not call `log.api(..., status=...)`
      and hit `TypeError`. Event type `"api.call"`, `data` includes
      `provider`, `method`, `path`, `status_code`, and
      `resource_id` (only if non-None). Status
      `result = "ok" if status_code < 400 else "fail"`. Increment
      `_current_summary["events_count"]` via the guard idiom (no-op
      if outside a session).
    - `state_change(file: str, key: str, before: dict, after: dict) -> None`.
      Event type `"state.change"`. Apply the cap-and-hash logic
      from the "cap implementation" design decision. **The signature
      and keyword-arg ordering exactly matches what
      `lib/state.py:_emit_state_change` calls** — this is the
      backward-compat contract with plan 001.
    - `gate(name: str, reason: str, instructions: str = "") -> None`.
      Event type `"gate.hit"`, `result = "gate"`. Increment
      `_current_summary["gates_hit"]` via the guard idiom (no-op
      if outside a session).
    - `error(msg: str, hint: str | None = None) -> None`. Event
      type `"error"`, `result = "fail"`. **No stack trace by
      default** (per `08-logging.md` event-type table). Mark
      `_current_summary["result"] = "fail"` via the guard idiom
      (no-op if outside a session).
    - `intent(action: str, impact: str) -> None`. Event type
      `"intent"`, `result = "pending"`. **Gated** by
      `os.environ.get("AWF_LOG_INTENTS") == "1"` OR
      `_dry_run_active`. If neither, return silently.
    - `note(text: str, by: str = "human") -> None`. Event type
      `"note"`. No skill scope; just the annotation.

11. **Dry-run toggle.** Implement
    `set_dry_run(active: bool) -> None`: sets a module-level
    `_dry_run_active` flag. Skills that parse their own `--dry-run`
    argument call this once at start. The flag is process-global
    (not a ContextVar) because dry-run is a process-wide mode in
    the existing skills.

    **Note:** this is the only piece of module-level mutable state
    in `lib/log.py` outside of ContextVars. It is justified by
    "dry-run is a CLI mode, not a per-call attribute," and tests
    cover the flag round-trip explicitly.

12. **Test surface** (`tests/lib/test_log.py`, one file). See
    "Tests required" below for the test list. Use real temp
    directories (`tmp_path` fixture) for project roots and the
    central index. Real files, real JSON, real `fcntl.flock`. For
    the concurrency test, use `multiprocessing.Pool` (matches the
    spec acceptance criterion "1000 concurrent appends produce 1000
    valid JSON lines"). For the central-index path, monkeypatch
    `lib.awf_home.user_config_dir` to return a `tmp_path` subdir
    (same pattern as plan 001's `Shared` tests).

    **Important** (lesson from plan 001 N3): some tests will need
    to access `_current_session_id` to confirm reset on exit;
    consider this acceptable test-internal access (same status as
    `_path` in plan 001's tests) and document the deviation at the
    test site.

13. **Lint + type gate.** Dev runs and confirms green:
    - `ruff check lib/log.py tests/lib/test_log.py`
    - `mypy --strict lib/log.py`
    - `pytest tests/lib/test_log.py -q` (all tests pass)
    - `pytest tests/lib/test_state.py -q` (21 existing tests still pass;
      this is the regression guard for the plan 001 contract)

## Acceptance criteria

Copied verbatim from `spec.md § A3`, plus plan-specific additions
flagged `[plan]`.

**From `spec.md § A3`:**

- [ ] 1000 concurrent appends produce 1000 valid JSON lines, no
      interleaving.
- [ ] Bearer token in `args` is redacted to `***`.
- [ ] Read-only file → stderr warning, no exception raised.
- [ ] Session context manager auto-emits `session.start` /
      `session.end` with `duration_ms`.
- [ ] Central index gains one line per completed session.

**Plan-specific:**

- [ ] `[plan]` `state_change(file, key, before, after)` accepts the
      exact keyword-arg signature called by
      `lib/state.py:_emit_state_change` (regression guard: plan 001's
      21 state tests still pass with `lib.log` importable).
- [ ] `[plan]` Session ID is threaded via `ContextVar`: a nested
      `invoke` reads the parent session's ID without the caller
      passing it; on session exit the ContextVar resets to its
      previous value (verified by token-based reset, not by overwrite).
- [ ] `[plan]` Session IDs are 26-character Crockford base32 ULIDs.
      Sortability is verified by the narrow property "two ULIDs
      generated at least 1ms apart sort with the earlier one first"
      — not by bulk concurrent ordering, which is luck-dependent.
- [ ] `[plan]` Events ≤ 4 KB are written via `O_APPEND` (no flock);
      events > 4 KB take an `fcntl.flock` exclusive lock before
      writing. Both paths emit valid JSON-lines on disk.
- [ ] `[plan]` `safe_log` redacts: `Authorization`, `*_TOKEN`,
      `*_SECRET`, `*_PASSWORD`, `*_KEY` (except `*_KEY_ID`) keys;
      bearer tokens and `sk-`/`pat-`/`hk-`/`tok-`-prefixed strings
      in any string value.
- [ ] `[plan]` `intent` is silent (no event emitted) by default;
      emits an event when `AWF_LOG_INTENTS=1` is set OR
      `set_dry_run(True)` is called.
- [ ] `[plan]` `state_change` caps embedded `before`/`after` at
      2 KB each; oversize sides record `before_hash` /
      `before_pointer` (or after-equivalents) instead.
- [ ] `[plan]` `error` event marks the surrounding session's
      `result` as `"fail"` in the `session.end` summary (if a
      session is active).
- [ ] `[plan]` `gate.hit` event increments the session's
      `gates_hit` counter in the `session.end` summary.
- [ ] `[plan]` Logging never raises: any `OSError` during write
      becomes a single-line stderr warning; the caller's flow
      continues unaffected (verified by read-only-file test).
- [ ] `[plan]` `mypy --strict lib/log.py` passes; `ruff check
      lib/log.py` passes.

## Tests required

File: `tests/lib/test_log.py`. Aligned with
[`docs/testing_principles.md`](../testing_principles.md) — behaviour
over implementation, real files via `tmp_path`, integration over
mocks, no tautological tests.

**Event-round-trip and JSON validity**

- `test_session_writes_start_and_end_events` — open a session,
  verify `.awf/log.jsonl` contains exactly two events with types
  `session.start` and `session.end`, both with the same `session`
  ID, the `end` event carrying `duration_ms >= 0`. Maps to spec
  AC4.
- `test_invoke_writes_invoke_and_complete_events` — within a
  session, run an `invoke` block, assert two events
  (`skill.invoke`/`skill.complete`) with matching `skill` and
  `session` IDs.
- `test_event_is_valid_jsonl` — after a sequence of events,
  read the file line-by-line, `json.loads` each line, assert no
  errors.

**ContextVar threading**

- `test_session_id_inherited_by_nested_invoke` — within
  `with log.session(...) as sid: with log.invoke(...): log.api(...)`,
  assert all three events share the same `session` field.
- `test_context_var_reset_after_session_exit` — open and close a
  session; read `lib.log._current_session_id.get()`; assert it is
  None again (token-based reset semantics).
- `test_atomic_skill_outside_session_mints_one_event_session` —
  call `log.api(...)` with no surrounding session; the emitted
  event has a non-empty `session` ULID. (Op rule 3.)
- `test_api_call_outside_invoke_has_no_skill_key` — call
  `log.api(...)` (or `log.note(...)` / `log.error(...)`) outside
  any `invoke()` context; read the on-disk event; assert
  `"skill" not in event` (the key is **absent**, not present as
  `null`). This guards the `_build_record` rule from step 4
  ("omit the key entirely if both skill and `_current_skill.get()`
  are None"). (N3 from Pass 1.)
- `test_summary_mutators_outside_session_do_not_raise` — outside
  any `session()` context, call `log.api(...)`, `log.gate(...)`,
  and `log.error(...)` in sequence; assert no exception; assert
  each event is on disk. Guards the `_current_summary is None`
  branch of the guard idiom (M2 from Pass 1).

**ULID**

- `test_ulid_format_is_26_crockford_base32` — call `_ulid()`,
  assert `len == 26` and `all(c in CROCKFORD_ALPHABET for c in
  ulid)`.
- `test_ulid_generated_1ms_apart_sorts_earlier_first` — generate
  one ULID, `time.sleep(0.005)` (5ms — comfortably above the 1ms
  resolution boundary on any reasonable host), generate a second
  ULID, assert `ulid_a < ulid_b`. **Do not** test ordering of
  bulk/concurrent generations — that is a luck-dependent assertion
  on loaded CI and is explicitly out of scope (N1 from Pass 1).

**Concurrency / durability** (spec AC1)

- `test_1000_concurrent_writes_produce_1000_valid_lines` — use
  `multiprocessing.Pool(8)` to call a worker that emits 125
  events each; assert final `.awf/log.jsonl` line count is 1000;
  assert every line is valid JSON; assert each event's `session`
  field appears in the expected per-worker session ID set.
- `test_oversize_event_uses_flock` — emit an event with a 5 KB
  `data` payload; assert the on-disk line is the expected size
  (>4 KB); assert no interleaving when emitted alongside
  concurrent small writes (combined sub-test).

**Redaction** (spec AC2)

- `test_safe_log_redacts_authorization_header` — input
  `{"Authorization": "Bearer xyz"}`, output
  `{"Authorization": "***"}`.
- `test_safe_log_redacts_token_suffix_keys` — keys like
  `API_TOKEN`, `cloudflare_token`, `MY_SECRET` redact to `"***"`;
  key `API_KEY_ID` is preserved.
- `test_safe_log_redacts_bearer_in_string_value` —
  `{"args": "curl -H 'Authorization: Bearer sk-abc123'"}` → the
  bearer substring is replaced with `***`.
- `test_safe_log_redacts_sk_pat_hk_tok_prefixed_strings` — verify
  each of the four prefixes is scrubbed from string values.
- `test_invoke_args_are_redacted_on_disk` — call
  `log.invoke("foo", args={"token": "sk-abc..."})`; read the
  on-disk event; assert the on-disk `data.args.token` is `"***"`.

**Best-effort / never-raises** (spec AC3)

- `test_log_write_to_readonly_file_warns_and_does_not_raise` —
  pre-create `.awf/log.jsonl`, chmod `0o400`, call any log
  function; assert no exception; assert
  `capsys.readouterr().err` contains `"warn:"`.
- `test_safe_log_on_unknown_type_returns_unchanged` —
  `safe_log(MyArbitraryObject())` does not raise; returns the
  object unchanged.

**Central index** (spec AC5)

- `test_session_end_appends_to_central_index` — monkeypatch
  `lib.awf_home.user_config_dir` to `tmp_path / "user-awf"`;
  open and close a session; assert
  `tmp_path/"user-awf"/"sessions.jsonl"` exists; assert it
  contains exactly one JSON line; assert the line carries the
  session_id, composer, target, events_count, gates_hit.
- `test_two_sessions_append_two_index_lines` — same setup; open
  and close two sessions; assert two lines.

**`state_change` cap behaviour**

- `test_state_change_embeds_small_diff_verbatim` — `before={}`,
  `after={"foo": "bar"}`; assert on-disk `data.before` and
  `data.after` are the full dicts; assert `data.diff` carries
  `added=["foo"]`.
- `test_state_change_caps_oversize_side_with_hash` — `before=`
  large dict (>2 KB serialised), `after={}`; assert on-disk
  `data.before_hash` starts with `"sha256:"`; assert
  `data.before_pointer` is non-empty; assert `data.before` is
  absent.
- `test_state_change_diff_summary_always_present` — even when
  one side is hashed, `data.diff.added` / `removed` / `changed`
  (key lists, not values) are present.

**Backwards compat with plan 001 (regression guard)**

- `test_state_py_shim_emits_through_log` — install
  `lib.log` (i.e., this module is now importable), call
  `ProjectAnchor.save()` on a populated anchor; read the
  project's `.awf/log.jsonl`; assert one `state.change` event
  with `data.file == str(path)`, `data.key == ""`, and `before` /
  `after` populated. **This is the contract guard with plan 001 —
  if it fails, the signature has drifted.**
- `test_state_py_existing_tests_still_pass` — meta-test: simply
  documents in a comment that `tests/lib/test_state.py` must
  continue to pass with no edits when `lib/log.py` is present.
  Verified by `pytest tests/lib/test_state.py -q` exit code 0 in
  Dev's final lint/type gate.

**`intent` gating**

- `test_intent_silent_by_default` — call `log.intent(...)` with
  no env var, no dry-run flag; assert no event on disk; assert
  no stderr warning.
- `test_intent_emits_under_env_var` — `monkeypatch.setenv(
  "AWF_LOG_INTENTS", "1")`; call `intent`; assert one event of
  type `"intent"` on disk.
- `test_intent_emits_under_set_dry_run` — `log.set_dry_run(True)`;
  call `intent`; assert one event. **Teardown pattern (locked):**
  use either a `try/finally` block in the test body or a pytest
  `autouse` fixture that always resets the flag:

  ```python
  @pytest.fixture(autouse=True)
  def _reset_dry_run() -> Iterator[None]:
      yield
      log.set_dry_run(False)
  ```

  A bare `log.set_dry_run(False)` after the assertion is **not
  acceptable** — if the assertion fails, the global stays True
  and contaminates subsequent tests. Any other test that mutates
  a module-level global (`set_dry_run`, monkeypatched
  `user_config_dir`, etc.) follows the same `try/finally` or
  `autouse`-fixture discipline. (N2 from Pass 1.)

**`error` and `gate` interaction with session summary**

- `test_error_marks_session_result_fail` — within a session,
  call `log.error("boom")`; on exit, assert the on-disk
  `session.end` event has `data.summary == "fail"`.
- `test_gate_increments_gates_hit_counter` — within a session,
  call `log.gate("foo", "bar")` twice; on exit, assert
  `data.gates_hit == 2`.

**Outside-project behaviour**

- `test_session_outside_project_falls_back_to_orphan_log` — open
  a session from `tmp_path` (no `.awf/project.json` anywhere);
  assert events land at
  `user_config_dir()/"orphan-log.jsonl"`; assert no exception.

**Test hygiene** (lessons from plans 001/002)

- All tests use `tmp_path` and `monkeypatch`; no
  `unittest.mock` of filesystem APIs.
- No test asserts the literal source text of `log.py` (no
  re-assertions of literals).
- Tests do **not** depend on global ContextVar leakage between
  tests — every test that sets a ContextVar uses
  `_current_*.set()` + `reset(token)` or runs the work inside a
  fresh `session` context.
- Type annotations on every test fixture and helper (plan 001
  Pass-1 lesson: type-annotation gaps in tests cause
  `mypy --strict` to surface them later).

## Status log

- 2026-05-31  Lead — created (draft).
- 2026-05-31  Reviewer — Pass 1, changes-requested (2 blockers, 2 major, 3 minor).
- 2026-05-31  Lead — revised per Pass 1 feedback: B1, B2, M1, M2 resolved; N1-N3 applied.
- 2026-05-31  Reviewer — plan review pass 2: ready. All six Pass 1 issues resolved; safe for Dev.

## Review

### Pass 1 (2026-05-31)

**Lead-flagged tensions:**

- **T1 — Orphan-log at `user_config_dir()/"orphan-log.jsonl"`:** Accept. The fallback is bounded, resolves through the established `user_config_dir()` helper (not a hardcoded string), is documented as degradation not feature, and is covered by `test_session_outside_project_falls_back_to_orphan_log`. No concern.

- **T2 — `_dry_run_active` as module-global:** Accept with minor note (see N2 below on test teardown). The plan's justification ("dry-run is a CLI mode, not a per-call attribute") is sound; asyncio is not in the codebase; the flag is process-wide. The test teardown note is acknowledged in the plan itself. Acceptable provided the teardown uses a `try/finally` guard, not a bare `log.set_dry_run(False)` at end-of-test.

- **T3 — Cap-pointer `<file>@<event_ulid>` self-referential, no external blob:** Accept. The plan is honest: over-cap content is forensically gone; the pointer is a locator hint only. The design accepts this explicitly. The cap behaviour is bounded, tested, and consistent with `08-logging.md`'s description.

- **T4 — `state.change` includes `diff` summary in addition to `before`/`after`:** Accept. `08-logging.md` event-type table says `data` carries "file, key, before, after" — the plan adds `diff` (key lists only, not values). This is additive; D-002 states "schemas tolerate extra keys." The diff summary increases utility without breaking schema compatibility. Legitimate plan-level extension.

- **T5 — `session.end` `summary` is string `"ok"|"fail"` not structured object:** Accept. `08-logging.md` specifies `data` for `session.end` carries `events_count, gates_hit, summary` but does not constrain the type of `summary`. The string `"ok"|"fail"` aligns with the `result` field values defined in the event schema header. Internally consistent.

---

**Blockers:**

- **B1 — `api()` parameter name conflicts between `spec.md § A3` and `08-logging.md` usage example.** `spec.md § A3` defines the public API as `log.api(provider, method, path, status_code, resource_id)` — parameter named `status_code`. `08-logging.md` line 141 shows `log.api(..., path="/servers", status=201, ...)` — parameter named `status`. The plan correctly adopts `status_code` (matching spec) in step 10 and the acceptance criteria. But any Dev (or future skill author) who reads the `08-logging.md` usage example will call `log.api(..., status=201, ...)` and receive a `TypeError` at runtime. The plan must either (a) note that the `08-logging.md` example contains a doc error and the correct keyword is `status_code`, and commit to fixing `08-logging.md` as part of this plan's final step 13 deliverable, or (b) change the parameter name to `status` and note the deviation from spec wording. Without resolution, the public API surface is ambiguous at a call site that every Phase B skill will use.

- **B2 — `_current_summary` is missing from the step 6 ContextVar declaration block.** Step 6 declares exactly six ContextVars (`_current_session_id`, `_current_project_slug`, `_current_project_root`, `_current_stage`, `_current_skill`, `_current_actor`) and states "No global mutable state outside ContextVars — they are the entire state surface." Step 7 then introduces "a sixth ContextVar `_current_summary`" — which is in fact a seventh, and is entirely absent from step 6's code block. Step 10 helpers (`gate`, `error`, `api`) mutate `_current_summary` conditionally ("if in a session"). A Dev following step 6 verbatim will not declare `_current_summary`; every `_current_summary.get()` call in steps 7 and 10 will raise `NameError`. The step 6 code block must add `_current_summary: ContextVar[dict | None] = ContextVar("awf_log_summary", default=None)`, and "sixth" in step 7 must be corrected to "seventh."

**Major:**

- **M1 — Import surface mismatch: module-level functions vs `log` namespace object.** `08-logging.md` shows `from lib.log import log` followed by `log.session(...)`, `log.invoke(...)`, etc. — this implies `log` is a re-exported namespace object inside `lib/log.py`. `lib/state.py` uses `from lib import log; log.state_change(...)` — treating the module itself as the namespace. Both patterns work in Python (the module is an object), but the `from lib.log import log` idiom in `08-logging.md` will produce the module itself only if no `log` name is defined inside `lib/log.py`. If a Dev writes `log = ...` anywhere in `lib/log.py` (e.g., a logger object), it breaks `from lib import log; log.state_change(...)`. The plan never addresses this. It must explicitly state: "no `log` attribute is defined inside `lib/log.py`; callers use `from lib import log` (module-as-namespace). The `from lib.log import log` form in `08-logging.md` is equivalent in Python (yields the module) but is an unusual idiom — it will be noted as acceptable in the module docstring."

- **M2 — `_current_summary` guard idiom unspecified in steps 10 and 7.** Step 10 says helpers increment `_current_summary["events_count"]` "if in a session." The guard idiom is never locked. A Dev checking `_current_session_id.get() is not None` (the obvious proxy) instead of `_current_summary.get() is not None` will fail when a one-shot event fires outside any session (session_id is auto-minted in `_build_record`, so `_current_session_id.get()` is not the right sentinel). The plan must specify: the guard is `if (s := _current_summary.get()) is not None: s["events_count"] += 1` (or equivalent). A test must verify that `gate()`, `error()`, and `api()` called outside any session context do not raise `TypeError` or `AttributeError`.

**Minor / nits:**

- **N1 — `test_ulid_lexicographic_order_matches_time_order` is fragile under load.** The plan specifies `time.sleep(0.001)` between 100 calls (100 ms total). A ULID's timestamp component has 1ms resolution; `time.sleep(0.001)` can deliver sub-1ms actual sleep on loaded hosts, causing two consecutive ULIDs to share a timestamp and sort by their random component — making the assertion non-deterministic. Fix: reduce the call count to 10 with `time.sleep(0.005)` per sleep (50ms total, still fast), or assert only that the timestamp *portions* (first 10 characters) are non-decreasing rather than the full ULID string. The full-string sort is strictly only guaranteed when timestamps differ.

- **N2 — `set_dry_run(False)` teardown in `test_intent_emits_under_set_dry_run` lacks `try/finally` protection.** The plan says "Reset with `log.set_dry_run(False)` in test teardown to avoid test pollution." If the test body raises, teardown runs in the `finally` of the test — but as written in the plan, the reset is described as a plain statement after the assertion, not inside a `try/finally` or pytest `autouse` fixture. If the assertion fails, `_dry_run_active` remains `True` and contaminates subsequent tests. Dev must use a `try/finally` block or a pytest `autouse` fixture that always resets the flag. The plan should specify the pattern explicitly (a one-line fixture is the idiomatic choice: `@pytest.fixture(autouse=True) def reset_dry_run(): yield; log.set_dry_run(False)`).

- **N3 — No test for `skill` field absence on events emitted outside `invoke` context.** Step 4 (`_build_record`) says to "omit the key entirely if both skill and `_current_skill.get()` are None." An absent key and a `null` key produce different JSON. None of the listed tests verify that `log.note(...)` or `log.error(...)` called outside any `invoke` context emits an event where the `skill` key is absent (not present as `null`). Add `test_note_outside_invoke_has_no_skill_key` (or similar) asserting `"skill" not in event`.

---

**Verdict:** changes-requested

Scope and test coverage are otherwise strong: all five spec § A3 acceptance criteria are present and each maps to at least one named test; ContextVar threading is correctly designed; the ULID inline approach is sound; concurrency design (O_APPEND + flock branch) correctly implements `08-logging.md`; the cap-and-hash design is fully specified; the plan 001 regression guard is explicit. The two blockers (B1 parameter-name conflict, B2 missing `_current_summary` declaration) are each one-sentence fixes but both will produce runtime failures if left unresolved.

### Pass 2 (2026-05-31)

**Resolution check against Pass 1 findings:**

- **B1 — `api()` `status_code` vs `status`:** Resolved. Step 10 explicitly names `status_code` throughout, labels the `08-logging.md` example a "doc error," and commits a follow-up fix in step 13. Acceptance criteria echo `status_code` consistently. No ambiguity remains.

- **B2 — `_current_summary` missing from step 6:** Resolved. Step 6 now declares all seven ContextVars, including `_current_summary: ContextVar[dict | None]`, and the count "seven" is stated explicitly. Step 7 correctly references "the seventh ContextVar `_current_summary`." No declaration gap.

- **M1 — Import surface ambiguity:** Resolved. A dedicated design-decision section ("lib.log is module-as-namespace; no `log` attribute defined inside") locks the canonical import form, notes the `08-logging.md` idiom is equivalent but unusual, and prohibits any `log = ...` assignment inside the file.

- **M2 — `_current_summary` guard idiom unspecified:** Resolved. A dedicated design-decision section ("_current_summary guard idiom") gives the canonical pattern with walrus-operator example, explains why `_current_session_id` is the wrong sentinel (auto-minted outside sessions), and lists `test_summary_mutators_outside_session_do_not_raise` as the coverage test.

- **N1 — ULID test fragile:** Resolved. Test renamed, uses a single pair with `time.sleep(0.005)` (5ms), bulk concurrent ordering explicitly ruled out. Sortability assertion is now robust.

- **N2 — `set_dry_run` teardown:** Resolved. Plan specifies an `autouse` fixture `_reset_dry_run` and explicitly forbids a bare `log.set_dry_run(False)` after assertions. Same discipline extended to any test that mutates module-level globals.

- **N3 — No test for absent `skill` key:** Resolved. Test `test_api_call_outside_invoke_has_no_skill_key` added, asserting `"skill" not in event`.

**New observations (no new blockers or majors):**

- **Cosmetic:** The heading "Design decision — `intent` gating is checked at call time, not at module load" appears twice (lines 137 and 183); the first instance has no body. This is an editorial artifact with no functional consequence and does not affect Dev.

**Blockers:** 0  
**Major:** 0  
**Minor:** 0  

**Verdict: ready**
