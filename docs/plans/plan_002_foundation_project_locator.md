# Plan 002 — Foundation: project locator dual-walk

**Status:** ready
**Phase:** A
**Spec refs:** spec.md §A2, decisions.md D-004 (primary); D-001 §4, D-003 (context); 01-principles.md A7
**Owner (current):** Lead
**Created:** 2026-05-31
**Updated:** 2026-05-31

## Goal

Extend `lib/project.py` so it can find a project rooted by **either**
`.awf/project.json` (the D-001/D-003 anchor) **or** `passport.json`
(the legacy S1 contract), preferring the former when both are
present. Surface `anchor_missing` to callers that need to know the
difference (composers, `awf-migrate`, `awf-status`). Add an
idempotent `ensure_anchor()` that mints `.awf/project.json` from
`passport.json` fields on legacy projects — the library half of the
D-004 migration. The `awf-migrate` skill (plan 004) will be the
explicit one-shot UX; this plan ships only the library it calls.

This is the second of three Phase-A library plans (after plan_001
state schema, before plan_003 log lib). It unblocks plan_003 (the
logging library needs the locator) and plan_004 (`awf-migrate` is a
thin wrapper over `ensure_anchor`).

## Context

### Required reading for the implementer

- `docs/spec.md` §A2 — the scope this plan implements (4 acceptance
  bullets, plus this plan's additions).
- `docs/decisions.md` D-004 — the dual-walk migration ADR. Key
  invariants: prefer `.awf/`, append-only migration, no passport
  rewrite, `awf-migrate` is the explicit UX.
- `docs/decisions.md` D-003 — locks the `.awf/project.json` schema
  that `ensure_anchor()` mints. Implemented in `lib/state.py` as of
  plan_001.
- `docs/decisions.md` D-001 §4–§5 — why the project anchor exists
  alongside `passport.json` rather than replacing it.
- `docs/07-multi-stage-architecture.md` — file layout, migration
  semantics. Note: the `.awf/` directory is the new spine; the
  passport remains the S1/S2 landing-page contract.
- `docs/01-principles.md` — especially A7 (project locator: walk up
  from cwd; never assume), A1 (idempotency).
- `lib/project.py` — current implementation. Single-walk
  (`passport.json` only); returns `Path | None`; raises
  `ProjectNotFound`.
- `lib/state.py` — plan_001 output. `ProjectAnchor.load()`,
  `Infra.load()`, `Infra.load_or_create()` all call
  `find_project_root(start)` and treat the return as `Path` (with
  `assert root is not None` guards — see "Design decision" below).
- `lib/passport.py` — what landing-page skills currently expect from
  the project (read `Passport.load()` and the slug/domain fields
  that `ensure_anchor` must copy into `.awf/project.json`).
- `docs/plans/plan_001_foundation_state_schema.md` Review §
  (Pass 1, Pass 2, code review Pass 1) — flagged the type-annotation
  gap addressed by this plan; established the
  `re-raise-with-augmented-message` pattern for `ProjectNotFound`.

### Design decision — return-type shape

The spec says `find_project_root()` should "flag
`anchor_missing=True` on the returned object." Three viable shapes
were considered:

1. **Return a dataclass `ProjectRoot(path: Path, anchor_missing:
   bool)`** — cleanest semantically but breaks every caller, including
   the freshly-merged `lib/state.py` which destructures the return as a
   `Path`.
2. **Keep `find_project_root(...) -> Path` (drop the `Optional`)
   and add `find_anchor_state(start: Path | None = None) -> tuple[Path,
   bool]` as the richer entry point.** Backwards-compatible. Callers
   that need `anchor_missing` (composers, migrate, status) call the
   new function; everyone else (existing landing-page skills,
   `lib/state.py`) keeps working unchanged.
3. **Subclass `Path` with an `anchor_missing` attribute.** Rejected
   — `Path` subclassing is fragile across Python versions and breaks
   `isinstance(x, Path)` patterns in unpredictable ways.

**Chosen: Option 2.** Rationale: plan_001 just shipped a fragile
`assert root is not None` workaround in three call sites. Option 1
forces a third change to those call sites within a week of their
landing; Option 2 lets us fix the type-annotation gap (by dropping
`Optional[Path]` from the non-optional path) without touching
`lib/state.py` at all. The `find_anchor_state()` name reads cleanly
at the call site (`root, anchor_missing = find_anchor_state()`) and
deliberately does not return a dataclass — a 2-tuple is enough for
two fields, and stays trivially unpacked in the migration shim.

If a third field is ever needed (e.g., which file matched), we
promote `find_anchor_state` to return a small dataclass at that
point; not now.

### Design decision — `find_project_root(optional=True)` semantics

Today: `optional=True` returns `Path | None`; `optional=False` (the
default) raises or returns a `Path`. The signature annotation
collapses both branches into `Path | None`, which is why
`lib/state.py` needs `assert root is not None` guards.

We **keep `optional=True` in place** (callers like `awf-doctor` and
`awf-status` rely on it for "am I in a project?" checks) but split
the signature into two `@overload`s so mypy can narrow each branch:

```python
@overload
def find_project_root(start: Path | None = ..., *, optional: Literal[False] = ...) -> Path: ...
@overload
def find_project_root(start: Path | None = ..., *, optional: Literal[True]) -> Path | None: ...
def find_project_root(start: Path | None = None, *, optional: bool = False) -> Path | None:
    ...  # body unchanged in shape
```

This is the cleanest fix for the gap N2 surfaced in plan_001's code
review (state.py lines 221, 386, 429). Dev removes those three
`assert root is not None` lines as part of this plan (they become
provably unreachable, not just runtime-correct).

### Design decision — `ensure_anchor` signature

`ensure_anchor(root: Path) -> ProjectAnchor`. Takes an explicit root
(no implicit `find_project_root()` call inside) so callers control
the start path and the contract is testable without `tmp_path`
cwd-mangling. Returns the (loaded or newly-created) anchor so the
caller can immediately use it. Idempotent: a no-op if
`.awf/project.json` already exists.

Migration data flow on a passport-only project:

1. Read `passport.json` → extract `domain` and `slug`.
2. Construct `ProjectAnchor(awf_version, domain, slug,
   stage="landing", created=now_utc(), has=HasFlags(passport=True))`.
3. Call `anchor._path = root / ".awf" / "project.json"` then
   `anchor.save()`. `_save_impl` handles atomic write + log event.
4. Return `anchor`.

`now_utc()` is a tiny inline helper:
`datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`. Not
worth extracting to `lib/`.

## Out of scope

- `awf-migrate` skill (plan 004). This plan provides the library
  function it calls; the SKILL.md and CLI surface are plan 004.
- Implicit-migration call sites from composer skills (plan_018 and
  the Phase-B skills). Each composer wires `ensure_anchor()` into
  its own first-run logic; that's per-composer business, not part of
  the library plan.
- Touching `lib/state.py`. The type-annotation tightening here makes
  the three `assert root is not None` lines provably unnecessary;
  removing them is included here as a single follow-up edit to
  state.py (≤3 line deletions). No state-schema changes.
- Touching `lib/passport.py`. We **read** `Passport` inside
  `ensure_anchor`; we never mutate it.
- Migrating existing landing-page skills to use `find_anchor_state`.
  They continue calling `find_project_root` and get back `Path`
  exactly as before. No skill-side changes.
- Any change to `~/.config/awf/shared.json` (D-003 §3 — that's
  user-scope, not affected by per-project migration).
- Logging library itself (plan_003). `ensure_anchor()`'s `state.change`
  emission goes through the import-safe shim
  `_emit_state_change` already inside `lib/state.py`
  (`_save_impl`). No new logging call sites here.

## Dependencies

- **Plan 001 accepted (it is, commit 4a27e7e on `main`).** Specifically:
  `ProjectAnchor` class, `HasFlags`, `_atomic_write_json`,
  `_save_impl`, `_emit_state_change` shim, and the
  `find_project_root` re-raise-with-augmented-message contract
  honored by `ProjectAnchor.load()`.
- **No new external credentials or env vars.** Pure stdlib + Pydantic
  v2 (already a project dep via plan_001).
- **No remote API calls.** Filesystem-only.

## Implementation steps

Numbered, fine-grained. Each step is independently verifiable.

1. **Add the dual-walk to `find_project_root`.** Change the inner
   match condition from `(parent / PASSPORT_FILENAME).is_file()` to:
   *either* `(parent / AWF_DIRNAME / PROJECT_FILENAME).is_file()`
   *or* `(parent / PASSPORT_FILENAME).is_file()`. **Locked choice
   (per N2):** define `AWF_DIRNAME = ".awf"` and
   `PROJECT_FILENAME = "project.json"` as module-level constants in
   `lib/project.py` (mirroring the existing `PASSPORT_FILENAME`
   constant). `lib/state.py` continues to use its own copies
   unchanged — no edit to `lib/state.py`'s constant declarations
   here; the two modules will hold identical string values, which
   is acceptable for two reasons: (a) `lib/state.py` is the
   schema-owner and `lib/project.py` is the locator, so they
   legitimately know about the same path independently; (b) any
   future reconciliation is a one-line refactor. Dev does NOT
   import these constants from `lib/state.py` for the walk — that
   would re-introduce the cycle this plan exists to avoid.

2. **Augment the "not found" error message** to list the directories
   walked (spec A2 bullet 4 requires it). Build the walked list as
   `[here, *here.parents]` was traversed; include the count and the
   topmost dir walked. Existing message tail ("Run `awf-create-project`…")
   is preserved.

3. **Add `@overload` signatures for `find_project_root`** per the
   "Design decision — `find_project_root(optional=True)` semantics"
   section above. Run `mypy --strict lib/project.py` to confirm the
   non-optional branch narrows to `Path`.

4. **Add `find_anchor_state(start: Path | None = None) -> tuple[Path,
   bool]`.** Returns `(root, anchor_missing)`. Implementation:

   ```python
   def find_anchor_state(start: Path | None = None) -> tuple[Path, bool]:
       root = find_project_root(start)
       anchor = root / AWF_DIRNAME / PROJECT_FILENAME
       return root, not anchor.is_file()
   ```

   That's it. Raises `ProjectNotFound` via `find_project_root` on a
   project-less cwd; otherwise always returns the tuple.

5. **Add `ensure_anchor(root: Path) -> ProjectAnchor`.** Per the
   "Design decision — `ensure_anchor` signature" section. Steps:

   a. If `(root / AWF_DIRNAME / PROJECT_FILENAME).is_file()`, return
      `ProjectAnchor.load(start=root)` and exit (idempotent no-op).
   b. Else, require `(root / "passport.json").is_file()` — if neither
      exists, raise `ProjectNotFound(f"...neither anchor nor passport
      at {root}")`. (Belt-and-braces; `find_project_root` should have
      caught this, but `ensure_anchor` is a public API and may be
      called with an arbitrary `root`.)
   c. Load the passport via `lib.passport.Passport.load(root)`.
      *Confirmed (per N5):* `Passport.load` is a classmethod with
      signature `load(cls, project_root: Path) -> "Passport"` —
      see `lib/passport.py:92`. Call form locked: `passport =
      Passport.load(root)`. Read `passport.domain` and
      `passport.slug`.
   d. Construct the `ProjectAnchor` with `stage="landing"`,
      `has=HasFlags(passport=True)`, `created=now_utc()`,
      `awf_version=AWF_VERSION`.
   e. Set `anchor._path = root / AWF_DIRNAME / PROJECT_FILENAME`;
      call `anchor.save()`. `_save_impl` validates, atomic-writes,
      and emits the `state.change` event via the existing shim.
      *Note (per N3):* this is the same private-attr assignment used
      in `ProjectAnchor.load()` — established deviation per plan_001
      code review N3, accepted because `ProjectAnchor` has no public
      path property. Not a bug; do not refactor here.
   f. Return the anchor.

   `ensure_anchor` lives in `lib/project.py` (not `lib/state.py`) to
   keep the migration entry point co-located with the locator.
   `lib/project.py` already depends on the stdlib + (after this plan)
   `lib.state` and `lib.passport`, which is acceptable: this module
   is the project boundary.

6. **Module-level imports (Option A, locked).** `lib/state.py`
   imports `ProjectNotFound, find_project_root` from `lib/project.py`.
   Adding a reverse module-level edge (`lib.project` → `lib.state`)
   creates a cycle. Resolution:

   - `find_project_root` and `find_anchor_state` use the module-level
     `AWF_DIRNAME` / `PROJECT_FILENAME` constants defined directly in
     `lib/project.py` per step 1. **No import from `lib.state`.**
   - `ensure_anchor` needs `ProjectAnchor`, `HasFlags`, and
     `AWF_VERSION` from `lib.state` and `Passport` from
     `lib.passport`. These are **deferred imports inside the function
     body** of `ensure_anchor`. Add a one-line comment at the import
     site: `# deferred import: avoid cycle with lib.state at module import`.

   This is the same shim pattern accepted by plan_001's
   `_emit_state_change` and noted as legitimate in this plan's T2
   tension review. No edit to `lib/state.py`'s import surface; the
   only `lib/state.py` change in this plan is the three
   `assert root is not None` deletions in step 7.

7. **Remove the three `assert root is not None` lines from
   `lib/state.py`** at the locations identified in plan_001's code
   review N2 (currently lines 221, 386, 429). The new `@overload`
   on `find_project_root` makes them provably unreachable; mypy
   will type-narrow without them. Re-run `mypy --strict lib/state.py
   lib/project.py` — both must pass.

8. **Update `docs/spec.md` lines 68 and 115**: change the two
   `ProjectNotFoundError` references to `ProjectNotFound` (the
   actual class name in `lib/project.py`). One-line doc fix per
   site; in scope because the misnamed type is in this plan's spec
   section and the inconsistency will confuse future plan reviewers.

9. **Update the `__main__` block** at the bottom of `lib/project.py`
   to print both the root and the `anchor_missing` flag, so
   `python -m lib.project` remains a useful one-shot diagnostic:

   ```python
   root, anchor_missing = find_anchor_state()  # or wrap in try
   print(f"{root}  anchor_missing={anchor_missing}")
   ```

   On `ProjectNotFound`, print `"(not in a project)"` exactly as
   today (preserve existing UX).

10. **Run the full Phase-A test suite.** `pytest tests/lib/test_state.py
    tests/lib/test_project.py -v`. All plan_001 tests must continue to
    pass unchanged. New tests (step below) all green. `ruff check
    lib/project.py tests/lib/test_project.py` clean. `mypy --strict
    lib/project.py lib/state.py` clean.

## Acceptance criteria

Verbatim from spec.md §A2 (the four bullets):

- [ ] Legacy project (passport only): `find_anchor_state()` returns
      `(root, True)`, and `find_project_root()` returns `root`
      (plain `Path`, no structural change to its return shape).
  *Verification:* the spec's "`anchor_missing=True` on the returned
  object" requirement is delivered via the new `find_anchor_state()`
  entry point — see "Design decision — return-type shape." Test:
  `tmp_path/passport.json` exists, no `.awf/`. Assert
  `find_anchor_state(tmp_path)` returns `(tmp_path, True)` and
  `find_project_root(tmp_path) == tmp_path`.

- [ ] After `ensure_anchor()`: `.awf/project.json` exists with
      `stage="landing"`, `has.passport=true`.
  *Verification:* Test: passport-only project; call
  `ensure_anchor(tmp_path)`; assert file exists, JSON parses,
  `stage == "landing"`, `has.passport is True`. Domain and slug
  copied verbatim from passport.

- [ ] New project (both files): `.awf/project.json` is preferred.
  *Verification:* Test: `tmp_path` with both files; `find_anchor_state`
  returns `anchor_missing=False`; the root selected by
  `find_project_root` is the one containing both. (Preference matters
  only when the two files diverge across directory levels — covered
  by a separate test where the anchor is in a parent and the passport
  is in a child, or vice versa: the **closest** `.awf/project.json`
  wins, falling back to the closest `passport.json` only if no
  `.awf/` is found on the walk.)

- [ ] No project (neither file): raises `ProjectNotFound` with
      message including cwd and the directories walked.
  *Verification:* Test: empty `tmp_path` (no parent within tmp has
  either file). Assert `ProjectNotFound` is raised; assert the
  exception message contains `str(tmp_path)` and at least one parent
  path.

Plan-specific additions:

- [ ] `ensure_anchor()` is idempotent: calling it twice on the same
      passport-only project results in no second `state.change`
      emission, and `.awf/project.json` is byte-identical between
      the two calls. The second call is a no-op read (the
      `.awf/project.json`-exists branch returns
      `ProjectAnchor.load(start=root)` without re-creation), so the
      `created` timestamp written on the first call is preserved
      verbatim.
- [ ] `ensure_anchor()` emits exactly one `state.change` log event on
      first call, via `ProjectAnchor.save()` → `_save_impl` →
      `_emit_state_change`. (Verified by spying on `lib.log.state_change`
      — the same hook plan_001's tests use.)
- [ ] `mypy --strict lib/project.py` passes (with the new `@overload`s).
- [ ] `mypy --strict lib/state.py` passes **without** the three
      `assert root is not None` lines (deleted in step 7).
- [ ] All existing `lib/state.py` callers (e.g., `ProjectAnchor.load`,
      `Infra.load`, `Infra.load_or_create`) continue to pass plan_001's
      tests unchanged. No edits to `tests/lib/test_state.py` are
      necessary or permitted.
- [ ] All existing landing-page skill scripts that import
      `find_project_root` continue to compile. (Grep:
      `grep -rn "find_project_root" skills/` — every match still
      receives a `Path` from a single-positional call. No skill code
      edited.)
- [ ] `docs/spec.md` lines 68 and 115 read `ProjectNotFound` (not
      `ProjectNotFoundError`).

## Tests required

`tests/lib/test_project.py` (new). All tests use `tmp_path`; none
mutate cwd. Mirrors `tests/lib/test_state.py`'s style.

**Dual-walk discovery:**

- `test_find_project_root_anchor_only` — `tmp_path/.awf/project.json`
  with minimal valid JSON; no `passport.json`. `find_project_root(tmp_path)`
  returns `tmp_path`. `find_anchor_state(tmp_path)` returns
  `(tmp_path, False)`.
- `test_find_project_root_passport_only` —
  `tmp_path/passport.json`; no `.awf/`. `find_project_root(tmp_path)`
  returns `tmp_path`. `find_anchor_state(tmp_path)` returns
  `(tmp_path, True)`.
- `test_find_project_root_both_present` — both files at `tmp_path`.
  `find_anchor_state` returns `anchor_missing=False`.
- `test_find_project_root_neither` — empty `tmp_path`.
  `pytest.raises(ProjectNotFound)`; assert message contains
  `str(tmp_path)` and at least one parent path string.
- `test_find_project_root_passport_wins_when_closer` — the closest
  match on the walk wins, even when that closest match is a
  `passport.json` and an anchor exists further up. Setup:
  `tmp_path/.awf/project.json`, `tmp_path/sub/passport.json`;
  start from `tmp_path/sub/deep/`. Walk hits `tmp_path/sub` first
  (passport-only directory). Assert `find_project_root(tmp_path/sub/deep)`
  returns `tmp_path/sub`, and `find_anchor_state(tmp_path/sub/deep)`
  returns `(tmp_path/sub, True)` (closer dir has passport only —
  anchor is missing at that directory). *Lead note:* spec/D-004
  says "prefer `.awf/` when both exist" — this is per-directory,
  not across-the-walk. The closest match in the walk wins;
  "preference" applies only when a single directory contains both.
  Plan locks this interpretation.
- `test_find_project_root_anchor_wins_when_closer` — the inverse:
  the closest match on the walk is an anchor, and a `passport.json`
  exists further up. Setup: `tmp_path/passport.json`,
  `tmp_path/sub/.awf/project.json`; start from `tmp_path/sub/deep/`.
  Walk hits `tmp_path/sub` first. Assert
  `find_project_root(tmp_path/sub/deep)` returns `tmp_path/sub`,
  and `find_anchor_state(tmp_path/sub/deep)` returns
  `(tmp_path/sub, False)`. Confirms first-match-wins is symmetric
  across the two file types.
- `test_find_project_root_both_files_same_dir_prefer_anchor` —
  same directory has both files. Assert the chosen root is that
  directory and `anchor_missing=False`. (This is the per-directory
  preference rule: when one directory holds both, the anchor's
  presence is what matters — `anchor_missing` is False.)

**Overload type-narrowing:** verified by Reviewer via
`mypy --strict`; no runtime test required (acceptance criterion
covers it).

**`ensure_anchor`:**

- `test_ensure_anchor_idempotent_when_anchor_exists` — pre-create
  `.awf/project.json`; call `ensure_anchor(tmp_path)`; file
  unchanged on disk (mtime equal or content byte-identical). Returns
  the loaded anchor.
- `test_ensure_anchor_migrates_from_passport` — passport-only
  project. Call `ensure_anchor(tmp_path)`. Assert
  `.awf/project.json` now exists, parses as valid JSON, has
  `stage == "landing"`, `has.passport is True`, `domain` and `slug`
  matching the passport.
- `test_ensure_anchor_idempotent_after_migration` — call twice. The
  second call returns a `ProjectAnchor` with the same `created`
  timestamp as the first; file content is byte-identical between
  the two calls.
- `test_ensure_anchor_emits_state_change_once` — monkeypatch
  `lib.log.state_change` (or a dedicated test double matching
  plan_001's approach in `test_state.py`); call `ensure_anchor`
  twice. Assert the spy was called exactly once on the first call,
  not at all on the second.
- `test_ensure_anchor_raises_on_empty_directory` — empty `tmp_path`;
  call `ensure_anchor(tmp_path)`. Assert `ProjectNotFound`.

**Regression:** the existing plan_001 test suite
(`tests/lib/test_state.py`) must continue to pass without
modification.

## Status log

- 2026-05-31  Lead — created (draft).
- 2026-05-31  Reviewer — plan review pass 1: changes-requested. No blockers; one Major (inverted test name M1 would cause Dev to write wrong assertion); four Minors; all three Lead-flagged tensions accepted.
- 2026-05-31  Lead — revised per Pass 1 feedback: M1 resolved, N1-N5 applied.
- 2026-05-31  Reviewer — plan review pass 2: ready. All Pass 1 issues resolved; no regressions; safe for Dev to start.

## Review

### Pass 1 (2026-05-31)

**Lead-flagged tensions:**

- T1 (closest-match-wins): **accept.** D-004's "prefer `.awf/` when both exist" contains no cross-directory ordering rule. The natural reading of a walk-up locator is first-match-wins, with per-directory preference applied when a single directory contains both files. The plan's interpretation is consistent with D-004 and removes all ambiguity about the cross-directory case by locking it explicitly. The two dedicated tests (`test_find_project_root_anchor_preferred_when_closer` and `test_find_project_root_both_files_same_dir_prefer_anchor`) cover both cases. See Major M1 below for a naming defect in the first of those tests.

- T2 (deferred imports): **accept.** `coding_principles.md` does not prohibit deferred imports. Precedent is set and accepted in `lib/state.py`'s `_emit_state_change` shim (plan_001 code review accepted it). Option A keeps `lib/state.py` untouched beyond the three `assert` deletions, consistent with the plan's explicit scope boundary. The one-line comment requirement ("avoid cycle with lib.state at module import") is correctly included. No new pattern is introduced.

- T3 (spec-doc in scope): **accept.** The stale `ProjectNotFoundError` references (spec.md lines 68, 115) were flagged in plan_001's code review N1 as "no code change needed — documentation only," but no plan was assigned to fix them. They live in spec §A2 — the exact section this plan implements. Leaving them unresolved misleads future plan reviewers and any Dev who reads only `spec.md` before writing plan_003 or plan_004. Two-line doc fix; bounded; no code impact. Legitimate scope.

**Blockers:**

- none.

**Major:**

- **M1 — `test_find_project_root_anchor_preferred_when_closer` has an inverted name.** The test fixture places `.awf/project.json` at `tmp_path/` and `passport.json` at `tmp_path/sub/`. The walk starts at `tmp_path/sub/deep/` and stops at `tmp_path/sub` (passport wins because it is closer). The test comment and Lead note confirm this — the result is the passport winning. But the test name says "anchor preferred when closer," implying the anchor wins. A Dev reading the test name in isolation will write the wrong assertion. Rename to `test_find_project_root_passport_wins_when_closer` (or equivalent) to match the described fixture and result.

**Minor / nits:**

- **N1 — Acceptance criterion wording copies spec literally without noting the design divergence.** The first criterion bullet reads "`find_project_root()` returns root with `anchor_missing=True`." The implementation delivers this via `find_anchor_state()`, not `find_project_root()`. The verification note explains it, but the criterion header is misleading for a Dev reading the list quickly. Recommend amending to: "Legacy project (passport only): `find_anchor_state()` returns `(root, True)`, and `find_project_root()` returns `root` (plain `Path`, no flag)." This removes any reading that `find_project_root` needs a structural change beyond the dual walk.

- **N2 — Step 1 / step 4 constants: plan does not lock which approach Dev must use for `AWF_DIRNAME` and `PROJECT_FILENAME` in the non-`ensure_anchor` functions.** Step 6 (module-level imports) documents Option A (deferred inside `ensure_anchor` only) and says for the other functions "use a local constant string `.awf/project.json` (or the constants defined directly in `lib/project.py`, mirroring `lib/state.py`)." Two competent Devs will diverge: one defines module-level string constants `AWF_DIRNAME = ".awf"` / `PROJECT_FILENAME = "project.json"` in `lib/project.py`; another uses raw string literals in the walk condition. Both are correct in behaviour but produce different module surfaces. Lock the choice: recommend module-level constants in `lib/project.py` (mirrors the `PASSPORT_FILENAME` constant already there, and keeps the walk condition readable). This is a one-sentence addition to step 1.

- **N3 — Step 5e sets `anchor._path` via direct private-attr mutation without noting this is the established pattern.** plan_001 code review N3 documented this as "acceptable deviation forced by the lack of a public path property." The same deviation appears here. A Dev encountering `anchor._path = ...` for the first time may consider it a bug. A one-line comment in step 5e ("same private-attr assignment as in `ProjectAnchor.load()` — established deviation per plan_001 N3") prevents confusion and keeps the deviation documented at the call site.

- **N4 — `ensure_anchor` acceptance criterion "byte-identical modulo the `created` timestamp" is misleading.** On the second call, `ensure_anchor` returns early (file already exists), so `ProjectAnchor.load()` is called and the on-disk content is returned unchanged. The `created` timestamp is identical between the two calls, not merely "modulo" it. Suggest rewording to: "file content is byte-identical between the two calls (the second call is a no-op read; no re-creation occurs)."

- **N5 — `Passport.load(root)` API left as "Dev confirms by reading `lib/passport.py`."** Step 5c defers this lookup to the Dev. If `lib/passport.py` does not expose a `load(root)` class-method at that exact signature, the Dev will either guess or produce a different call. A quick read of `lib/passport.py` at review time would confirm or flag the signature before Dev starts. Recommend Lead confirm the call form in step 5c before Dev begins step 5.

**Verdict:** changes-requested

### Pass 2 (2026-05-31)

**Pass 1 resolution check:**

- **M1 resolved.** The inverted test name `test_find_project_root_anchor_preferred_when_closer` is renamed to `test_find_project_root_passport_wins_when_closer` (line 396). The name now matches the fixture and the stated result (passport wins because it is closer). The companion test `test_find_project_root_anchor_wins_when_closer` (line 407) correctly describes the inverse fixture. Both tests are symmetric and unambiguous. M1 is fully closed.

- **N1 resolved.** The first acceptance criterion (lines 311–319) now reads "`find_anchor_state()` returns `(root, True)`, and `find_project_root()` returns `root` (plain Path, no structural change to its return shape)." The verification note reinforces that the design divergence from spec wording is intentional and documented. No misleading implication remains.

- **N2 resolved.** Step 1 (lines 185–196) explicitly locks the approach: define `AWF_DIRNAME = ".awf"` and `PROJECT_FILENAME = "project.json"` as module-level constants in `lib/project.py`, mirroring the existing `PASSPORT_FILENAME` constant. The rationale for not importing from `lib/state.py` (cycle avoidance) is stated. Dev has no ambiguity on constant placement.

- **N3 resolved.** Step 5e (lines 245–247) adds the one-line note: "*Note (per N3):* this is the same private-attr assignment used in `ProjectAnchor.load()` — established deviation per plan_001 code review N3, accepted because `ProjectAnchor` has no public path property. Not a bug; do not refactor here." Correct and sufficient.

- **N4 resolved.** The idempotency acceptance criterion (lines 350–354) now states the second call "is a no-op read" and the `created` timestamp from the first call "is preserved verbatim." The prior misleading "byte-identical modulo the timestamp" phrasing is gone. The test `test_ensure_anchor_idempotent_after_migration` (lines 437–440) matches the updated wording.

- **N5 resolved.** Step 5c (lines 232–235) now reads: "*Confirmed (per N5):* `Passport.load` is a classmethod with signature `load(cls, project_root: Path) -> "Passport"` — see `lib/passport.py:92`. Call form locked: `passport = Passport.load(root)`." Cross-checked against `lib/passport.py` line 92, which contains exactly that classmethod signature. The claim is accurate. Dev call form is unambiguous.

**Independent verification — `lib/passport.py:92`:** `Passport.load(cls, project_root: Path) -> "Passport"` confirmed present. `ensure_anchor`'s call `Passport.load(root)` is valid.

**Regressions:** none. The plan body is otherwise unchanged from the post-Pass-1 revision. All design decision sections, out-of-scope boundaries, dependency declarations, implementation steps, and test list remain intact.

**Blockers:** none.

**Major:** none.

**Minor / nits:** none.

**Verdict:** ready
