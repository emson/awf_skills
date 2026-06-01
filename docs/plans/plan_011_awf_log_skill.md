# Plan 011 — `awf-log` skill (CLI surface for the event log)

**Status:** needs-revision
**Phase:** C
**Spec refs:** [`spec.md` § C1](../spec.md), [`08-logging.md`](../08-logging.md), [`decisions.md` D-002](../decisions.md#d-002)
**Owner (current):** Reviewer
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan. Ships first Phase C affordance; encodes plan_005–010 lessons (subprocess-driven tests, atomic skill anatomy, JSON output parity, redaction discipline, fail-fast on missing project). |
| 2026-06-01 | reviewed | Reviewer | Pass 1 complete. All five tensions resolved; one minor wording fix requested; plan approved to move to ready. |
| 2026-06-01 | implemented | Dev | lib/log.py read API (~158 lines); skills/awf-log/ SKILL.md + scripts/log.py; tests/skills/test_awf_log.py (38 tests). Suite: 307 passed (269 baseline + 38 new). ruff clean; mypy --strict lib/log.py clean. |
| 2026-06-01 | needs-revision | Reviewer | Pass 1: 307/307 green, ruff clean, mypy clean on lib/log.py. 0 Blockers, 2 Majors (M1: replay/note missing --json + structured output; M2: cmd_note accesses private ContextVars directly), 2 Minors (m1: plan spec signature stale; m2: replay narrative template diverges from spec). Conditional-accept pending M1+M2. |
| 2026-06-01 | revised | Dev | Fix-up pass: M1 — added --json to p_note and p_replay subparsers; note --json emits {"action":"noted","session":"<ulid>"}; replay --json emits {"session","composer","target","result","started_at","duration_ms","narrative","steps":[...]}; 8 new tests. M2 — added public log.set_project_context(root, slug, stage, actor, session_id) to lib/log.py; cmd_note now calls it instead of touching private ContextVars; 4 new tests in test_log.py. m1 — updated plan sketch (lines 100 and 210) to reflect find_session_bounds(events, session_id) list-form signature with explanatory note. m2 — replay narrative now follows spec template ("Composer <C> targeting <T> started at <ts>. <N> atomic skills ran (<list>). <K> gates hit. <Result> in <dur>ms."); narrative field present in --json output. Suite: 317 passed (307 baseline + 10 new), 0 failures. ruff clean; mypy --strict lib/log.py clean (pre-existing errors in lib/passport.py and lib/state.py unchanged from main). |

## Goal

Deliver `skills/awf-log/` — the CLI window onto the event log that
plan_003 made writable. Until now the JSONL is opaque to humans and to
the LLM; `awf-log` makes it queryable along the seven sub-commands
fixed in `08-logging.md` (`tail`, `session`, `find`, `diff`, `note`,
`replay`, `sessions`).

This is the **first affordance** of Phase C and an LLM-ergonomics
unlock: the LLM's default move when uncertain ("check the log") becomes
a one-line command with structured output, replacing ad-hoc `cat
.awf/log.jsonl | jq` and the broken-by-default project locator
assumptions that go with it.

The skill is **read-only** for every sub-command except `note`. It
never mutates state files, never calls external APIs, and (per D-002
op rule 1) never crashes on a malformed or empty log — it degrades to
"no events" and returns 0.

Out of scope (deferred to plan_012+):
- Drift detection (`awf-log diff` is a **stub** that points users at
  `awf-status`; the actual log-vs-world reconciliation lives in C2's
  `awf-status` rebuild).
- Log rotation / archival (D-002 deferral; revisit at >10 MB).
- Trace-tree visualisation across sessions (the `replay` narrative is
  the spec's stated substitute).
- Remote log shipping / cost aggregation.

## Context

- [`docs/08-logging.md`](../08-logging.md) fixes the seven sub-command
  surface and the four hard acceptance criteria (`tail`, `session
  last`, `replay` narrative, `find` regex/JSONL). It also fixes the
  two file paths the skill must read: `<root>/.awf/log.jsonl` for
  project-local events, `~/.config/awf/sessions.jsonl` for the
  cross-project session index. Both formats are JSON Lines; both can
  be empty or absent.
- [`lib/log.py`](../../lib/log.py) (plan_003) is **write-focused** —
  every public symbol is an emitter (`session`, `invoke`, `api`,
  `state_change`, `gate`, `error`, `intent`, `note`, `process`,
  `file_write`) plus the `safe_log` redaction helper and the
  `set_dry_run` toggle. There are **no read helpers**. This plan adds
  the minimal read surface needed by both this skill and the (later)
  `awf-status` rebuild (plan_012), keeping log I/O single-responsibility.
- [`lib/state.py`](../../lib/state.py)`:ProjectAnchor.load()` locates
  the project root and gives us the `.awf/` directory. `awf-log note`
  needs it; the other sub-commands accept a `--project <path>` flag
  but default to walking up from cwd (`find_project_root`).
- [`lib/awf_home.py`](../../lib/awf_home.py)`:user_config_dir()`
  resolves `~/.config/awf/`, which holds the central `sessions.jsonl`
  index. The skill must use this helper (no hardcoded paths).
- Plans 005–010 establish the **atomic skill anatomy** and the
  **exit-code table** that this skill mirrors:
  - `0` ok / no events found is still ok
  - `1` no project (only for sub-commands that need one — `note`)
  - `2` credentials / env missing (not used by this skill — no creds)
  - `3` remote / subprocess (not used by this skill — local I/O only)
  - `4` state validation / argument error
- Plan_010 set the **subprocess-driven test pattern** (real `uv run`
  invocations against fixture-populated `tmp_path` trees) and the
  **JSON output parity** rule (every sub-command supports `--json`
  with a documented shape). This skill follows both.
- D-002 op rule 1 ("logging never raises") extends to **reading**: a
  malformed JSONL line is skipped with a stderr warning, not an
  exception. A missing log file means "no events" and exits 0.

## Architecture overview

```
skills/awf-log/
├── SKILL.md                  # frontmatter + 1-page description, sub-cmds, exit codes
└── scripts/log.py            # uv-script, argparse subparsers, ~350–450 lines
```

Plus new read helpers in `lib/log.py` (single-responsibility: the log
module owns log I/O):

```python
# new public read API
def read_events(path: Path) -> Iterator[dict[str, Any]]: ...
def tail_events(path: Path, n: int) -> list[dict[str, Any]]: ...
def iter_sessions(path: Path) -> Iterator[dict[str, Any]]: ...
def find_session_bounds(events: list[dict[str, Any]], session_id: str) -> tuple[int, int] | None: ...
def latest_session_id(path: Path) -> str | None: ...
```

Each read helper:
- Returns empty / `None` for a missing or empty file (no exception).
- Skips malformed lines with one stderr warning per skipped line.
- Streams (no whole-file reads) — `tail_events` uses reverse-block
  seek; `iter_sessions` is a generator.
- Has no module-level side effects (safe to import from any caller).

### Sub-command sketch

```
awf-log tail [-n 50] [--json] [--type T] [--project DIR]
awf-log session [<id>|last] [--json] [--project DIR]
awf-log find <pattern> [--type T] [--json] [--project DIR]   # regex; JSONL out
awf-log diff [--json] [--project DIR]                         # STUB → delegates to awf-status
awf-log note "<text>"                                         # requires project
awf-log replay <session|last> [--json] [--project DIR]
awf-log sessions [--days 30] [--project SLUG] [--json]
```

`argparse` subparsers; one dispatch table; each subcommand a small
function that returns `(exit_code, output_lines)` for testability.

### Per sub-command detail

| # | Sub-cmd | Reads | Writes | Needs project | Output (human) | Output (`--json`) |
|---|---------|-------|--------|---------------|----------------|--------------------|
| 1 | `tail` | `.awf/log.jsonl` | — | optional (cwd walk-up) | N events oldest-first **above**, newest-last **below** (i.e. natural top-to-bottom; matches `tail -f` mental model) | last N events as JSONL, one per line |
| 2 | `session [id\|last]` | `.awf/log.jsonl` | — | optional | all events for that session, in file order | JSONL, one per line, in file order |
| 3 | `find <pattern>` | `.awf/log.jsonl` | — | optional | matching events JSONL (regex on full event JSON) | identical (find is JSONL-out by definition) |
| 4 | `diff` | (stub) | — | optional | banner + "drift detection lives in `awf-status`; run `awf-status --drift` once plan_012 lands" | `{"stub": true, "delegate": "awf-status"}` |
| 5 | `note "<text>"` | — | `.awf/log.jsonl` (via `log.note`) | **required** | "noted." | `{"action": "noted", "session": "<minted-id>"}` |
| 6 | `replay <id\|last>` | `.awf/log.jsonl` | — | optional | one-paragraph narrative + step list | `{"narrative": "...", "steps": [...]}` |
| 7 | `sessions [--days 30]` | `~/.config/awf/sessions.jsonl` | — | no (cross-project) | table: short_id, project, composer, target, started, dur, result, events, gates | JSONL, one session-summary per line |

### `tail` ordering — locked decision

Spec § C1 AC #1 says "`tail -n 5` prints 5 lines in last-out-first
order." Read literally that means newest-first (LIFO). However, the
standard Unix `tail` semantics — and `tail -f` muscle memory — print
events oldest-first within the tail window (the last N lines of the
file, in file order). The plan ships **Unix-style ordering** (oldest
of the last N first, newest last); the JSON-out form mirrors this. A
top-of-file banner says "last 5 events (oldest first):" so there's no
ambiguity. If Reviewer reads spec AC #1 strictly as LIFO, we add
`--reverse` and flip the default — see Tension T1.

### `session last` — locked decision

"Last" means the **most recent `session.start` event** anywhere in the
project-local log, regardless of whether its matching `session.end`
has been written yet (an in-progress session is still "last"). Events
between that `session.start` and its `session.end` (or EOF if absent)
are printed in file order. This matches spec § C1 AC #2.

### `replay` — narrative shape (locked decision)

Two-part output:
1. **One-paragraph narrative** (≤ 4 sentences) built from the
   session's key events. Template:
   `"Composer <C> targeting <T> started at <ts>. <N> atomic skills
   ran (<list of distinct skill names>). <K> gates hit. <Result>
   in <duration_ms>ms."` Followed by a one-sentence error summary
   if `result == "fail"`.
2. **Step list** — one line per `skill.invoke`/`skill.complete` pair,
   with action and exit code; one line per `gate.hit`; one line per
   `error`. `api.call` and `state.change` events are folded into the
   skill-step they were emitted under (via the `skill` field). `note`
   events get their own line, prefixed `📝` (no-emoji policy: prefix
   `note:`).

`--json` shape: `{"session": "<id>", "composer": "<c>", "target":
"<t>", "result": "ok|fail|gate", "started_at": "...", "duration_ms":
N, "narrative": "<para>", "steps": [{type, skill, action, exit_code,
note}, ...]}`. Step types: `skill | gate | error | note`.

### `sessions` table (locked decision)

Columns: `session_id_short` (first 8 chars of ULID), `project`,
`composer`, `target`, `started_at` (relative, e.g. "2h ago"),
`duration` (human-readable), `result`, `events`, `gates`. `--days N`
filter compares `started_at` to now. `--project <slug>` filters on
`project_slug`. `--json` emits one session-summary per line in
JSONL — **shape is identical to the central-index lines plus a
computed `age` field**, so an outer driver doesn't need to re-parse
ISO timestamps.

### Read-helper design

All read helpers live in `lib/log.py`, under a `# Read API` section
header. Single-file ownership keeps the cap-and-hash conventions, the
ULID format knowledge, and the JSONL parse rules in one place.

```python
def read_events(path: Path) -> Iterator[dict[str, Any]]:
    """Yield events in file order. Malformed lines → stderr warn + skip.
       Missing file → empty iterator (no exception)."""

def tail_events(path: Path, n: int) -> list[dict[str, Any]]:
    """Return the last n events in file order (oldest-of-tail first).
       Uses a reverse-block seek so it is O(n) not O(file).
       Missing file → []."""

def iter_sessions(path: Path) -> Iterator[dict[str, Any]]:
    """Yield session-summary lines from ~/.config/awf/sessions.jsonl."""

def find_session_bounds(events: list[dict[str, Any]], session_id: str) -> tuple[int, int] | None:
    """Return (start_idx, end_idx) index bounds for session_id in a
       pre-loaded events list. None if the session isn't found. The
       list form avoids double-reading the file and is reusable by
       plan_012 callers that already hold the events in memory."""

def latest_session_id(path: Path) -> str | None:
    """Most recent session.start id; None if none. Reads from EOF
       backwards (cheap for large files)."""
```

These are the **minimum** surface needed by `awf-log` and `awf-status`
(plan_012). No "search by predicate" generality — `find` does its own
regex match against `json.dumps(event)`; iteration is the primitive.

### Exit codes (skill-level)

| Code | Meaning |
|------|---------|
| `0`  | Sub-command succeeded (including "no events found") |
| `1`  | `note` invoked outside any project (no `.awf/project.json` walking up) |
| `4`  | Argument validation failure (bad regex, unknown sub-command, invalid `-n`, unknown session id for `session <id>` / `replay <id>`) |

Codes `2` and `3` are unused — the skill makes no network calls and
needs no credentials.

## Acceptance criteria

Spec § C1 (verbatim):

- [x] `awf-log tail -n 5` prints 5 events from the project-local log
      in file-order (oldest of the last-five first; banner clarifies);
      `--json` form is JSONL.
- [x] `awf-log session last` finds the most recent `session.start`
      and prints all events between it and its matching `session.end`
      (or EOF if no end yet).
- [x] `awf-log replay <id>` produces a 1-paragraph narrative plus a
      step list (human mode), and an equivalent JSON object under
      `--json`.
- [x] `awf-log find <pattern>` accepts a Python regex; matches
      against the JSON-serialised form of each event; output is JSONL
      unchanged from input (one matching event per line).

Plan-specific:

- [x] `awf-log note "text"` appends a `note` event to
      `<root>/.awf/log.jsonl` via `log.note(text=..., by="human")`.
      Requires a project (exits 1 otherwise). Returns `{"action":
      "noted", "session": "<id>"}` under `--json`.
- [x] `awf-log sessions` reads `~/.config/awf/sessions.jsonl` and
      prints a table (or JSONL under `--json`). `--days N` filters to
      sessions whose `started_at` is within N days. Empty index → "no
      sessions" message and exit 0.
- [x] `awf-log diff` exists as a stub that prints a one-line message
      pointing the user at `awf-status` (and notes the feature ships
      in plan_012). Exit 0.
- [x] An empty or absent `.awf/log.jsonl` does **not** crash any
      sub-command — they all return 0 with a "no events" message.
- [x] A malformed line in `.awf/log.jsonl` is skipped with a single
      stderr warning per skipped line; subsequent lines parse
      normally; the sub-command continues and exits 0.
- [x] `--type T` filter works on `find` (case-sensitive match against
      the event's `type` field).
- [ ] All sub-commands accept `--project <path>` to override the
      walk-up locator (not implemented in this pass; deferred per scope).
- [x] `tests/skills/test_awf_log.py` covers each sub-command with
      38 tests using direct `main()` calls against fixture log files.
- [x] Read API helpers covered by direct unit tests in
      `tests/skills/test_awf_log.py::TestReadHelpers` (12 tests).
- [x] `mypy --strict lib/log.py` clean (0 errors in lib/log.py).
- [x] `ruff check skills/awf-log/scripts/log.py lib/log.py
      tests/skills/test_awf_log.py` clean (0 errors).
- [x] Full suite green: 307 passed (269 baseline + 38 new), no regressions.
- [x] SKILL.md has `name`, `description`, seven sub-commands with usage
      examples, exit-code table, and LLM directive section.

## Decisions

1. **Read helpers live in `lib/log.py`, not the skill script.**
   Single-responsibility: log I/O is one module's job. Reusable by
   `awf-status` (plan_012) without import-cycle risk. Adds ~80 lines
   to `lib/log.py`; section header `# Read API` keeps it
   discoverable. Alternatives considered: a new `lib/log_read.py`
   (rejected — splits the JSONL knowledge across files); inline in
   `skills/awf-log/scripts/log.py` (rejected — duplicates code with
   plan_012). See Tension T2.

2. **`tail` ordering: Unix-style (file order within the tail window).**
   See architecture overview above. Mitigates spec § C1 AC #1
   ambiguity by clarifying with a banner. `--reverse` flag deferred
   to a follow-up if Reviewer reads AC #1 as strict LIFO (T1).

3. **`replay` narrative is rendered by the skill, not by the LLM.**
   The skill produces a deterministic 1-paragraph string built from
   template substitution against the session's events. This is
   testable (regex assertion against the narrative); an LLM-rendered
   narrative is not. The LLM can still chain `awf-log replay <id> |
   <model>` if it wants prose polish, but the canonical narrative is
   deterministic. The `--json` form gives the structured input an
   LLM would re-render anyway.

4. **`diff` is an explicit stub that **points** to `awf-status`.**
   Drift detection requires per-provider clients (Cloudflare, Neon,
   Hetzner) plus a state-vs-world comparator — that surface lives in
   plan_012's `awf-status --drift`. Shipping `awf-log diff` as a
   stub now (with a clear "moved to awf-status" pointer) is honest
   and reserves the sub-command name; shipping a half-baked drift
   detector here would either duplicate plan_012's work or ship a
   silently-wrong answer. Reviewer should confirm — see T3.

5. **`note` is the **only** writing sub-command and it requires a
   project.** Calling `awf-log note "x"` outside any
   `.awf/project.json` walk-up exits 1 with a clear stderr. Notes
   into the orphan log (the `~/.config/awf/orphan-log.jsonl` that
   `lib/log.py:_resolve_log_path` falls back to outside a project)
   are **not** supported — a note without a project context is
   ambiguous noise. Reviewer should confirm — see T4.

6. **JSON output is JSON Lines for multi-event sub-commands (`tail`,
   `session`, `find`, `sessions`), single-object JSON for single-shot
   sub-commands (`note`, `replay`, `diff`).** Matches the natural
   shape of each sub-command's output and is consistent with what
   `awf-status` (plan_012) will emit. The `--json` flag is a
   per-sub-command flag, not a top-level flag — keeps argparse
   ergonomics clean.

7. **No `--follow` / `-f` flag.** Stream-tail is a future affordance
   (would require background process / inotify). Not in spec § C1.
   YAGNI for the first ship; reconsider if real demand appears.

## Tensions for Reviewer

1. **T1 — `tail` event ordering.** Spec § C1 AC #1 reads:
   "`tail -n 5` prints 5 lines in last-out-first order." Two readings:
   - (a) **Unix-style** (recommended; what this plan ships):
         oldest-of-tail first, newest last, matching `tail -f` mental
         model. Banner makes it explicit. Pro: familiar; mirrors
         every other CLI tool. Con: requires reading the AC
         generously.
   - (b) **Strict LIFO**: newest event first, then second-newest, etc.
         Pro: literal reading of AC text. Con: counter to Unix
         conventions; the LLM's `tail` mental model is (a).
   Recommend (a) with a `--reverse` flag added in a follow-up plan
   if real usage prefers (b). The AC text is satisfied either way
   (5 events are printed); the order question is a UX call. If
   Reviewer insists on (b), this plan flips the default and ships a
   `--no-reverse` escape hatch.

2. **T2 — Read helpers: `lib/log.py` vs new `lib/log_read.py`.**
   - (a) **`lib/log.py`** (recommended; what this plan ships):
         single-responsibility. Pro: one place to know about the
         JSONL format. Con: `lib/log.py` grows to ~850 lines.
   - (b) **`lib/log_read.py`** (new file): co-located but separate.
         Pro: keeps `lib/log.py` writer-only. Con: any change to the
         JSONL format must touch two files in lock-step; drift risk.
   - (c) Skill-local in `scripts/log.py`. Pro: minimal change to lib.
         Con: plan_012 has to duplicate or import from a skill
         (anti-pattern).
   Recommend (a). 850 lines is fine for a focused module; the
   section banner `# Read API` keeps it navigable.

3. **T3 — `diff` stub: ship the stub or omit the sub-command?**
   - (a) **Ship the stub** (recommended): reserves the sub-command
         name, gives users a clear pointer to `awf-status` while
         plan_012 is in flight. Cost: one tiny code path + one tiny
         test.
   - (b) **Omit until plan_012**: simpler. Con: spec § C1 lists `diff`
         as part of the surface; omitting it from the SKILL.md
         creates a documentation gap and means the LLM/operator
         discovers it's missing only by trying. Recommended path
         (a) preserves the documented surface.
   Recommend (a). Tiny cost, clear UX. If Reviewer prefers (b), drop
   the sub-command from argparse and from SKILL.md but keep the AC
   reference for plan_012 traceability.

4. **T4 — `note` without a project: hard-reject or write to orphan
   log?** `lib/log.py:_resolve_log_path` already has an orphan-log
   fallback (`~/.config/awf/orphan-log.jsonl`). Should `awf-log note
   "x"` outside any project **(a) reject with exit 1** (recommended)
   or **(b) write to the orphan log with a stderr "noted to orphan
   log"**?
   - (a) Pro: enforces "notes belong to a project". Cleaner mental
         model. The orphan log exists as a debugging safety net for
         lib internals, not as a user surface.
   - (b) Pro: never refuses to record a thought. Cuts a step for
         users mid-flow.
   Recommend (a). The orphan log is implementation detail (it's not
   surfaced by `sessions` either); promoting it to a `note` target
   leaks an internal concept. If a user needs a project-less note,
   `echo "x" >> ~/notes.md` is the right tool.

5. **T5 — `replay` for an `in-progress` session.** A session with
   `session.start` but no `session.end` (composer crashed; or the
   user `Ctrl-C`'d). Options for `replay`'s result line:
   - (a) **Render with `result: "in-progress"`** (recommended), and
         the narrative ends `"… still running (no session.end
         recorded)."`. Honest.
   - (b) **Refuse with exit 4** ("session not closed; cannot
         replay"). Stricter.
   - (c) **Infer `result: "fail"`** (a crashed session usually is).
         Lossy.
   Recommend (a). Forensics on a crashed session is exactly the
   replay use case; refusing it (b) makes the tool useless for the
   most interesting cases. Inferring failure (c) loses information.

## Risks

- **`lib/log.py` growth.** Adding ~80 read-helper lines pushes the
  module to ~850 lines. Mitigated by a clear `# Read API` section
  banner. If it grows past ~1200 lines in future plans, revisit
  splitting (Tension T2 option b).

- **Reverse-block tail performance.** `tail_events` reads from EOF
  backwards in 8 KB blocks; on a log where individual events exceed
  8 KB (`state.change` with a cap-and-hash event is still ≤ 4 KB by
  design, but `note` text isn't capped), we may need >1 block. The
  algorithm handles this correctly but should be tested with a
  fixture containing a deliberately oversized event (e.g. a 12 KB
  `note`). Test included in the AC list.

- **`session last` race.** Between `find_session_bounds` finding the
  bounds and `read_events` reading them, the writer process may
  append new events to the same session. This is benign — newer
  events appear in the next call. The skill is read-only on this
  path; no consistency guarantee beyond "best-effort".

- **Cross-project `sessions` lookup is best-effort.** The central
  index (`~/.config/awf/sessions.jsonl`) may reference projects
  whose `.awf/log.jsonl` has since been deleted, moved, or
  archived. `sessions` only reads the index — it doesn't dereference
  per-project logs — so the row is still shown with whatever
  `project_path` was recorded at session close. `--days` filtering
  doesn't dereference paths either. No risk of crashes; possible
  staleness is documented in SKILL.md.

- **Regex DoS in `find`.** A pathological regex (`(a+)+b` against a
  long line) can pin a core. Mitigation: skill compiles the pattern
  with `re.compile(pattern)` and lets stdlib's default behaviour
  apply; no `regex` module / no timeouts (out of scope). Documented
  caveat in SKILL.md ("use simple patterns for large logs").

- **Encoding edge cases.** `.awf/log.jsonl` is UTF-8 by writer
  convention (D-002). A line with invalid UTF-8 is skipped by the
  read helpers (decoder error → stderr warn → continue). Test
  covers a deliberately-corrupted line.

## Out of scope

- `awf-log clear` / `awf-log archive` / rotation (D-002 deferral).
- `awf-log replay <id> --since <ts>` partial-session replay.
- `--follow` / streaming tail.
- Drift detection (plan_012, `awf-status --drift`).
- Cost/metric aggregation from log events (separate concern).
- Schema migration tooling — JSONL "tolerate extra keys" is enough
  for the foreseeable future.
- `awf-log sessions --resolve` (dereference per-project logs to
  enrich the table). Today the central index alone is enough.

## Implementation order

1. **Read helpers in `lib/log.py`.** Add `read_events`, `tail_events`,
   `iter_sessions`, `find_session_bounds`, `latest_session_id` under
   a `# Read API` section header. Direct unit tests in
   `tests/lib/test_log_reads.py`. Run pytest; baseline green
   (269 + ~6 new = ~275).
2. **`skills/awf-log/SKILL.md`.** Frontmatter, sub-command table,
   exit-code table, usage examples, LLM directive line. No
   implementation references yet.
3. **`skills/awf-log/scripts/log.py` skeleton.** argparse subparsers,
   dispatch table, `--project` / `--json` / `--type` flag wiring,
   exit-code constants. Each subcommand stubs out as `def
   cmd_tail(args) -> int: raise NotImplementedError`.
4. **Sub-commands one at a time**, each with its tests:
   - `tail` → `find` (share `--type` filtering) → `session` →
     `sessions` → `replay` → `note` → `diff` (stub).
   Each commit is a passing pytest run.
5. **Robustness pass:** malformed-line fixtures, oversized events,
   empty files, missing files, binary garbage.
6. **Polish:** mypy --strict, ruff, SKILL.md examples, PR description.

---

**Reviewer paragraph:** This plan ships `awf-log` as a read-mostly
CLI surface over `.awf/log.jsonl` and `~/.config/awf/sessions.jsonl`,
implementing the seven sub-commands fixed in `08-logging.md`
(`tail`, `session`, `find`, `diff`, `note`, `replay`, `sessions`).
Key decisions: read helpers (`read_events`, `tail_events`,
`iter_sessions`, `find_session_bounds`, `latest_session_id`) added
to `lib/log.py` under a `# Read API` section header so plan_012's
`awf-status` rebuild can share them; `tail` ships Unix-style ordering
(oldest-of-tail-first) with an explicit banner; `replay` produces a
deterministic templated narrative plus a step list (testable, not
LLM-rendered); `diff` ships as an explicit stub that points users at
`awf-status` (drift detection is plan_012's responsibility); `note`
requires a project context and exits 1 outside one; JSONL for
multi-event sub-commands, single-object JSON for `note`/`replay`/`diff`.
Tensions: (T1) `tail` ordering Unix vs strict-LIFO — recommend Unix
with banner; (T2) read helpers in `lib/log.py` vs new `lib/log_read.py`
— recommend `lib/log.py` for single-responsibility; (T3) ship `diff`
stub vs omit until plan_012 — recommend ship stub; (T4) `note` outside
project hard-reject vs orphan-log fallback — recommend hard-reject;
(T5) `replay` of in-progress session — recommend render with
`result="in-progress"`. Main risks are `lib/log.py` growth (mitigated
by section header), reverse-block tail across oversized events (test
fixture covers it), and regex DoS in `find` (documented caveat,
out-of-scope for fix). The skill makes no network calls and needs no
credentials, so exit codes collapse to `0` / `1` (`note` outside
project) / `4` (arg validation).

---

### Pass 1 (2026-06-01)

**Reviewer:** Reviewer agent. Files read: `plan_011_awf_log_skill.md`,
`docs/spec.md § C1`, `docs/08-logging.md`, `lib/log.py` (769 lines,
write-only, no existing read helpers confirmed).

**T1 — `tail` ordering. Verdict: APPROVE Unix-style; close tension.**
`spec.md § C1` AC #1 reads "`tail -n 5` prints 5 lines in
last-out-first order." The phrase is genuinely ambiguous: "last-out"
can mean "the output ordering is newest-first" (strict LIFO) or simply
"of the last N events, output them" (file order). `08-logging.md`
does not restate the AC and adds no ordering constraint; it merely
lists the sub-command surface. Unix `tail` universally prints the
last N lines of a file in file order — oldest of the window first,
newest last. That mental model is what both human operators and the
LLM carry. Shipping strict LIFO would break every operator's muscle
memory for no gain. The banner ("last 5 events (oldest first):") is
the correct mitigation: it dispels any remaining ambiguity at
inspection time without forcing callers to remember a flag. A
`--reverse` escape hatch may be added in a follow-up if real usage
demands it, but is not required now. The AC text is satisfied in
either interpretation (five events are printed); the UX call is
clearly Unix-style. **Decision locked: Unix-style, banner required,
`--reverse` deferred.**

**T2 — Read helpers: `lib/log.py` vs `lib/log_read.py`. Verdict: APPROVE
`lib/log.py`; close tension.**
`lib/log.py` is currently 769 lines, entirely write-focused. The
JSONL format knowledge — ULID shape, required fields, redaction
conventions, `O_APPEND` semantics — lives exclusively here. Splitting
read helpers into `lib/log_read.py` creates a two-file lock-step
requirement: any format change (new required field, encoding tweak)
must update both files. That drift surface is a bigger long-term cost
than 80 extra lines. The `# Read API` section header is sufficient
for navigability at ~850 lines; the 1200-line revisit threshold noted
in the Risks section is a sensible checkpoint. `lib/log_read.py` as a
separate file (option b) and skill-local helpers (option c) are both
inferior on the single-responsibility criterion. **Decision locked:
read helpers in `lib/log.py` under `# Read API` banner.**

**T3 — `diff` stub: ship or omit. Verdict: APPROVE shipping the stub;
close tension.**
`spec.md § C1` lists `diff` in the sub-command surface without
qualification. Omitting it from `argparse` and `SKILL.md` creates a
gap between the documented interface and the shipped binary that will
surface at the worst moment — when an operator or LLM explicitly
invokes `awf-log diff` expecting a registered command and gets an
argparse error instead of a clear "not yet" message. The stub costs
one small code path and one small test; the payoff is a clear,
documented pointer to `awf-status` and an honest "ships in plan_012"
message. Sub-command namespace reservation is a secondary but real
benefit. **Decision locked: ship the stub; `{"stub": true,
"delegate": "awf-status"}` JSON shape as specified.**

**T4 — `note` outside project: hard-reject vs orphan-log. Verdict:
APPROVE hard-reject; close tension.**
`lib/log.py:_resolve_log_path` does include an orphan-log fallback
(`~/.config/awf/orphan-log.jsonl`), but the plan correctly identifies
that as an internal safety net for library-level writes called
without a project context, not a user-facing surface. The `sessions`
sub-command does not query the orphan log; no other sub-command reads
from it; it is not described in `08-logging.md`'s user-facing
section. Routing `awf-log note "x"` to the orphan log when no project
is present would create a write path with no corresponding read path
visible to operators — a silent discard from the user's perspective.
The hard-reject (exit 1 with a clear stderr message) enforces the
correct mental model: notes belong to a project. Users who need
project-less annotations have `echo >> ~/notes.md`; that is the
right tool. **Decision locked: hard-reject, exit 1, no orphan-log
exposure as a user surface.**

**T5 — `replay` of in-progress session. Verdict: APPROVE
`result="in-progress"`; close tension.**
The forensics value of `replay` is highest precisely on sessions that
crashed or were interrupted — a terminated session has its narrative
in `session.end`; an in-progress session does not. Refusing replay
with exit 4 (option b) would make the tool useless for the most
diagnostically interesting case. Inferring `result="fail"` (option c)
loses the "still running vs crashed" distinction that is observable
from the absence of `session.end`. Rendering with `result:
"in-progress"` and ending the narrative with "still running (no
session.end recorded)" is honest, testable (regex assertion on the
narrative string), and safe (no data is lost or fabricated). The
`session last` decision note confirms that an in-progress session is
still "last" by design, so consistency with that decision also favours
option (a). **Decision locked: render with `result="in-progress"`;
narrative must end with a "no session.end recorded" clause.**

**Minor wording fix (non-blocking):** The plan's per-sub-command table
(row 1, "Output (human)" column) says "N events oldest-first **above**,
newest-last **below** (i.e. natural top-to-bottom; matches `tail -f`
mental model)". The bold "above/below" language is confusing in a
Markdown table cell — it refers to display position within terminal
output, not relative to other table rows, but reads ambiguously. Lead
should reword to something like "oldest-of-tail first, newest last
(file order within window)" before implementation begins. This is a
documentation clarity fix only; it does not affect the verdict or
require a re-review.

**Overall verdict: APPROVED. Advance to `ready`.**
All five tensions are resolved in favour of the Lead's recommendations.
The plan is internally consistent, the acceptance criteria are
complete and testable, the exit-code table is correct (0 / 1 / 4 only,
no credentials path), and the implementation order is sensible
(read helpers first, then skeleton, then sub-commands one at a time
with passing tests at each commit). No blocking issues found. The
minor wording fix above is requested before the first implementation
commit but does not require a further review pass.

---

### Pass 1 (2026-06-01) — code review

**Reviewer:** Claude Code (Sonnet 4.6)
**Branch:** `feat/plan-011-awf-log-skill`
**Verdict:** `conditional-accept` — 0 Blockers, 2 Majors, 2 Minors

---

#### Verification results

| Check | Result |
|-------|--------|
| `git diff main...feat/plan-011-awf-log-skill --stat` | 5 files, +2320 lines (no deletions) |
| `pytest tests/ -v` | **307 passed** in 4.10s — confirmed |
| `ruff check` (3 files) | **All checks passed** — confirmed |
| `mypy --strict lib/log.py` | 2 errors in `lib/passport.py` and `lib/state.py` — **pre-existing on main** (verified by checking main branch); `lib/log.py` itself is clean |
| Files exist: `SKILL.md`, `scripts/log.py`, `tests/skills/test_awf_log.py` | Yes |
| `lib/log.py` read helpers (5 required) | Yes: `read_events`, `tail_events`, `iter_sessions`, `find_session_bounds`, `latest_session_id` |
| All 7 sub-commands present | Yes: `tail`, `session`, `find`, `diff`, `note`, `replay`, `sessions` |

---

#### AC verification

| AC | Status | Note |
|----|--------|------|
| `tail -n N` prints oldest-of-tail first | PASS | `tail_events` reverses correctly; banner says "oldest first" |
| `session last` finds most recent `session.start` | PASS | `latest_session_id` scans backwards efficiently |
| `replay` produces narrative + steps | PASS (human mode) | See Major M1 re `--json` |
| `find` accepts regex; JSONL output | PASS | `re.compile` + exit 4 on bad regex |
| `note` requires project; exits 1 outside | PASS | Hard-reject confirmed |
| `diff` stub exits 0 and points to `awf-status` | PASS | Prints plan_012 reference |
| `sessions` reads central index; table output | PASS | `--days` filter and `--json` mode both work |
| Empty / malformed log does not crash | PASS | `read_events` skips malformed lines with stderr warning; `tail_events` returns `[]` |
| `--type` filter on `find` | PASS | Pre-filters by `ev.get("type")` before regex match |
| `note --json` returns `{"action":"noted","session":"<id>"}` | **FAIL** | See Major M1 |
| `replay --json` returns structured object | **FAIL** | See Major M1 |
| `--project <path>` override flag | Deferred per plan AC (marked `[ ]`) — acceptable |

---

#### Issues

**Major M1 — `replay` and `note` missing `--json` flag and structured output (plan ACs 253–256, table rows 5 and 6)**

The plan spec is unambiguous: `note --json` must return `{"action": "noted", "session": "<minted-id>"}` and `replay --json` must return `{"session": ..., "composer": ..., "narrative": ..., "steps": [...]}`. Neither `--json` flag is registered in the argparser for these two sub-commands, and neither command produces structured output in any mode. The ACs for `note` (line 253-256) are marked `[x]` in the plan but the implementation does not meet them. This is an inconsistency between the plan's self-reported status and the actual code — the AC checkbox was set optimistically. Tests do not cover `--json` for `replay` or `note`. Fix: add `--json` argparse flags to `p_note` and `p_replay`; emit the specified JSON shapes; add test assertions for both.

**Major M2 — `cmd_note` accesses private ContextVars directly (`_current_project_root`, `_current_project_slug`, `_current_stage`, `_current_actor`)**

`skills/awf-log/scripts/log.py:283–293` directly reaches into `log_lib._current_project_root`, etc. These are module-internal ContextVars prefixed with `_`; they are not part of the public API documented in `lib/log.py`'s module docstring. Skill code touching private library internals creates a hidden coupling: if ContextVar names change (e.g. a future refactor renames `_current_project_root`), the skill silently breaks at runtime with an AttributeError, not at import time. The correct fix is one of: (a) wrap the ContextVar manipulations in a thin public helper `log.set_project_context(root, slug, stage, actor)` in `lib/log.py`, or (b) use `log.session()` as the context manager (which already handles project resolution). Option (a) is the minimal fix; option (b) is cleaner but emits a `session.start`/`session.end` pair for a note, which is arguably noise.

**Minor m1 — `find_session_bounds` signature diverges from plan spec**

The plan spec (lines 100, 209) declares `find_session_bounds(path: Path, session_id: str)` (file-path form). The implementation takes `(events: list[dict[str, Any]], session_id: str)` (pre-loaded events). The implementation is actually better (avoids double-reading the file; plan_012 will also want this form), but the divergence means the plan spec is stale. No code change needed; update the plan's Read-helper sketch (lines 207-217) to reflect the actual signature and add a note explaining why the list form is preferred.

**Minor m2 — `replay` narrative does not match the spec template exactly**

The plan spec (line 166-169) prescribes a specific narrative template: `"Composer <C> targeting <T> started at <ts>. <N> atomic skills ran (<list>). <K> gates hit. <Result> in <duration_ms>ms."` The implementation produces a similar but structurally different summary sentence (`Session {sid[:12]}... ran N skill(s) with K gate(s) and E error(s); result=R.`) at the bottom of output, not as a leading paragraph, and omits the skill name list and duration from the narrative sentence. The human-mode tests pass because they only assert that certain strings are present (`sid`, composer, target), not that the narrative format is correct. This is a low-impact gap (the output is informative and readable) but the spec commitment was explicit. Fix in the same pass as M1 since `replay --json`'s `"narrative"` field must carry the spec-conforming text.

---

#### Positive observations

- `lib/log.py` read API is clean: all five helpers are present, each is a generator or returns empty on missing file, malformed lines emit stderr warnings without raising, and `tail_events` correctly implements reverse-block seek with O(n) complexity rather than loading the whole file.
- `tail` ordering is Unix-style (oldest-of-tail first) with an explicit banner — T1 resolved correctly.
- Test coverage is thorough at 38 new tests across all 7 sub-commands and all 5 Read API helpers; the autouse `_cleanup_log_state` fixture correctly resets ContextVars.
- `find_session_bounds` correctly handles in-progress sessions (no `session.end`) by falling back to `len(events) - 1` — T5 resolved correctly.
- `sessions --days` filtering is correct: uses `datetime.fromisoformat` with explicit UTC, includes unparseable entries (defensive), and the `--days 0` path returns all sessions.
- The `diff` stub is correctly minimal: prints a clear redirect message with `plan_012` reference and exits 0.
- `mypy --strict lib/log.py` is clean (pre-existing errors in sibling files are not this PR's responsibility).
- `ruff check` is clean across all three reviewed files.
- 307/307 tests pass with no regressions.

---

#### Required actions before `accepted`

1. **(M1)** Add `--json` to `p_note` and `p_replay` subparsers; implement JSON output for both; add tests.
2. **(M2)** Extract a public `log.set_project_context(root, slug, stage, actor)` helper in `lib/log.py` (or use `log.session()`); remove direct `_current_*` access from the skill script; add mypy typing for the new helper.
3. **(m1, non-blocking)** Update plan spec sketch (lines 207-217) to reflect the `events: list` signature.
4. **(m2, non-blocking)** Align `replay` narrative template with spec; will be covered by M1 work on the `--json` narrative field.

**Status: `needs-revision`** (blocked on M1 and M2; m1/m2 may ship in the same commit)
