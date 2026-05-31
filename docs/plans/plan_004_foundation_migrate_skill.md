# Plan 004 — Foundation: `awf-migrate` skill

**Status:** accepted
**Phase:** A
**Spec refs:** [`spec.md` § A4](../spec.md#a4-awf-migrate-skill-new)
**Owner (current):** Reviewer
**Created:** 2026-05-31
**Updated:** 2026-05-31

## Goal

Deliver `skills/awf-migrate/` — a thin skill that wraps
`lib.project.ensure_anchor()` so a human (or composer) can explicitly
migrate a legacy `passport.json`-only project to the dual-file layout
(`passport.json` + `.awf/project.json`) introduced by plan 002.

This closes Phase A. Plans 001–003 shipped the libraries
(`lib/state.py`, `lib/project.py`, `lib/log.py`); this plan exposes the
last foundation behaviour to the user as a runnable skill.

The library half is already done — `ensure_anchor(root) -> ProjectAnchor`
is implemented in `lib/project.py` (plan 002, lines 84–129) and is
itself idempotent. This plan is essentially packaging: a `SKILL.md`,
a uv-script that calls the library, and a small test surface that
exercises the script via `uv run` subprocess.

## Context

- Spec: [`docs/spec.md` § A4](../spec.md#a4-awf-migrate-skill-new) —
  three-bullet purpose + behaviour, three acceptance criteria.
- ADR: [D-004 — project anchor split](../decisions.md#d-004) — the
  dual-file layout this skill migrates *into*. `ensure_anchor` is the
  locked migration primitive; this plan does not re-derive it.
- Skill authoring: [`docs/04-skill-authoring.md`](../04-skill-authoring.md)
  — SKILL.md anatomy, uv-script PEP-723 header, the
  `AWF_HOME` bootstrap pattern.
- Style template: [`skills/awf-doctor/`](../../skills/awf-doctor/) —
  same shape (SKILL.md + `scripts/<verb>.py` uv-script with
  `# /// script` header, dataclass result model, JSON/human output
  branch, exit-code discipline).
- Principles:
  - [A1 — idempotency](../01-principles.md): `ensure_anchor` is
    already idempotent; the skill must preserve that property at the
    process boundary (running it twice in a row is a no-op the second
    time, exit 0, no log noise beyond a `session.start`/`session.end`
    pair).
  - [A11 — resumability](../01-principles.md): the log is the audit
    track for resumes. Every invocation of this skill emits a
    `session.start` + `session.end` pair, plus the `state.change`
    events that `ProjectAnchor.save()` already emits via the plan
    001/003 shim. No new gate tracking required — migration is
    one-shot, not multi-step.

### Design decision — call the library, do not re-implement

The script's `main()` is approximately twenty lines: locate the
project root via `lib.project.find_project_root()`, call
`ensure_anchor(root)`, diff against the pre-call state of
`.awf/project.json` (file existed → no-op message; file did not
exist → migration message), print one line per file touched, exit 0.

Everything else — schema validation, ULID timestamps, `state.change`
emission — happens *inside* `ProjectAnchor.save()` (plan 001) and
`_emit_state_change` (plan 001 → plan 003). The skill must not
duplicate any of it.

### Design decision — pre-call probe is the only way to detect no-op

`ensure_anchor` returns the loaded-or-created `ProjectAnchor`
unconditionally; it does not signal whether work was done. The
skill needs that signal to print "already migrated" vs "migrated".
Implementation: before calling `ensure_anchor`, check
`(root / ".awf" / "project.json").is_file()`. Record the boolean
as `anchor_pre_exists`. After the call, if `anchor_pre_exists` is
True, print "already migrated" and emit no extra log events; if
False, print "migrated: created .awf/project.json" and rely on
`ProjectAnchor.save()` to emit the `state.change` event itself.

This is a *probe*, not a TOCTOU race — concurrent migration is not
a supported workflow, and even if two processes raced, the second
would see the anchor and no-op (because `ensure_anchor` itself
re-checks `is_file()` before writing). The probe is purely for the
user-visible message.

### Design decision — wrap the whole run in `log.session`

Per plan 003, every skill invocation is one logging session. The
script's `main()` body is wrapped in
`with log.session(composer="awf-migrate", target="anchor")`. Inside
that session, the `state.change` event(s) emitted by
`ProjectAnchor.save()` are automatically tied to the session via the
ContextVar threading from plan 003. No explicit `log.invoke` is
needed — this script *is* the skill body; the session boundary is
sufficient.

If the project is not found (legitimate user error), the script
prints a helpful message to stderr and exits non-zero. The session
context manager catches the re-raised exception, marks the session
`"fail"` in the `session.end` summary, and re-raises. We catch at
the outermost `try` in `main()` and convert to an exit code.

### Design decision — exit codes

Per spec § A4 AC3 ("exits 0 on no-op; 0 on successful migration;
non-zero only on I/O failure"):

- `0` — no-op (already migrated) **or** successful migration.
- `1` — project not found (no `passport.json` and no
  `.awf/project.json` walking up from cwd). Helpful message to
  stderr.
- `2` — I/O failure during `ensure_anchor` (e.g., read-only
  filesystem, malformed `passport.json` that fails schema
  validation). Exception message to stderr.

The split between `1` (no project) and `2` (I/O) is so a composer
calling this skill can distinguish "user is in the wrong directory"
from "the filesystem is broken." Both are non-zero; spec AC3 is
satisfied by any non-zero on I/O failure, and we extend it with the
helpful split for composers.

## Out of scope

- **Rolling back a migration.** `awf-migrate` is one-way. There is
  no `awf-unmigrate`. If a user wants to revert, they `rm
  .awf/project.json` manually.
- **Migrating across schema versions** (e.g., `awf_version=1` →
  `awf_version=2`). When `AWF_VERSION` bumps in the future, that is
  a separate plan; `ensure_anchor` will need new logic, and this
  skill will inherit it without changes.
- **Backing up `passport.json` before touching `.awf/`.** Migration
  does not mutate `passport.json`; the read-only contract is locked
  in plan 002. No backup needed.
- **Re-overlay of templates.** That is `awf-update-template`, a
  separate skill that already exists.
- **A composer-level `awf-launch` integration step that auto-calls
  `awf-migrate`.** Out of scope for Phase A. When a Phase B/C
  composer wants automatic migration, it imports
  `lib.project.ensure_anchor` directly — the library is the
  primitive; the skill is the human-facing surface.

## Dependencies

- **Plan 001 (`lib/state.py`)** — accepted. Provides
  `ProjectAnchor`, `AWF_VERSION`, `HasFlags`, `.save()`.
- **Plan 002 (`lib/project.py`)** — accepted. Provides
  `find_project_root()`, `ensure_anchor()`, `ProjectNotFound`.
- **Plan 003 (`lib/log.py`)** — accepted. Provides `log.session(...)`
  context manager. Without plan 003, the wrapped session would be a
  no-op (the `from lib import log` import would fail before plan
  003, but plan 003 is merged).
- **External / user prerequisites:** none. No new credentials, no
  new CLIs, no new dependencies. The uv-script declares only
  `pydantic` (transitively required by `lib.state` and
  `lib.passport`).

## Implementation steps

1. **Create skill directory.** `mkdir -p
   skills/awf-migrate/scripts`. The directory layout mirrors
   `awf-doctor/` exactly.

2. **Write `SKILL.md`.** YAML frontmatter with `name: awf-migrate`
   and a one-paragraph `description:` that the Claude Code skill
   loader will surface. Body sections (per
   `04-skill-authoring.md`):
   - **Purpose** — one paragraph: "Explicit one-shot upgrade for
     legacy projects (passport-only) to the dual-file layout
     (`passport.json` + `.awf/project.json`)."
   - **Prerequisites** — "A directory containing `passport.json`
     (legacy) or `.awf/project.json` (already migrated). Walks up
     from cwd to find one."
   - **Inputs** — none required. Optional flag `--json` for
     machine-readable output (mirrors `awf-doctor`).
   - **Procedure** — one step: run `uv run
     "$AWF_HOME/skills/awf-migrate/scripts/migrate.py"`. Report
     output verbatim. Exit-code table (0 / 1 / 2 as locked above).
   - **Idempotency** — "Running on an already-migrated project is a
     no-op: prints `already migrated`, exits 0, emits no
     `state.change` events."
   - **Failure modes** — "Project not found" (exit 1; tell user to
     `cd` into a project directory or run `awf-create-project`);
     "I/O failure" (exit 2; surface the underlying error from
     `ensure_anchor`).
   - **Manual gates** — none.

3. **Write the uv-script `scripts/migrate.py`.** PEP-723 header:
   ```python
   #!/usr/bin/env -S uv run --script
   # /// script
   # requires-python = ">=3.11"
   # dependencies = ["pydantic>=2"]
   # ///
   ```
   Body shape (mirrors `awf-doctor/scripts/check.py` lines 1–32):
   - Module docstring referencing spec § A4.
   - `AWF_HOME` bootstrap: `AWF_HOME = Path(os.environ.get(
     "AWF_HOME") or Path(__file__).resolve().parents[3]).resolve();
     sys.path.insert(0, str(AWF_HOME / "lib"))`.
   - Imports: `from project import find_project_root,
     ensure_anchor, ProjectNotFound`; `from lib import log` (using
     the `from lib import log` form locked in plan 003).
   - Argparse: one optional flag `--json` (default False).
   - `main()` body:
     1. Try `root = find_project_root()`. If `ProjectNotFound`:
        print helpful message to stderr ("Run from a directory
        containing `passport.json` or `.awf/project.json`."), exit 1.
     2. Probe `anchor_pre_exists = (root / ".awf" /
        "project.json").is_file()`.
     3. Open `with log.session(composer="awf-migrate",
        target="anchor"):`.
     4. Inside the session:
        ```python
        try:
            anchor = ensure_anchor(root)
        except OSError as e:
            print(f"awf-migrate: I/O failure: {e}", file=sys.stderr)
            sys.exit(2)
        except Exception as e:
            print(f"awf-migrate: migration failed: {e}", file=sys.stderr)
            sys.exit(2)
        ```
     5. Emit user-visible output:
        - If `anchor_pre_exists`: print `"already migrated: {root}/.awf/project.json"`.
        - Else: print `"migrated: {root}/.awf/project.json"`
          (one line per file touched — currently only the anchor;
          if `ensure_anchor` grows to touch more files later, this
          script extends the output accordingly).
        - In `--json` mode, print a single JSON object:
          `{"action": "no-op" | "migrated", "anchor_path": "...",
          "domain": "...", "slug": "..."}`.
     6. Exit 0.

4. **Wire `argparse`.** Same minimal pattern as
   `awf-doctor/scripts/check.py`: one `argparse.ArgumentParser` at
   the top of `main()`, one `--json` flag, `args = parser.parse_args()`.

5. **Test surface** (`tests/skills/test_awf_migrate.py`) — see
   "Tests required" below. Tests invoke the script via
   `subprocess.run([sys.executable, "-m", "uv", "run", ...])` —
   actually, follow plan 003's existing pattern: tests call the
   `migrate.py` script via `subprocess.run` with `uv run`
   directly, against a `tmp_path`-built fake project. The
   `AWF_HOME` env var is set to the repo root for the subprocess.

6. **Lint + type gate.** Dev runs and confirms green:
   - `ruff check skills/awf-migrate/scripts/migrate.py tests/skills/test_awf_migrate.py`
   - `mypy --strict skills/awf-migrate/scripts/migrate.py`
   - `pytest tests/skills/test_awf_migrate.py -q`
   - `pytest tests/lib/ -q` (regression: 67 existing tests stay green)

## Acceptance criteria

Copied from `spec.md § A4`, plus plan-specific additions flagged
`[plan]`.

**From `spec.md § A4`:**

- [x] Idempotent: running on an already-migrated project is a no-op
      ("already migrated" message; no `state.change` events emitted
      beyond what plan 001 already emits, which is zero on
      no-op since `ensure_anchor` returns early without calling
      `.save()`).
- [x] Emits `state.change` events for every file touched (the
      anchor write goes through `ProjectAnchor.save()` which emits
      `state.change` via the plan 001/003 shim — this skill does
      not need to emit anything extra).
- [x] Exits 0 on no-op; 0 on successful migration; non-zero only on
      I/O failure.

**Plan-specific:**

- [x] `[plan]` `SKILL.md` exists at `skills/awf-migrate/SKILL.md`
      with valid YAML frontmatter (`name: awf-migrate`,
      `description:` populated) and the six body sections specified
      in step 2.
- [x] `[plan]` `scripts/migrate.py` is a uv-script (PEP-723 header
      present) with `pydantic>=2` declared in `dependencies`.
- [x] `[plan]` The whole `main()` run is wrapped in
      `log.session(composer="awf-migrate", target="anchor")`. The
      project's `.awf/log.jsonl` gains one `session.start` and one
      `session.end` event per invocation (verified by reading the
      file after a subprocess call).
- [x] `[plan]` Project-not-found exits 1 (not 2). I/O failure
      exits 2. Both write a helpful message to stderr.
- [x] `[plan]` `--json` flag emits a single JSON object on stdout
      with `action`, `anchor_path`, `domain`, `slug`.
- [x] `[plan]` `ruff check` passes on `migrate.py` (mypy --strict
      deferred: no mypy in uv inline-script environment; ruff is
      the lint gate per the verify command in the task spec).

## Tests required

File: `tests/skills/test_awf_migrate.py`. Aligned with
[`docs/testing_principles.md`](../testing_principles.md) — behaviour
over implementation, real `tmp_path` projects, real `subprocess.run`
invocations of the script under `uv run`.

A shared helper builds a legacy project (writes a minimal valid
`passport.json` to `tmp_path`). Another helper builds an
already-migrated project (writes `passport.json` and runs
`ensure_anchor` directly to seed `.awf/project.json`).

The subprocess wrapper sets `AWF_HOME` to the repo root and `cwd` to
the `tmp_path` project.

**Happy paths**

- `test_legacy_project_migrates_and_creates_anchor` — build a
  legacy project; invoke script; assert exit 0, assert
  `.awf/project.json` exists after, assert stdout contains
  `"migrated"`, assert `.awf/log.jsonl` contains one
  `session.start`, one `session.end`, and at least one
  `state.change` event. Maps to spec AC2.
- `test_already_migrated_is_noop` — build an already-migrated
  project; invoke script; assert exit 0, assert stdout contains
  `"already migrated"`, assert `.awf/log.jsonl` gains exactly two
  events (`session.start` + `session.end`) and **no** new
  `state.change` event. Maps to spec AC1 + AC3.
- `test_json_flag_emits_structured_output` — build a legacy
  project; invoke with `--json`; assert stdout parses as a JSON
  object with the four required fields.

**Error paths**

- `test_no_project_exits_1` — invoke script with cwd set to a
  `tmp_path` directory that contains neither `passport.json` nor
  `.awf/project.json`; assert exit code is 1; assert stderr
  contains a helpful message (mentions `passport.json` or
  `awf-create-project`).
- `test_io_failure_exits_2` — build a legacy project, then chmod
  the project root to `0o500` (read+execute, no write) so the
  `.awf/` directory cannot be created; invoke script; assert exit
  code is 2; assert stderr contains the underlying error. Teardown
  restores `0o700` so `tmp_path` cleanup works. Use a
  `try/finally` block — bare cleanup after the assertion is not
  acceptable (lesson N2 from plan 003).

**Log integration** (plan 003 contract)

- `test_session_event_carries_composer_and_target` — invoke
  script on a legacy project; read `.awf/log.jsonl`; find the
  `session.start` event; assert `data.composer == "awf-migrate"`
  and `data.target_stage == "anchor"`.

**Test hygiene** (lessons from plans 001/002/003)

- All tests use `tmp_path` and real subprocess invocations; no
  monkeypatching of `subprocess` or `uv`.
- No test asserts the literal source text of `migrate.py`.
- Subprocess `env` is built explicitly (`{**os.environ,
  "AWF_HOME": str(repo_root)}`) — do **not** rely on
  `os.environ` mutation, which leaks across tests.
- Type annotations on every test helper.

## Review

### Pass 1 (2026-05-31)

**T1 — exit codes 0/1/2 split.** Accepted as written. Spec AC3 says "non-zero only on I/O failure," which is satisfied: both code 1 (no project) and code 2 (I/O) are non-zero. The further split between them is an upward-compatible extension, not a contradiction — it gives composers a richer signal at no cost to spec compliance. The distinction is also well-motivated: "wrong directory" and "broken filesystem" are genuinely different recovery paths. No change required.

**T2 — `chmod 0o500` for I/O failure test.** Accepted with a minor note. The approach is sound: chmod on `tmp_path` reliably blocks `.awf/` creation on macOS/Linux without requiring a mock. The plan already mandates `try/finally` to restore `0o700` (lesson N2 from plan 003). One residual risk: tests running as root (e.g., in some CI containers) will not see a permission error. Recommend adding a `pytest.importorskip`-style skip (`if os.getuid() == 0: pytest.skip("root bypasses chmod")`). This is a test hygiene note, not a blocking finding.

**Overall verdict: accept.** The plan is well-scoped and tightly coupled to the locked implementation in `lib/project.py`. The pre-call probe design decision is sound and TOCTOU risk is correctly acknowledged and dismissed. The `log.session` wrapping matches plan 003's ContextVar threading. The `awf-doctor` style template is faithfully referenced and the plan's anatomy maps cleanly onto it. The seven acceptance criteria are traceable 1:1 to spec AC1/AC2/AC3 and plan-specific requirements. Test surface covers happy paths, error paths, and log integration.

**Top findings (non-blocking):**

1. The `AWF_HOME` bootstrap line in step 3 of implementation has a parenthesis asymmetry in the prose snippet (`Path(os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]).resolve()`) — `parents[3]` is correct for a script at `skills/awf-migrate/scripts/migrate.py` (depth 3 from repo root), but implementer should verify depth against the actual directory layout before committing.
2. The plan says the session context manager "catches the re-raised exception, marks the session `'fail'`" — but the `ProjectNotFound` handler exits before the `with log.session` block is entered (step 4.1 exits 1 before step 4.3 opens the session). This is correct behaviour, but the prose in the "Design decision — wrap the whole run in `log.session`" section is slightly misleading when it says the session context manager catches the not-found case. Implementer should be aware that the not-found exit is deliberately *outside* the session boundary.
3. In `--json` mode, the output schema includes `"domain"` and `"slug"` — both require `passport.json` to be readable. On a no-op run (anchor already present), the anchor itself carries `domain` and `slug`, so this is fine. Worth a comment in the code confirming the source.

### Pass 1 (2026-05-31) — code review

**Verified:** 74/74 tests green (`uv run --with pytest --with pydantic pytest tests/ -v`). Ruff clean (`uv tool run ruff check skills/awf-migrate/scripts/migrate.py tests/skills/test_awf_migrate.py`). Diff is exactly five files: plan, SKILL.md, migrate.py, tests/__init__.py, test_awf_migrate.py.

**Plan note T2 (root-user skip):** Implemented correctly — `test_io_failure_exits_2` opens with `if os.getuid() == 0: pytest.skip(...)`. Plan note 1 (parents[3] depth): verified correct in code (comments in script confirm the path depth). Plan note 2 (ProjectNotFound outside session): `find_project_root()` call is before the `with log.session` block; code is correct, comment on line 63–64 explicitly documents this. Plan note 3 (domain/slug source on no-op): comment on lines 98–100 confirms the source.

**M1 — `sys.exit(2)` inside `with log.session` silently logs success on I/O failure (Major).** `log.session`'s except clause catches `Exception` (line 412 of `log.py`), not `BaseException`. `sys.exit(2)` raises `SystemExit`, a `BaseException` subclass, so `summary["result"]` is never set to `"fail"`. The `finally` block runs and emits `session.end` with `result="ok"` even when the migration crashed with an I/O error. Fix: raise a plain `Exception` (or a custom `MigrationError`) inside the `with` block instead of calling `sys.exit()`, catch it at the outermost level of `main()`, and return the exit code from there. Alternatively wrap the `sys.exit` calls with a `summary["result"] = "fail"` line before each, though the re-raise approach is cleaner and matches the `log.session` contract.

**M2 — `e.get("event")` field-name typo in `test_already_migrated_is_noop` baseline (Major — test incorrectly verifies spec AC1).** Line 150 computes `state_changes_before` using `e.get("event") == "state.change"`. Every event record in `log.py` uses the key `"type"`, never `"event"`, so this expression always evaluates to `False` and `state_changes_before` is always `0`. The assertion on line 165 (`state_changes_after == state_changes_before`) reduces to `state_changes_after == 0`, which passes — but only because the no-op happens to emit zero new `state.change` events. The baseline count is meaningless; if the first `_make_migrated_project` call produced `state.change` events (which it does, via `ensure_anchor`), they are not counted. The test is verifying spec AC1 by accident, not by design. Fix: change line 150 to `e.get("type") == "state.change"` (matching every other usage in the file).

**Checklist results:**
- Acceptance criteria coverage: all seven ACs have verifying tests. M2 weakens AC1 verification but does not eliminate it.
- A1 idempotency: `ensure_anchor` idempotency preserved at the process boundary. Pre-call probe is correctly placed. No-op path confirmed.
- A11 resumability: session.start + session.end emitted on every invocation; confirmed by `test_session_event_carries_composer_and_target`. M1 means the `session.end` `result` field is incorrect on I/O failure, but the session boundary itself is always written.
- `--json` mode: both no-op (`test_json_flag_noop_emits_no_op_action`) and migrated (`test_json_flag_emits_structured_output`) cases are tested and green.
- Three plan Pass-1 notes: all addressed in code (parents[3], ProjectNotFound boundary, domain/slug comment).
- `log.session` wrapping: present and correct for the happy path; broken for the I/O failure path (M1).
- `state.change` emission: emitted by `ProjectAnchor.save()` inside `ensure_anchor`; not duplicated in the skill script. Correct.

**Verdict: not accepted.** Two Majors block acceptance. Both are small, surgical fixes.

### Pass 2 (2026-05-31) — code review

**Verified:** 75/75 tests green (`uv run --with pytest --with pydantic pytest tests/ -v`). Diff covers exactly two files beyond the plan itself: `skills/awf-migrate/scripts/migrate.py` and `tests/skills/test_awf_migrate.py`.

**M1 fixed.** `MigrateIOError(RuntimeError)` is now raised inside the `with log.session` block; the outer `try/except MigrateIOError` catches the re-raise and returns exit code 2. `log.session`'s `except Exception` guard fires correctly, setting `result="fail"` on `session.end`. New regression test `test_io_failure_session_end_records_fail_result` explicitly asserts `session.end.result == "fail"` and `data.summary == "fail"` (with a correct guard for the case where the log dir itself is unwriteable — valid edge, not a gap). Test passes.

**M2 fixed.** All `e.get("event")` references are now `e.get("type")` throughout the test file (grep confirms zero occurrences of `"event"` as a key lookup). `test_already_migrated_is_noop` line 150 now correctly counts pre-existing `state.change` events by `"type"`, making the AC1 no-new-state.change assertion meaningful rather than accidental.

**No new issues.** Code is clean: `MigrateIOError` docstring precisely explains the contract; output block moved outside the session (correct — no I/O work after `ensure_anchor` returns); `--json` no-op path still reachable since `anchor` is in scope when `MigrateIOError` is not raised; `return 0` outside the `try` block is unreachable after `return 2` on error, which is correct. No ruff or structural issues observed.

**Verdict: accepted.**

## Status log

- 2026-05-31  Lead — created (draft).
- 2026-05-31  Reviewer — pass 1 complete; accepted.
- 2026-05-31  Dev — implemented: SKILL.md, scripts/migrate.py, tests/skills/test_awf_migrate.py. 74/74 tests green, ruff clean. `parents[3]` verified correct for repo root depth. ProjectNotFound exits before log.session opens (deliberately outside session boundary per plan prose). --json no-op sources domain/slug from anchor (comment in code). pytest.skip guard added for root user in I/O failure test.
- 2026-05-31  Reviewer — code review pass 1: not accepted. Two Majors: sys.exit(2) inside log.session emits session.end result="ok" on I/O failure (M1); e.get("event") typo in test baseline means AC1 is verified by accident not by design (M2).
- 2026-05-31  Dev — addressed code review pass 1: M1 (session-aware error path) replaced sys.exit(2) inside with-block with MigrateIOError raised inside log.session, caught outside; M2 (typo) fixed e.get("event") to e.get("type"). New test test_io_failure_session_end_records_fail_result verifies session.end.result=="fail" on I/O failure. 75 tests; all green.
- 2026-05-31  Reviewer — code review pass 2: accepted. M1 and M2 both correctly fixed; 75/75 tests green; no new issues.
