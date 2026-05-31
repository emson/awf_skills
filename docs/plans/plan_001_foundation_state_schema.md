# Plan 001 — Foundation: `.awf/` state schemas

**Status:** accepted
**Phase:** A
**Spec refs:** [`spec.md` § A1](../spec.md#a1-awf-schemas-d-003), [`decisions.md` D-002](../decisions.md#d-002--logging-model), [`decisions.md` D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson), [`decisions.md` D-004](../decisions.md#d-004--libprojectpy-dual-walk-migration)
**Owner (current):** Reviewer
**Created:** 2026-05-31
**Updated:** 2026-05-31

## Goal

Deliver `lib/state.py`: Pydantic v2 dataclasses for the three `.awf/`
state files locked by D-003 — `ProjectAnchor` (`.awf/project.json`),
`Infra` (`.awf/infra.json`), and `Shared` (`~/.config/awf/shared.json`).
Each class provides `load()` / `save()` / `validate()` (and
`load_or_create()` where applicable), supports atomic writes,
forward-compatible deserialisation (tolerates unknown keys), enforces
cross-field invariants, and emits a `state.change` log event on every
successful save — through a hook that no-ops cleanly until `lib/log.py`
(plan 003) lands. This is the foundation every subsequent skill in
Phases B and C reads from and writes to.

## Context

- Spec: [`docs/spec.md` § A1](../spec.md#a1-awf-schemas-d-003) — public
  API, behaviour, errors, acceptance criteria.
- ADRs:
  - [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson)
    — the locked schemas (verbatim JSON in the ADR).
  - [D-002](../decisions.md#d-002--logging-model) — `state.change`
    event contract; logging never raises.
  - [D-004](../decisions.md#d-004--libprojectpy-dual-walk-migration) —
    the dual-walk `find_project_root()`; **this plan must not implement
    it** but `ProjectAnchor.load()` will call into it. Plan 002 owns
    the dual-walk; this plan uses the *current*
    [`lib/project.py`](../../lib/project.py) signature and the new
    `.awf/project.json` resolution behind a small adapter call so
    plan 002 can swap it in without changing `state.py`.
- Existing sibling: [`lib/passport.py`](../../lib/passport.py) — same
  style (dataclass, `load`/`save`/`validate`, forward-compat via
  `migrate()`), but stdlib-only. **This plan diverges**: D-003
  explicitly requires Pydantic v2. Each script that imports `lib.state`
  will declare `pydantic` in its uv-script header.
- Principles:
  - [A1 — idempotency](../01-principles.md): `load_or_create` is the
    idempotent constructor.
  - [A6 — layered config](../01-principles.md): `Shared.load_or_create`
    resolves the user-scope path via `lib.awf_home` /
    `lib.config.Config.layered()`, not a hardcoded path.
  - [A7 — project locator](../01-principles.md): `ProjectAnchor.load()`
    walks up via `find_project_root()`; never assumes cwd.
  - [A11 — resumability](../01-principles.md): atomic write + the
    `state.change` log are the substrate that lets composers resume.
- Coding/testing principles:
  [`docs/coding_principles.md`](../coding_principles.md),
  [`docs/testing_principles.md`](../testing_principles.md).
- Multi-stage layout reference:
  [`docs/07-multi-stage-architecture.md`](../07-multi-stage-architecture.md).
- Logging contract:
  [`docs/08-logging.md`](../08-logging.md) (event shape for
  `state.change`).

### Design decision: BaseModel, not `pydantic.dataclasses.dataclass`

D-003 uses the phrase "Pydantic v2 dataclasses" colloquially. This plan
uses Pydantic v2 `BaseModel` because we need `model_validate`,
`model_dump`, `model_config = ConfigDict(extra="allow")` for
forward-compat, and `PrivateAttr` for `_path` stashing.
`pydantic.dataclasses.dataclass` does not give us `extra="allow"`
ergonomics or `PrivateAttr` cleanly. Lead has logged this divergence
here rather than as a new ADR because the substance of D-003
(Pydantic-v2-based models with the documented JSON shapes) is
unchanged.

## Out of scope

- **Plan 002** — `lib/project.py` dual-walk for `.awf/project.json`
  OR `passport.json` (D-004). This plan calls
  `find_project_root()` as it is today; plan 002 extends it. We
  introduce no new code in `lib/project.py`.
- **Plan 003** — `lib/log.py` (D-002, spec A3). This plan defines the
  *hook surface* (`_emit_state_change`) and exercises it in tests via
  a fake hook, but does not implement `lib.log`. The real implementation
  is wired in plan 003.
- **Plan 004** — `awf-migrate` skill. This plan creates no skill, no
  SKILL.md, no scripts/ entry point. It only ships the library
  primitives those plans will consume.
- **Passport migration.** Reading or rewriting `passport.json` is out
  of scope. The two coexist (D-001 §5, D-004).
- **`state.change` log event semantics beyond emission.** Where the
  event is written, redaction, file-locking, ULID, diff computation,
  and size-capping — all owned by plan 003 / `lib/log.py`. The shim
  in this plan passes raw `before`/`after` dicts to `log.state_change`
  and does no diffing or capping itself.

**One small addition outside `lib/state.py`:** this plan adds a single
helper `user_config_dir()` to `lib/awf_home.py` (see step 7a). That is
the only file outside `lib/state.py` and `tests/lib/test_state.py` the
Dev touches. This is not credential layering (A6 governs credentials,
not the FHS-ish user config dir, which is a fixed location).

## Dependencies

- **Soft / temporal:** Plan 002 (`lib/project.py` dual-walk) logically
  *depends on* plan 001, not the other way round. Plan 001 ships
  first.
- **Decoupling for plan 003 (logging):** because `lib.log` does not
  exist yet, this plan defines a `_emit_state_change(event)` shim in
  `lib/state.py` that:
  1. Attempts `from lib import log` lazily, inside the function body.
  2. On `ImportError` (log module absent), no-ops silently.
  3. On any exception from the log call, swallows + emits a stderr
     warning (D-002 op rule: logging never raises).
  When plan 003 lands, `lib.log` becomes importable and the shim
  starts emitting real events with no change to `state.py`.
- **External / user prerequisites:** none. Pydantic v2 is added to the
  uv-script headers of any script that imports `lib.state`. No new
  credentials, no new env vars.
- **Code prerequisites:** `lib/awf_home.py` (exists),
  `lib/project.py` (exists, current single-walk version is fine).

## Implementation steps

Each step is small enough to verify independently. Steps 1–6 and 8
are the `lib/state.py` body; step 7 is the small `lib/awf_home.py`
addition; step 9 is shared `_save_impl` cross-cutting; step 10 is the
test surface; step 11 is the documentation touch.

1. **Add module skeleton** `lib/state.py` with module docstring
   citing D-003 and stating the Pydantic-v2 divergence from
   `lib/passport.py`. Declare module constants:
   ```python
   AWF_DIRNAME = ".awf"
   PROJECT_FILENAME = "project.json"
   INFRA_FILENAME = "infra.json"
   SHARED_FILENAME = "shared.json"
   AWF_VERSION = "0.1.0"
   ```

2. **Custom exception hierarchy.** Define, in order:
   - `StateError(RuntimeError)` — base.
   - `StateCorruptError(StateError)` — malformed JSON. Wraps the
     underlying `json.JSONDecodeError`; message reports byte offset
     (from `.pos`) and the file path.
   - `StateValidationError(StateError)` — schema-invalid. Wraps
     `pydantic.ValidationError`; message lists `loc` paths and
     human-readable error strings.

   **Project-not-found reuses `lib.project.ProjectNotFound`.** Do not
   define a new exception class for this in `lib/state.py`. Import
   `ProjectNotFound` from `lib.project`. **Propagation policy (locked):**
   when `load*` catches `lib.project.ProjectNotFound`, re-raise as
   `raise ProjectNotFound(augmented_msg) from e` with `augmented_msg`
   containing the resolved start path and the directories walked. Type
   stays unchanged. The original exception is preserved as `__cause__`.
   All references in this plan and in tests use the name
   `ProjectNotFound`.

3. **Atomic-write helper** `_atomic_write_json(path: Path, data: dict)`:
   - `path.parent.mkdir(parents=True, exist_ok=True)`.
   - Write to `path.with_suffix(path.suffix + ".tmp")` in the same
     directory.
   - `os.replace(tmp, path)` for POSIX-atomic rename.
   - Use `json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False)`
     followed by a trailing newline (match `passport.py` style).
   - Let `OSError` propagate (coding principle: fail fast at edges;
     callers decide).
   - No defensive try/except around the rename; if the temp write
     succeeded the rename will succeed on every supported OS.

4. **Log hook** `_emit_state_change(file: Path, before: dict, after: dict) -> None`:
   - Calls the canonical signature locked by `08-logging.md` and
     `spec.md § A3`:
     ```python
     log.state_change(file=str(file), key="", before=before, after=after)
     ```
     `key` is `""` for a full-file save — semantically "the whole
     file changed." Per-key emissions are reserved for future
     fine-grained call sites; today the shim always emits whole-file
     saves.
   - **The shim does NOT compute a diff or cap sizes.** It hands the
     raw `before` and `after` dicts to `log.state_change`. Diff
     computation, redaction, and the 2 KB-per-side cap are owned by
     `lib/log.py` (plan 003) per D-002 and `08-logging.md`.
   - Lazy import idiom:
     ```python
     try:
         from lib import log
         log.state_change(file=str(file), key="", before=before, after=after)
     except ImportError:
         return
     except Exception as e:
         print(f"warn: log.state_change failed: {e}", file=sys.stderr)
         return
     ```
   - Confine the lazy-import idiom to this one function so the rest
     of `state.py` is import-clean.

5. **`ProjectAnchor` model** — Pydantic v2 `BaseModel` (see Context §
   "Design decision: BaseModel, not `pydantic.dataclasses.dataclass`"
   above for the rationale). Configure
   `model_config = ConfigDict(extra="allow")` to honour the
   forward-compat requirement.
   - Fields per D-003: `awf_version: str = AWF_VERSION`,
     `domain: str`, `slug: str`, `stage: Literal["landing", "demo", "mvp-play", "prescale", "scale"]`,
     `created: str` (ISO-8601 UTC),
     `has: HasFlags`.
   - Nested `HasFlags(BaseModel)` with `extra="allow"`:
     `passport: bool = False`, `infra: bool = False`,
     `kamal: bool = False`, `content: bool = False`.
   - **`@classmethod load(cls, start: Path | None = None) -> "ProjectAnchor"`**:
     1. Call `lib.project.find_project_root(start)` (current signature).
        If it raises `ProjectNotFound`, catch and re-raise per the
        propagation policy locked in step 2 (`raise
        ProjectNotFound(augmented_msg) from e`, type unchanged,
        `__cause__` preserved).
     2. Resolve `path = root / AWF_DIRNAME / PROJECT_FILENAME`.
     3. If `path` does not exist → raise `ProjectNotFound` (imported
        from `lib.project`) with the cwd and the resolved `path` in
        the message, per the policy in step 2. (Once plan 002 lands,
        the dual-walk handles the missing-anchor case; until then,
        this is the explicit error.)
     4. Read text, `json.loads` (wrap `JSONDecodeError` as
        `StateCorruptError`).
     5. `cls.model_validate(data)` (wrap `pydantic.ValidationError`
        as `StateValidationError`).
     6. Run cross-field invariants via `self.validate()`; raise on
        failure.
     7. Stash the source path on the instance via a `PrivateAttr`
        (`_path: Path | None = PrivateAttr(default=None)`); set
        `self._path = path` so `save()` round-trips to the same
        file.
   - **`save(self) -> None`**: delegates to the shared `_save_impl`
     helper (step 9). Asserts `self._path is not None`, then calls
     `_save_impl(self, self._path)`.
   - **`validate(self) -> None`**: cross-field invariants:
     - `stage == "landing"` → `has.infra is False` (D-003: infra
       appears at S3 promotion).
     - `stage == "landing"` → `has.kamal is False`.
     - `slug` matches `[a-z0-9][a-z0-9-]*` (consistent with
       passport).
     - `domain` non-empty, lowercase, contains a `.` (deliberately
       narrow — full validation is passport's job).
     - Raise `StateValidationError` listing all failures (collect, do
       not fail-fast inside `validate`; the surrounding `load`/`save`
       decides what to do).

6. **`Infra` model** — Pydantic v2 `BaseModel`, `extra="allow"` on
   every nested model. Field structure mirrors D-003 verbatim:
   - `registry: Registry` (`host: str`, `image: str`, `user: str`).
   - `hetzner: Hetzner` (`servers: list[Server]`, `lb_id: str | None`,
     `network_id: str | None`).
   - Nested `Server`: `id: str`, `ip: str`, `role: str`, `shared: bool`,
     `cost_eur_month: float`.
   - `neon: Neon` (`project_id: str`, `branch_id: str`,
     `branch_name: str`, `mode: Literal["shared-branch", "dedicated"]`,
     `connection_secret_ref: str`).
   - `kamal: Kamal` (`config_path: str`, `last_deploy_image: str | None`).
   - All fields default to safe empty values where the schema
     permits, so `load_or_create()` can mint an empty `Infra` for a
     project just entering S3.
   - **`@classmethod load(cls, start: Path | None = None) -> "Infra"`**:
     like `ProjectAnchor.load` but reads
     `root / AWF_DIRNAME / INFRA_FILENAME`. Accepts the same `start`
     parameter so tests can drive it via `tmp_path`. If the project
     root itself isn't found, applies the propagation policy from
     step 2 (catch `lib.project.ProjectNotFound`, re-raise as
     `ProjectNotFound(augmented_msg) from e` with type unchanged and
     `__cause__` preserved). Raises `FileNotFoundError` (concrete,
     not wrapped) if the project exists but `infra.json` does not —
     callers that want create-on-miss use `load_or_create`.
   - **`@classmethod load_or_create(cls, start: Path | None = None) -> "Infra"`**:
     1. Resolve `root = find_project_root(start)`; let `ProjectNotFound`
        propagate (callers must have a project context).
     2. Compute `path = root / AWF_DIRNAME / INFRA_FILENAME` as a
        local variable **before** the `try` block.
     3. `try: instance = cls.load(start=start)` — on
        `FileNotFoundError`, instantiate the empty default
        (`instance = cls()`).
     4. Set `instance._path = path` in **both** branches (the
        success branch via `load` will already have done so; assigning
        again is a no-op for correctness and makes the contract
        explicit).
     5. **Do not save** (per A1: don't materialise empty files until
        something writes; matches `Shared.load_or_create` behaviour
        and is documented in the docstring). Tests cover this
        contract.
   - **`save(self) -> None`**: same shape as `ProjectAnchor.save` —
     implemented via the shared `_save_impl` helper (step 9).
   - **`validate(self) -> None`**: minimal invariants:
     - If `hetzner.lb_id` is set, `hetzner.servers` must be non-empty.
     - If `neon.mode == "shared-branch"`, `neon.project_id` must
       match the shared play project (deferred — for now, just
       require `project_id` non-empty).
     - `registry.image` matches `f"{registry.user}/.*"` when both
       `registry.user` and `registry.image` are *truthy*
       (`bool(registry.user) and bool(registry.image)`). Empty
       strings — Pydantic's default for `str` fields — skip the
       check. This is the "both set" rule.

7. **Add `user_config_dir()` to `lib/awf_home.py`.** This is the only
   edit outside `lib/state.py` and `tests/lib/test_state.py`. Append
   the following helper to `lib/awf_home.py` (alongside
   `find_awf_home`):

   ```python
   def user_config_dir() -> Path:
       """The awf-skills user-scope config directory: ~/.config/awf.

       Exists for cross-project state (D-001 / D-003). Layered
       credential config is separate (lib.config); this is purely a
       path helper.
       """
       return Path.home() / ".config" / "awf"
   ```

   No tests required for this helper directly (it is a trivial
   expression); it is exercised through `Shared`'s tests via
   monkeypatch.

8. **`Shared` model** — Pydantic v2 `BaseModel`, `extra="allow"`.
   - Path is **user-scope**: resolve via
     `lib.awf_home.user_config_dir() / SHARED_FILENAME`. **No
     hardcoded `~/.config/awf/`** in `state.py`. Tests inject an
     alternative location with
     `monkeypatch.setattr(lib.awf_home, "user_config_dir", lambda: tmp_path / "user-awf")`.
   - Fields per D-003: `play_server: PlayServer | None`,
     `play_neon_project_id: str | None`,
     `default_registry: DefaultRegistry`.
   - Nested `PlayServer`: `hetzner_id: str`, `ip: str`,
     `hostname: str`, `registry: str`, `created: str`.
   - Nested `DefaultRegistry`: `host: str = "ghcr.io"`,
     `user: str = ""`.
   - **`@classmethod load_or_create(cls) -> "Shared"`** — note: no
     `start` parameter, because the path is user-scope and not
     project-scope. If the file is missing, instantiate default,
     set `_path = lib.awf_home.user_config_dir() / SHARED_FILENAME`;
     do not write until `.save()` is explicitly called. (No `load()`
     variant for `Shared` — it's always optional; first invocation
     is always "or create".)
   - **`save(self) -> None`**: same atomic-write + log hook, via
     `_save_impl` (step 9).
   - **`validate(self) -> None`**: if `play_server` is set, all its
     sub-fields are non-empty.

9. **Cross-cutting:**
   - **Extract `_save_impl(self, path: Path) -> None` from the
     start** (do not leave the three `save` methods as inline copies).
     Three near-identical copies exist from day one across
     `ProjectAnchor`, `Infra`, and `Shared`; the
     "don't abstract speculatively" rule is about not extracting
     before a repetition exists, not about ignoring three
     simultaneous existing copies. `_save_impl` performs:
     1. Read existing file (if any) to compute `before` dict; else
        `before = {}`.
     2. `after = self.model_dump(mode="json")`.
     3. `self.validate()`; raise on failure.
     4. `_atomic_write_json(path, after)`.
     5. `_emit_state_change(path, before, after)`.

     Each class's public `save()` simply asserts `self._path is not
     None` and delegates: `return _save_impl(self, self._path)`.
   - All three classes provide `__repr__` from Pydantic by default;
     do not customise.
   - Type hints complete; module passes `ruff check` and
     `mypy --strict` (Dev confirms by running both).

10. **Test surface** (`tests/lib/test_state.py`, one file). See
    "Tests required" below for the test list. Use real temp
    directories (`tmp_path` fixture), real files, real JSON — no
    mocks for I/O (testing principle: integration over mocks where
    feasible). The log hook is exercised by a tiny fake `lib/log.py`
    injected via
    `monkeypatch.setitem(sys.modules, "lib.log", FakeLog())` in the
    relevant tests; the rest of the test file does not touch
    logging.

11. **Documentation touch.** Update `docs/03-passport-contract.md`
    with a one-line pointer noting that `.awf/state` files are a
    separate contract (link to D-003) — **only if** the doc doesn't
    already say so. No new doc files; no SKILL.md.

## Acceptance criteria

Copied verbatim from `spec.md § A1`, plus plan-specific additions
flagged `[plan]`.

- [x] Round-trip: load → mutate → save → load yields identical state.
- [x] Forward-compat: a file with extra top-level keys loads without
      error.
- [x] `save()` produces a `state.change` log entry.
- [x] Cross-field invariants enforced (test: `stage="landing"` with
      `has.infra=true` rejects).
- [x] Atomic write: simulated mid-write crash leaves prior file
      intact.
- [x] `[plan]` Missing anchor raises `ProjectNotFound` (imported
      from `lib.project`) with cwd context in the message.
- [x] `[plan]` Malformed JSON raises `StateCorruptError` referencing
      the byte offset and the file path.
- [x] `[plan]` `Infra.load_or_create()` on a missing file returns an
      empty model and does **not** create the file on disk; the file
      is only created on the first explicit `.save()`.
- [x] `[plan]` `Shared.load_or_create()` resolves its path via
      `lib.awf_home.user_config_dir()` (no hardcoded `~/.config/awf/`
      in `state.py`).
- [x] `[plan]` `save()` calls `log.state_change(file, key, before,
      after)` — the canonical signature from `08-logging.md` — with
      `key=""` for whole-file saves and raw (uncapped, undiffed)
      `before`/`after` dicts.
- [x] `[plan]` The log hook is import-safe: if `lib.log` is absent,
      `save()` still succeeds and writes the file.
- [x] `[plan]` All three models accept and round-trip unknown
      top-level keys (Pydantic `extra="allow"`).
- [x] `[plan]` `mypy --strict lib/state.py` passes; `ruff check
      lib/state.py` passes.

## Tests required

File: `tests/lib/test_state.py`. Aligned with
[`docs/testing_principles.md`](../testing_principles.md) — behaviour
over implementation, real files via `tmp_path`, no tautological tests.

**Round-trip and equality**
- `test_project_anchor_round_trip` — write a populated anchor, reload,
  assert `model_dump()` equality. Maps to spec acceptance #1.
- `test_infra_round_trip` — same, with all D-003 fields populated.
- `test_shared_round_trip` — same, with `play_server` set.

**Forward compatibility**
- `test_anchor_tolerates_unknown_top_level_keys` — write JSON with an
  extra `"future_field": {...}`, load succeeds, save round-trips the
  key. Maps to acceptance #2.
- `test_infra_tolerates_unknown_nested_keys` — extra key inside
  `hetzner: { ... }` survives a load-save round trip.

**Validation**
- `test_anchor_rejects_landing_with_infra` — `stage="landing"` +
  `has.infra=True` raises `StateValidationError`. Maps to acceptance
  #4.
- `test_anchor_rejects_bad_slug` — `slug="Has Spaces"` raises.
- `test_infra_rejects_lb_without_servers` — `lb_id` set, empty
  `servers` → `StateValidationError`.
- `test_save_refuses_to_write_invalid_state` — mutate to invalid,
  call `save`, assert raises **and** the on-disk file is unchanged.

**Errors**
- `test_load_missing_anchor_raises_project_not_found` — empty
  `tmp_path`, call `ProjectAnchor.load(start=tmp_path)` → asserts
  `ProjectNotFound` (imported from `lib.project`) is raised, asserts
  the tmp path appears in `str(exc.value)`, AND asserts
  `exc.value.__cause__` is the original `ProjectNotFound` from
  `find_project_root` (per the step 2 propagation policy). Maps to
  plan acceptance.
- `test_infra_load_missing_project_raises_project_not_found` — empty
  `tmp_path`, call `Infra.load(start=tmp_path)` → asserts
  `ProjectNotFound` raised, the tmp path appears in
  `str(exc.value)`, AND `exc.value.__cause__` is the original
  `ProjectNotFound` from `find_project_root` (per step 2).
- `test_load_malformed_json_raises_corrupt` — write `{not json` to
  `.awf/project.json`, load → `StateCorruptError`, message contains
  byte offset and path.

**Atomic write**
- `test_atomic_write_leaves_prior_file_intact_on_crash` — populate
  the file, monkeypatch `os.replace` to raise mid-save, assert the
  original file content is unchanged on disk. Maps to acceptance #5.
- `test_atomic_write_creates_parent_dir` — `_atomic_write_json` to
  `tmp_path / ".awf" / "project.json"` when `.awf/` doesn't exist;
  succeeds; `.awf/` now exists.

**Log hook**
- `test_save_emits_state_change_via_log_hook` — install a fake
  `lib.log` via `monkeypatch.setitem(sys.modules, "lib.log", fake)`;
  `fake.state_change` records calls. Assert exactly one call per
  `save`, invoked with keyword args matching the canonical signature
  `(file=str(path), key="", before=<dict>, after=<dict>)`. Assert
  `before` is empty `{}` for a first save and is the prior on-disk
  contents for a subsequent save. Assert no `diff` kwarg is passed.
  Maps to acceptance #3.
- `test_save_when_log_unavailable_does_not_raise` — ensure
  `sys.modules["lib.log"]` is absent (or set to a module that
  raises `ImportError` on attribute access); `save()` still
  succeeds and writes the file.
- `test_log_hook_failures_become_stderr_warnings` — fake `lib.log`
  whose `state_change` raises; `save()` succeeds; `capsys.readouterr().err`
  contains "warn:".

**Load-or-create semantics**
- `test_infra_load_or_create_returns_empty_when_file_absent`.
- `test_infra_load_or_create_does_not_create_file_until_save` — call
  `load_or_create`, assert file does not exist; call `.save()`,
  assert file now exists.
- `test_shared_load_or_create_uses_awf_home` — monkeypatch
  `lib.awf_home.user_config_dir` to return `tmp_path / "user-awf"`;
  assert `Shared` reads/writes under that directory (and does not
  touch `~/.config/awf/`).
- `test_shared_load_or_create_returns_default_when_absent` — with
  the `user_config_dir` monkeypatched to an empty `tmp_path` subdir,
  call `Shared.load_or_create()`; assert no exception, the model's
  fields are their declared defaults (`play_server is None`,
  `play_neon_project_id is None`,
  `default_registry.host == "ghcr.io"`,
  `default_registry.user == ""`), and the file does not exist on
  disk yet.

**Test hygiene**
- All tests use `tmp_path` and `monkeypatch`; no `unittest.mock` of
  filesystem APIs.
- No test asserts the exact source text of `state.py` (no
  re-assertions of literals).
- One assertion concept per test where practical.

## Status log

- 2026-05-31  Lead — created (draft).
- 2026-05-31  Reviewer — plan review pass 1: changes-requested. Two blockers: log.state_change signature diverges from D-002/08-logging.md contract; lib.awf_home.user_config_dir() does not exist in the current codebase.
- 2026-05-31  Lead — revised plan per Pass 1 feedback: resolved 2 blockers and 3 majors. Adopted canonical `log.state_change(file, key, before, after)` with `key=""`; added scoped `user_config_dir()` helper to `lib/awf_home.py`; added `start` param to all `load*` methods (Shared excepted, user-scope); reused `lib.project.ProjectNotFound`; recorded BaseModel divergence under Context; committed upfront to `_save_impl`; tightened `validate` truthiness and `_path` derivation; added `test_shared_load_or_create_returns_default_when_absent`. Status: draft pending Pass 2.
- 2026-05-31  Reviewer — plan review pass 2: ready. All 10 Pass 1 findings resolved; one Minor (N6) requires Lead to lock ProjectNotFound to augmented-message strategy before Dev starts step 5; no re-review needed.
- 2026-05-31  Lead — locked N6 (ProjectNotFound propagation): re-raise-with-augmented-message + __cause__ preserved.
- 2026-05-31  Dev — implementation complete. Branch: feat/plan-001-foundation-state-schema. Commits 82eaa64..46b141e (4 commits). 21 tests, all green. Ruff clean. mypy --strict lib/state.py passes. All 13 acceptance criteria verified locally. Note: `Infra.validate()` neon invariant is scoped to trigger only when branch_id/branch_name are non-empty (not on empty default Infra), consistent with plan intent that the rule applies to partially-populated neon config. Reviewer should confirm this interpretation.
- 2026-05-31  Reviewer — code review pass 1: accepted. 0 blockers, 0 major, 3 minor.

## Review

### Pass 1 (2026-05-31)

**Blockers:**

- **B1 — `_emit_state_change` signature conflicts with D-002 / 08-logging.md contract.**
  Step 4 specifies `log.state_change(file=str(file), diff=diff, before=before, after=after)`.
  But `08-logging.md` (event type table) and `spec.md § A3` both specify the call as
  `log.state_change(file, key, before, after)` — the `key` parameter identifies which
  JSON key changed; there is no `diff` parameter in the locked API surface.
  The shim must call the function that `lib/log.py` (plan 003) will expose, or the two
  plans will be silently incompatible when wired up. The plan must either adopt the
  locked signature or record an explicit decision to diverge from it.
  Acceptance criterion #3 (`save()` produces a state.change log entry) cannot be verified
  as written because the signature is wrong relative to the spec.

- **B2 — `lib.awf_home.user_config_dir()` does not exist.**
  Step 7 and acceptance criterion `[plan]` 9 both instruct `Shared.load_or_create` to
  resolve its path via `lib.awf_home.user_config_dir() / "shared.json"`. The actual
  `lib/awf_home.py` exposes only `find_awf_home()` — there is no `user_config_dir()`
  function. Two competent Devs will make different choices: one will add
  `user_config_dir()` to `awf_home.py`, another will derive the path inline from
  `find_awf_home()`, a third will use `Config.layered()`. The plan must specify exactly
  which approach is correct and, if the helper needs adding to `awf_home.py`, explicitly
  scope that work here.

**Major:**

- **M1 — `Infra.load()` has no `start` parameter; ambiguous for tests and callers.**
  `ProjectAnchor.load(cls, start: Path | None = None)` accepts an explicit `start` to
  support `tmp_path` in tests. `Infra.load(cls)` in step 6 has no such parameter.
  Two Devs will make different choices (add `start=`, omit it, use a module-level
  override). Tests for `Infra.load` with `tmp_path` are impossible without a `start`
  parameter or some other injection point. The plan should specify the signature
  explicitly.

- **M2 — Name collision: `ProjectNotFound` vs `ProjectNotFoundError`.**
  `lib/project.py` already exports `class ProjectNotFound(RuntimeError)`. Step 2 defines
  a new `ProjectNotFoundError(StateError)` in `lib/state.py`. Step 5 says to re-raise
  `find_project_root()`'s error as the new class. The plan never acknowledges this
  collision. A Dev must decide whether to: (a) import `ProjectNotFound` from
  `lib.project` and re-raise it as `ProjectNotFoundError`, (b) let both names live in
  the codebase, or (c) re-use the existing class instead. No two Devs will decide the
  same way without explicit direction.

- **M3 — `BaseModel` vs "Pydantic v2 dataclasses" — inline divergence from ADR language.**
  Step 5 states: "per D-003 'Pydantic v2 dataclasses' we use `BaseModel` for
  `model_validate` + `model_dump`; the ADR's 'dataclass' is the colloquial sense."
  D-003 uses the phrase "Pydantic v2 dataclasses" throughout. The plan is correct that
  `BaseModel` is the right technical choice, but it makes this decision inline in an
  implementation step rather than recording it where Lead can track it (a D-NNN ADR or
  at minimum a plan-level note that is visible before Dev starts). This is the sort of
  "implicit decision" the workflow explicitly prohibits for non-trivial divergences from
  the locked spec language.

**Minor / nits:**

- **N1 — `Infra.validate()` leaves "both set" undefined for empty-string vs None.**
  Step 6 says `registry.image matches f"{registry.user}/.*"` "when both set." Pydantic
  `str` fields default to empty string (or None if `Optional`). The plan should state
  the rule: "both set" means both are truthy (`bool(value)` is True), so an empty
  string skips the check.

- **N2 — `load_or_create` path-stashing is implicit for the no-file branch.**
  Step 6 says "on `FileNotFoundError`, instantiate the empty default, set `_path`."
  The `_path` value in the no-file case is derived from the project root that
  `find_project_root()` returned. The plan should explicitly state: derive `_path`
  from `root / AWF_DIRNAME / INFRA_FILENAME` before the `try`, capture it in a local
  variable, and assign to `_path` in both branches, so `save()` always has a valid path.
  Without this, a Dev who sets `_path` only in the success branch of `load` will produce
  an instance that raises `AttributeError` on `save()` after `load_or_create`.

- **N3 — Step 8 "third repetition" abstraction rule: three `save()` methods are exactly
  the threshold.** The step says "factor if (and only if) the three saves are literal
  copies." They will be near-literal copies. The plan should commit to one direction
  rather than leaving it to Dev's judgment, since this affects how the three classes are
  structured and tested. Recommendation: extract `_save_impl` from the start, because
  three copies exist from day one and the rule is about not abstracting _speculatively_,
  not about ignoring three identical existing copies.

- **N4 — Acceptance criterion tracing: no explicit test for `Shared.load_or_create`
  returning a usable empty model (non-error) when the config file is absent.**
  `test_shared_load_or_create_uses_awf_home` tests the path; there should also be a test
  `test_shared_load_or_create_returns_default_when_absent` verifying the model fields are
  their defaults and no exception is raised. The acceptance criterion for `Shared` is
  implied but not covered by an explicit named test.

- **N5 — The `state.change` diff documented in step 4 caps at top-level keys.**
  `08-logging.md` specifies "Diff size in `state.change` is capped at 2 KB per side;
  larger diffs record a hash + pointer instead." Step 4 specifies only a top-level diff
  with no cap or fallback. This is a nit rather than a blocker because the full cap
  behaviour belongs to `lib/log.py` (plan 003), not to the shim — but the plan should
  note that the shim passes raw `before`/`after` and the capping is plan 003's
  responsibility.

**Verdict:** changes-requested

### Pass 2 (2026-05-31)

All ten Pass 1 findings (B1, B2, M1–M3, N1–N5) have been resolved in the revised plan. No regressions were found. One residual Lead-flagged tension requires a decision before Dev starts.

**Blockers:** none.

**Major:** none.

**Minor:**

- **N6 — Lock the ProjectNotFound propagation strategy (residual tension from step 2 / step 5).**
  Step 2 deliberately leaves open "either let it propagate … or re-raise it with an augmented message." Step 5 step 1 repeats the same either/or. However, the acceptance criterion states "Missing anchor raises `ProjectNotFound` … with cwd context in the message" and the named test asserts `message contains the tmp path`. The existing `find_project_root()` in `lib/project.py` raises `ProjectNotFound` with a message that names `passport.json` and gives generic "run awf-create-project" advice — it does not embed the `start` path in a form reliably testable as "tmp path." A Dev who chooses "let propagate" will write a test that passes only if `start` happens to appear in the existing message; a Dev who chooses "re-raise with augmented message" will control the message content. Both are `ProjectNotFound` and both pass the type-check, so the type contract is satisfied either way. But the _test_ contract (message contains the tmp path) is only reliably satisfiable with the augmented-message approach.

  **Recommendation: lock to re-raise with augmented message.** Specifically: after `find_project_root(start)` succeeds but `.awf/project.json` is absent (step 5 step 3), raise `ProjectNotFound(f"No .awf/project.json found in {root}; cwd was {start or Path.cwd()}")`. For the case where `find_project_root(start)` itself raises (i.e., no passport.json either), catch the `ProjectNotFound` from `lib.project` and re-raise `ProjectNotFound(f"No project root found from {start or Path.cwd()}: {e}")` — preserving type, augmenting message. This makes the test deterministic, documents what "cwd context" means concretely, and removes all Dev ambiguity.

  This is classified Minor (not Blocker) because both choices satisfy the type contract and the spec acceptance criterion text; the test contract is the tiebreaker, and the fix is a single sentence of guidance. Dev should not proceed past step 5 until the Lead confirms this choice.

**Spec acceptance criteria check:**

All five spec acceptance criteria from `spec.md § A1` are present in the plan:
1. Round-trip (acceptance criterion 1 + test `test_project_anchor_round_trip`). ✓
2. Forward-compat (acceptance criterion 2 + tests). ✓
3. `save()` produces a `state.change` log entry (acceptance criterion 3 + log-hook tests). ✓
4. Cross-field invariants enforced (acceptance criterion 4 + validation tests). ✓
5. Atomic write (acceptance criterion 5 + `test_atomic_write_leaves_prior_file_intact_on_crash`). ✓

**Plan size check:** Eleven implementation steps, one new file (`lib/state.py`), one helper addition (`lib/awf_home.py`), one test file. This is comfortably within one Dev pass. No scope creep introduced by the revision.

**No new ambiguity** has been introduced by the revision. The BaseModel divergence note, `_save_impl` commitment, and `start`-param signatures all reduce ambiguity relative to pass 1.

**Verdict:** ready

*Conditional on Lead locking N6 to the augmented-message strategy before Dev starts step 5. This does not require a re-review pass — a one-line update to step 2 and step 5 is sufficient.*

---

### Pass 1 (2026-05-31) — code review

**Dev callout responses:**

1. **`Infra.validate()` neon invariant scoping.** Confirmed correct. Step 6 says "all fields default to safe empty values … so `load_or_create()` can mint an empty `Infra`." An unconditional `mode=="shared-branch" → project_id non-empty` rule would fire on every freshly-minted empty Infra (which defaults `mode="shared-branch"`) and break the `load_or_create` contract. Scoping to trigger only when `branch_id` or `branch_name` are non-empty is the right interpretation of "partially-populated neon config." Consistent with D-003 and the plan. No amendment needed.

2. **`_save_impl` `# type: ignore[call-arg]`.** Legitimate type-system limitation. Pydantic v2 `BaseModel` exposes a deprecated classmethod also named `validate`; mypy sees the classmethod signature on the `BaseModel` type and flags the instance call as a `call-arg` error. The three subclasses define instance-method `validate(self) -> None` which is the correct target at runtime. The comment is accurate; it does not mask a bug. The `# type: ignore[attr-defined]` on line 113 (inside `_emit_state_change`) is similarly legitimate — `lib.log` does not exist at type-check time.

**Blockers:** none.

**Major:** none.

**Minor / nits:**

- **N1 — `spec.md` § A1 uses `ProjectNotFoundError`, not `ProjectNotFound` (line 68 and line 115).** The implementation correctly uses `ProjectNotFound` (imported from `lib.project`), which is what the plan locked in step 2 and what the tests assert. The spec has a stale name. `lib/state.py` and `tests/lib/test_state.py` are correct; `docs/spec.md` line 68 and line 115 should be updated to `ProjectNotFound` for consistency. No code change needed — documentation only.

- **N2 — Three `assert root is not None` guards (state.py lines 221, 386, 429) are mypy-satisfaction assertions for a type-annotation gap in `lib/project.py`.** `find_project_root()` returns `Path | None` because `optional=True` is a supported call path; when called without `optional=True` it always raises or returns a `Path`, never `None`. The assertions are harmless and technically correct; a cleaner fix would be an `@overload` in `lib/project.py` to narrow the return type for the non-optional call. That is squarely plan 002 scope (which owns `lib/project.py`). No action needed in this plan.

- **N3 — `test_save_refuses_to_write_invalid_state` and `test_atomic_write_leaves_prior_file_intact_on_crash` access `infra._path` directly (test lines 237, 247, 307, 317).** The testing principles say "don't test internal state"; `_path` is a `PrivateAttr`. The access is forced by the lack of a public path property and is the only practical way to retrieve the file path for on-disk comparison. Acceptable deviation. Consider adding a `@property path(self) -> Path` to each model in a follow-on plan so tests can avoid reaching into private attributes.

**Acceptance criteria verification:**

All 13 criteria (5 spec § A1 + 8 `[plan]`-tagged) are covered by named tests:

| Criterion | Test |
|---|---|
| Round-trip | `test_project_anchor_round_trip`, `test_infra_round_trip`, `test_shared_round_trip` |
| Forward-compat | `test_anchor_tolerates_unknown_top_level_keys`, `test_infra_tolerates_unknown_nested_keys` |
| `save()` emits `state.change` | `test_save_emits_state_change_via_log_hook` |
| Cross-field invariants | `test_anchor_rejects_landing_with_infra`, `test_anchor_rejects_bad_slug`, `test_infra_rejects_lb_without_servers`, `test_save_refuses_to_write_invalid_state` |
| Atomic write | `test_atomic_write_leaves_prior_file_intact_on_crash`, `test_atomic_write_creates_parent_dir` |
| Missing anchor → `ProjectNotFound` | `test_load_missing_anchor_raises_project_not_found`, `test_infra_load_missing_project_raises_project_not_found` |
| Malformed JSON → `StateCorruptError` | `test_load_malformed_json_raises_corrupt` |
| `load_or_create` no premature write | `test_infra_load_or_create_does_not_create_file_until_save` |
| `Shared` uses `user_config_dir()` | `test_shared_load_or_create_uses_awf_home` |
| Log signature `(file, key, before, after)` | `test_save_emits_state_change_via_log_hook` |
| Log import-safe | `test_save_when_log_unavailable_does_not_raise` |
| All models extra="allow" | `test_anchor_tolerates_unknown_top_level_keys`, `test_infra_tolerates_unknown_nested_keys` |
| mypy --strict + ruff clean | Verified by Reviewer (both pass) |

**Verdict:** accepted
