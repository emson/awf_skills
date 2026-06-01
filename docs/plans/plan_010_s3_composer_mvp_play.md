# Plan 010 — S3 composer `awf-stage-mvp-play` (proof of architecture)

**Status:** accepted
**Phase:** B
**Spec refs:** [`spec.md` § B5](../spec.md), [`decisions.md` D-001](../decisions.md#d-001--multi-stage-architecture-pattern), [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson), [`08-logging.md`](../08-logging.md)
**Owner (current):** Reviewer
**Created:** 2026-06-01
**Updated:** 2026-06-01

## Status log

| Date | Status | Actor | Note |
|------|--------|-------|------|
| 2026-06-01 | draft | Lead | Initial plan; proves Phase B architecture by chaining the ten S3 atomic skills into a first composer; encodes T1–T5 tensions from plan_008/009 lessons. |
| 2026-06-01 | review-passed | Reviewer | Pass 1: all five tensions approved as recommended. One blocking prerequisite amendment (plan_009 `awf-kamal-setup` must emit `"gate":"dns_propagation"` in JSON on exit-3). Tier-3 happy-path confirmed as merge gate. No other blockers. |
| 2026-06-01 | implemented | Dev | `skills/awf-stage-mvp-play/SKILL.md` + `scripts/stage_mvp_play.py` implemented. 24 new tests in `tests/skills/test_stage_mvp_play.py` — all passing. Full suite 265 green (241 baseline + 24 new). ruff clean. Tier-3 real-domain test is follow-up merge gate. |
| 2026-06-01 | changes-requested | Reviewer | Code review Pass 1: 1 Blocker (DATABASE_URL plaintext in process.invoke log via dead safe_args), 2 Majors (re-run breaks at step 4 when neon_branch returns skip without connection_string; missing-script untested). Fix and re-submit. |
| 2026-06-01 | fix-applied | Dev | B1 fixed: `_redact_cmd` helper strips `--value`/`--from-file` values; `log.process` now receives `redacted_cmd`; dead `safe_args` param removed; also fixed script path naming bug (`awf-` prefix was not stripped, causing incorrect path). M1 fixed: `_kamal_secrets_has_database_url` checks `.kamal/secrets` on re-run; step 4 auto-skips if key already present; `test_skip_actions_on_rerun` updated to use real skip payloads (no `connection_string`). M2 fixed: `_invoke` guards `skill_script.exists()` before subprocess; exits 4 with clear message. 4 new tests added (269 total, all green, ruff clean). |

## Goal

Deliver `skills/awf-stage-mvp-play/` — the first **composer** skill. It
promotes a project from any prior stage to `stage="mvp-play"` by
chaining the ten S3 atomic skills (plans 008+009) in dependency order
per spec § B5. This is the proof-of-architecture for Phase B: it
demonstrates that atomic skills, the layered state model (D-003), the
event log (plan_003), the kamal lib's DNS gate (plan_007) and the
project locator (plan_002) compose into an end-to-end pipeline that can
take an empty repo to a live deployed site without any composer-internal
"glue" hidden from the atomic skills.

On a happy path against a real test domain, one invocation produces a
live site within 5 minutes (spec § B5 AC #5). On any mid-run failure,
the anchor is **not** advanced and partial state on `infra.json` /
`shared.json` reflects exactly what was created (resumability). Re-run
after the operator fixes the cause skips the completed sub-steps.

Out of scope (deferred):
- Higher composers (`awf-stage-prescale`, `awf-stage-scale`) — Phase D.
- `awf-launch` (Pages/landing-page launch) — that's a separate composer
  already shipped at the S1/S2 layer; this plan does not touch it.
- Operator-facing `awf-status`/`awf-log` rebuilds — Phase C (C1/C2).
- Rollback / down-stage composer — post-Phase-B.

## Context

- Spec [§ B5](../spec.md): defines the nine-step sequence (the eighth
  being a wait/poll built into `awf-kamal-setup` via plan_007's lib),
  and the five composer acceptance criteria.
- ADR [D-001](../decisions.md#d-001--multi-stage-architecture-pattern):
  the two-layer model. **Composers orchestrate; atomic skills mutate.**
  A composer must not reach into provider APIs directly — every external
  side effect is delegated to an atomic skill. The DNS-before-TLS rule
  and the orange-cloud-after-cert rule live inside the atomic
  layer/lib (plan_007 `KamalRunner.setup`, plan_008 `awf-cf-dns-record`
  default `proxied=False`); the composer does not re-implement them.
- ADR [D-003](../decisions.md#d-003--awf-schemas-projectjson-infrajson-sharedjson):
  composer reads/writes `ProjectAnchor` (`.awf/project.json`) to advance
  `stage` and `has.*` flags. It reads `Infra` and `Shared` to decide
  which sub-steps are needed, but it never writes them — atomic skills
  own those files.
- Plans 008+009 delivered the ten atomic skills; both ship the **same
  exit-code table** (0 ok / 1 no-project / 2 creds-or-CLI-missing /
  3 remote-or-subprocess / 4 state-validation) and a stable `--json`
  output shape with `action` ∈ {`created`, `updated`, `skip`}. This
  shared protocol is the composer's interface with its children.
- Plan_003 / `08-logging.md`: sessions are **flat** in the JSONL log.
  Nested sessions are not modelled. Composer opens its own session;
  each subprocess opens its own peer session. The composer's session
  contains its own `log.note`, `log.gate.hit`, `log.error` events plus
  per-step summary notes; the **per-atomic-skill** events live in the
  child sessions, discoverable via `awf-log sessions --days 1`.
- Plan_007 `lib/kamal/runner.py`: `KamalRunner.setup()` polls DNS
  internally and raises `KamalDnsTimeout` on hard timeout; the atomic
  `awf-kamal-setup` skill catches this and exits 3 with stderr
  `DNS for <domain> never resolved to <ip>; waited <n>s` plus a
  `gate.hit name=dns_propagation` event in its child session log.

## Architecture overview

```
skills/awf-stage-mvp-play/
├── SKILL.md                       # frontmatter + 1-page description + exit codes
└── scripts/stage_mvp_play.py      # uv-script, ~300-400 lines
```

The script is a deterministic pipeline:

```
load anchor/infra/shared
  → compute desired diff
  → for step in STEPS:
        if step.needed(state): step.run(ctx) else: ctx.note("skip", step)
  → on first non-zero subprocess exit → log.error + early-return (no anchor advance)
  → on clean finish → mutate ProjectAnchor (stage/has.*), save, exit 0
```

Each step is a small, testable function:

```python
def step_dockerize(ctx: ComposerCtx) -> StepResult: ...
def step_neon_branch(ctx: ComposerCtx) -> StepResult: ...
# ...
STEPS = [
  ("shared_infra_get",  step_shared_infra_get),
  ("dockerize",         step_dockerize),
  ("neon_branch",       step_neon_branch),
  ("secret_database_url", step_secret_database_url),
  ("kamal_config",      step_kamal_config),
  ("cf_dns_record",     step_cf_dns_record),
  ("kamal_setup",       step_kamal_setup),    # DNS poll inside the atomic skill (lib)
  ("kamal_deploy",      step_kamal_deploy),
]
```

`ComposerCtx` is a tiny dataclass carrying `anchor`, the project root,
the cached pre-run snapshots of `Infra` and `Shared`, parsed CLI args,
and helpers `ctx.invoke(skill, *args) -> StepResult`. `StepResult` is
`{action, skill, exit_code, details, stderr}` — `action` is mirrored
from the atomic skill's JSON output, `details` is the rest of that JSON.

### Step execution

`ctx.invoke()` is the single point where subprocesses are spawned
(Decision §1). Pseudocode:

```python
def invoke(self, skill: str, *cli_args: str) -> StepResult:
    script = AWF_HOME / "skills" / skill / "scripts" / f"{verb(skill)}.py"
    cmd = ["uv", "run", str(script), *cli_args, "--json"]
    if self.args.dry_run:
        log.note(f"[dry-run] would invoke {skill} {cli_args}")
        return StepResult(action="dry-run", skill=skill, exit_code=0, details={}, stderr="")
    log.note(f"invoke {skill}")
    proc = subprocess.run(cmd, cwd=self.project_root, capture_output=True, text=True)
    try:
        details = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        details = {"_unparsed_stdout": proc.stdout}
    return StepResult(
        action=details.get("action", "unknown"),
        skill=skill,
        exit_code=proc.returncode,
        details=details,
        stderr=proc.stderr,
    )
```

`cwd=self.project_root` so each atomic skill's `find_project_root()`
walks up from the same place (Decision §4). `--json` is always passed
(Decision §2) so stdout is parseable. `AWF_HOME` resolves via
`lib/awf_home.py:get_awf_home()` (the existing locator).

### Failure handling

After each `invoke`:

```python
if result.exit_code != 0:
    log.error(
        f"step {step_name} ({result.skill}) failed: exit {result.exit_code}",
        stderr=result.stderr[-2000:],   # tail; full stderr already in child session
    )
    if "dns_propagation" in result.stderr or result.details.get("gate") == "dns_propagation":
        log.gate_hit(name="dns_propagation", reason=result.stderr.splitlines()[-1] if result.stderr else "")
        sys.stderr.write(
            f"\nDNS for {anchor.domain} has not propagated to the play server.\n"
            f"Wait a few minutes and re-run `awf-stage-mvp-play` — completed steps will be skipped.\n"
        )
        sys.exit(EXIT_GATE)   # 5 — see exit-code table below
    sys.exit(map_exit(result.exit_code))   # passthrough atomic codes
```

We do **not** roll back partial state. The atomic skills already
recorded what they did in `infra.json` / `shared.json`; resumability
comes from re-running and letting each atomic skill's idempotent
`action="skip"` path no-op the already-done work (plans 008+009 §
acceptance #1). Anchor is only advanced after the **last** step
succeeds.

### Anchor advancement

On clean run-through:

```python
anchor.stage = "mvp-play"
anchor.has.infra = True
anchor.has.kamal = True
anchor.save()                 # emits state.change
log.note("stage advanced to mvp-play")
```

The save happens **inside** the composer's `log.session` so the
`state.change` event is attributed to the composer's session id. This
is the **only** state-file write performed by the composer itself.

## Per-step detail

For each step the composer:
1. Decides whether the step is needed (`needed(state)` predicate against
   the pre-run `Infra`/`Shared`/filesystem snapshot).
2. Computes the CLI args for the atomic skill from anchor + state.
3. Invokes it via `ctx.invoke()`.
4. Records a summary `log.note` with `{step, action, skill}`.

The `needed()` predicates are deliberately conservative — if in doubt,
**invoke the atomic skill** and let its own idempotency contract
produce `action="skip"`. This means `needed()` mostly returns `True`;
its job is to skip steps that can't even be attempted yet (e.g.,
`kamal_setup` requires `Shared.play_server.ip` to be populated).

| # | Step | Atomic skill | `needed()` predicate | CLI args derived from |
|---|------|--------------|----------------------|------------------------|
| 1 | `shared_infra_get`    | `awf-shared-infra-get`  | always True (idempotent search-or-create) | `--play-hostname` from composer arg (default `play.awfship.dev`) |
| 2 | `dockerize`           | `awf-app-dockerize`     | always True (file-level skip handled in-skill) | `--port` / `--node-version` composer args (defaults `3000` / `20`) |
| 3 | `neon_branch`         | `awf-neon-branch`       | `Shared.play_neon.project_id` is set | branch name = `anchor.slug`; project id = `Shared.play_neon.project_id` |
| 4 | `secret_database_url` | `awf-app-secret-set`    | `Infra.neon.branch_connection_string` is set after step 3 | `--key DATABASE_URL --value <conn-string>` (value sourced **literal** — see Decision §6) |
| 5 | `kamal_config`        | `awf-kamal-config`      | `Infra.registry.host` is set (composer fails fast otherwise — see T2) | `--path config/deploy.yml` |
| 6 | `cf_dns_record`       | `awf-cf-dns-record`     | always True (CF zone is upstream of this composer; see T3) | record name = `anchor.domain`; type `A`; content = `Shared.play_server.ip`; proxied=False |
| 7 | `kamal_setup`         | `awf-kamal-setup`       | `Shared.play_server.ip` is set | `--server-ip <ip>` (DNS wait/poll happens inside the atomic skill — spec § B5 step 7 is **not a separate composer step**) |
| 8 | `kamal_deploy`        | `awf-kamal-deploy`      | step 5 succeeded in this run or `Infra.kamal.config_path` is set | (no args) |
| 9 | anchor advance        | — (composer-local)      | all prior steps `exit_code == 0`                            | — |

**Re-read** between steps: after each successful step the composer
reloads `Infra` and `Shared` from disk into `ctx.infra` / `ctx.shared`
before the next step's `needed()` runs. This is the cleanest way to
pick up the new state without modelling cross-skill data flow inside
the composer (D-003: state files are the source of truth). The cost
is two file reads per step — negligible.

### Exit codes (composer-level)

| Code | Meaning |
|------|---------|
| `0`  | All steps succeeded; anchor advanced to `mvp-play` |
| `1`  | No `.awf/project.json` walking up |
| `2`  | An atomic skill exited 2 (credentials / CLI missing); composer surfaces the child's stderr |
| `3`  | An atomic skill exited 3 (remote / subprocess error) |
| `4`  | An atomic skill exited 4 (state validation) or composer's own anchor `.save()` failed |
| `5`  | **Gate hit** — DNS propagation (or future gates); re-run after the gate clears |

Code 5 is **new in this plan**. Plans 008+009 do not use it (their
DNS-timeout case is exit 3 at the atomic layer). The composer
**re-classifies** an atomic exit-3 that carries a `gate.hit
dns_propagation` event into composer exit-5, so the operator (or an
outer driver) can distinguish "wait and retry" from "fix and retry".
This is the canonical handling for spec § B5 AC #4 ("exits cleanly with
gate.hit event, stderr instruction, non-zero exit code").

## Logging

- **One composer session** wraps the whole run:
  ```python
  with log.session(composer="awf-stage-mvp-play", target="mvp-play"):
      ...
  ```
- Per-step events emitted by the composer:
  - `log.note(step=<name>, action="start")` before each `invoke`.
  - `log.note(step=<name>, action=<result.action>, skill=<result.skill>, exit=<n>)` after.
  - `log.error(...)` on any non-zero child exit.
  - `log.gate_hit(name="dns_propagation", ...)` on DNS timeout.
  - `log.state_change(...)` from the anchor `.save()` at the end.
- The **atomic skills' own events** live in their own sessions (peers,
  not children). Cross-session correlation is via timestamp + project
  slug; `awf-log session <id>` will show the composer's session, and
  `awf-log sessions --days 1 --project <slug>` will show the full set
  in time order (Phase C C1 will polish this; today the raw JSONL is
  enough).
- **No nested `log.session`** anywhere. (See Decision §5.)

## Acceptance criteria

Spec § B5 (verbatim):

- [x] Whole run wrapped in one `log.session(composer="awf-stage-mvp-play", target="mvp-play")`.
- [x] On mid-run failure, anchor is **not** advanced; partial
      `infra.json` / `shared.json` reflect what atomic skills created.
- [x] Re-run after fix: completed sub-steps log `action="skip"`; no
      duplicate resources created (delegated to atomic-skill
      idempotency — plans 008+009 already enforce this).
- [x] On DNS-propagation gate: composer exits 5 with `gate.hit
      name=dns_propagation` event and a one-paragraph stderr
      instruction telling the operator to wait and re-run.
- [ ] End-to-end happy path on a real test domain produces a live site
      within 5 minutes (tier-3 manual test; see § Testing). **DEFERRED — merge gate for Reviewer.**

Plan-specific additions:

- [x] `--dry-run` flag: prints the eight-step plan with the resolved
      atomic skill names and the args that would be passed; emits
      **zero** subprocess calls; emits `log.note(step=..., action="dry-run")`
      per step; exits 0; anchor untouched.
- [x] Unit tests with **mocked `subprocess.run`** (no real subprocesses):
  - Happy path: 8 successful invocations → anchor advanced.
  - Mid-run failure (step 5 returns exit 3): composer exits 3,
    anchor unchanged, no further subprocess calls observed.
  - DNS gate (step 7 returns exit 3 with `gate=dns_propagation` in
    JSON): composer exits 5, `gate.hit` log event present, anchor unchanged.
  - Re-run with all atomic skills returning `action="skip"`: composer
    still emits 8 invocations, anchor advanced exactly once.
  - `--dry-run`: zero subprocess calls (counter asserted); anchor unchanged.
  - Missing project (no `.awf/project.json` upward): exit 1, no subprocess calls.
  - Step 3 (`neon_branch`) gated by `Shared.play_neon_project_id`:
    composer fails fast with exit 4 and clear stderr.
- [x] Integration test with **fake atomic-skill scripts** in `tmp_path`
      (subprocess actually invoked but pointed at trivial fakes). ≥ 1 happy path,
      ≥ 1 non-zero-exit path.
- [ ] `ruff check skills/awf-stage-mvp-play tests/skills/test_stage_mvp_play.py` clean.
- [ ] Full suite green: 239 baseline + ≥ 12 new = ≥ 251 passing, no
      regressions.
- [ ] SKILL.md frontmatter: `name`, `description`, the eight-step
      sequence, composer-level inputs (`--dry-run`, `--play-hostname`,
      `--port`, `--node-version`), composer exit-code table including
      code 5, prerequisites (CF zone exists, credentials present —
      cite `awf-doctor`).

## Decisions

1. **Subprocess invocation (Option A, per task brief).** Each atomic
   skill is invoked as `uv run skills/<name>/scripts/<verb>.py … --json`
   in a fresh subprocess. Pros: clean process isolation, the
   exit-code table from plans 008+009 is the literal interface (no
   import-time coupling), test fakes are tiny shell scripts.
   Cons: ~200ms `uv` startup per call × 8 = ~1.6s overhead per run
   — acceptable; the real-cost steps (kamal setup/deploy, Neon API)
   dominate by orders of magnitude.
2. **`--json` always passed.** Stdout parseability is the contract.
   Stderr is captured separately and tail-logged on failure; not
   parsed. Human-mode output is never used by the composer (it's
   reserved for direct human invocation of atomic skills).
3. **One composer SKILL.md surface, minimal args.** Composer CLI:
   - `--dry-run` — plan-only mode.
   - `--play-hostname HOST` — passthrough to `awf-shared-infra-get`
     (default `play.awfship.dev`).
   - `--port N` / `--node-version V` — passthrough to `awf-app-dockerize`.
   - `--json` — composer's own JSON output (summary of the eight
     steps with their `action`s).
   No `--skip-step`, no `--from-step`, no `--force`. Resumability is
   delegated entirely to atomic-skill idempotency (A1, T1).
4. **`cwd=project_root` for every subprocess.** All atomic skills walk
   up from cwd to find `.awf/project.json` (plan_002); running them
   from the project root is the simplest invariant. The composer
   itself is invoked from anywhere — it resolves the project root
   once via `ProjectAnchor.load()` and uses that path as the cwd for
   every child.
5. **Sessions are flat.** No `parent_session_id` env var; no nested
   `log.session`. Composer's session contains step-level summaries;
   atomic-skill sessions contain the resource-level detail. Cross-
   session navigation is a C1 (`awf-log`) concern, not a B5 concern.
6. **Step 4 `awf-app-secret-set` uses `--value` literal, not
   `--from-env`.** The DATABASE_URL value is read by the composer
   from `Infra.neon.branch_connection_string` (populated by step 3)
   and passed as a literal CLI argument to `awf-app-secret-set`. The
   atomic skill's own redaction (plan_009 Decision §2 + T2) ensures
   the value never lands in the log. The composer must take the same
   care: when building `safe_args` for its own `log.invoke`/`log.note`
   events, the DATABASE_URL value is never included — only the key
   name and the source-step (`step="neon_branch"`).
7. **`--dry-run` short-circuits at `ctx.invoke`**, not in each step's
   body. The step bodies still execute their `needed()` predicates
   and arg-derivation logic so the dry-run output reflects the same
   decisions a real run would make. Only the `subprocess.run` call is
   suppressed and replaced with a `log.note(action="dry-run")`. This
   keeps dry-run faithful to the real plan.

## Tensions for Reviewer

1. **T1 — `needed()` predicates: aggressive or conservative?**
   - (a) **Conservative (recommended; what this plan ships).**
         `needed()` mostly returns `True`; the composer always invokes
         the atomic skill and trusts its `action="skip"` path. Pro:
         simpler composer, single source of idempotency truth (the
         atomic skill). Con: 8 subprocess spawns even on a no-op
         re-run (~1.6s wasted).
   - (b) **Aggressive.** Composer pre-computes the diff per step,
         skips invocation when state already matches the target.
         Pro: faster no-op re-runs. Con: composer now duplicates each
         atomic skill's skip-logic; drift between the two is a
         silent-correctness hazard.
   Recommend (a). Performance is a non-issue at S3 scale (1 run per
   site per day at most); correctness is paramount. Revisit only if
   composer chains grow to 30+ steps.

2. **T2 — Fail-fast vs proceed on missing prerequisite state.**
   E.g., step 5 (`kamal_config`) requires `Infra.registry.host`,
   which is populated by an earlier registry-credentials step not in
   this composer (it's in the doctor / init layer). Options:
   - (a) **Fail-fast with exit 4** and stderr referring the operator
         to the missing-state's owning skill (`awf-doctor` for
         registry creds). Recommended.
   - (b) Invoke `awf-kamal-config` and let *it* fail with its own
         error message (lib raises `KamalConfigInvariantError`).
   (a) gives a clearer message and fails before the subprocess is
   even spawned; (b) keeps the composer dumber. The cost of (a) is
   a handful of `if not infra.registry.host: sys.exit(4)` lines —
   small and explicit. Recommend (a).

3. **T3 — Cloudflare zone prerequisite.** `awf-cf-dns-record` (plan_008)
   assumes the CF zone for `anchor.domain` exists. The zone is created
   by `awf-launch` / a Pages/landing skill upstream of this composer,
   not by any S3 atomic skill. Options:
   - (a) **Document as a prerequisite** in SKILL.md; composer fails
         fast with exit 4 if `awf-cf-dns-record` returns exit 3 with
         a "zone not found" error code. Recommended; matches the
         S1/S2 boundary.
   - (b) Add `awf-cf-zone-ensure` to the composer's sequence (would
         require a new atomic skill — out of scope for plan_010).
   Recommend (a). The S1 composer (`awf-launch`) is the canonical
   place for zone creation; mvp-play presupposes a launched site.

4. **T4 — Composer exit code 5 vs exit code 3 for DNS gate.**
   - (a) **New exit code 5 = gate-hit** (recommended; what this plan
         ships). Lets outer drivers distinguish "wait and retry"
         from "fix and retry". Aligns with spec § B5 AC #4 which
         requires a "non-zero exit code" but doesn't pin a value.
   - (b) Reuse exit code 3 (passthrough from atomic layer). Simpler
         but loses the gate/error distinction; the only way to tell
         them apart is to grep the log for `gate.hit`.
   Recommend (a). Cost is one extra entry in the exit-code table and
   a small bit of re-classification logic. This is also forward-
   compatible: future gates (e.g., GHCR-token-not-yet-active,
   Hetzner-quota-pending) all map to 5.

5. **T5 — How much detail in the composer's JSON output mode?**
   - (a) **Per-step summary** (recommended): `{"steps": [{step,
         skill, action, exit_code}, ...], "stage": "mvp-play",
         "advanced": true}`. Composable for an outer driver.
   - (b) Full echo of each atomic skill's JSON. Larger, but loses
         nothing.
   - (c) Just `{"stage": "mvp-play", "advanced": true}`.
   Recommend (a). The atomic-skill detail is already in the event
   log under their child sessions; the composer's JSON is the
   summary view.

## Risks

- **Subprocess `uv run` cold-start performance.** ~200ms × 8 ≈ 1.6s
  fixed overhead; tolerable today, but if Phase D composers chain
  30+ atomic calls this becomes the dominant cost. Mitigation
  documented in T1: revisit Option B (in-process import) only when
  the composer chain exceeds ~15 steps.
- **Test fakes drifting from real atomic-skill JSON shape.** The
  integration test uses tiny fake scripts that emit a stubbed JSON
  object. If plans 008+009 atomic skills evolve their JSON shape
  (add fields, rename `action`), the composer's unit tests still
  pass but a real run fails. Mitigation: the integration test
  exercises **at least one real atomic skill** (`awf-app-dockerize`,
  which has no remote deps and can run safely in `tmp_path`) end
  to end — this is the canary.
- **DNS-gate detection brittleness.** We detect the gate by checking
  `result.details.get("gate") == "dns_propagation"` in the parsed
  JSON or by stderr substring match. If `awf-kamal-setup` doesn't
  reliably emit the `gate` field in its JSON output (plan_009
  Decision §3 has it always emitting `action="created"` on success
  and exit-3 on `KamalDnsTimeout` with stderr text but **no**
  structured JSON gate marker), we fall back to stderr scanning.
  **Mitigation:** plan_009 SKILL.md for `awf-kamal-setup` should be
  amended to include a `"gate": "dns_propagation"` field in the JSON
  output when `KamalDnsTimeout` is the cause of exit-3. This is a
  small follow-up to plan_009 — call it out in the PR description
  for plan_010 so the reviewer can decide whether to amend plan_009
  in the same PR or accept the stderr-substring fallback as
  permanent.
- **`AWF_HOME` resolution in subprocesses.** Each child process
  resolves `AWF_HOME` independently. If the env var isn't set, the
  fallback walk-up (`lib/awf_home.py`) finds the repo root via
  `pyproject.toml`. The composer passes through its own environment
  unchanged, so any AWF_HOME visible to the composer is visible to
  the children. No special handling needed.
- **Re-run idempotency depends on every atomic skill respecting its
  `skip` contract.** We rely on plans 008+009's acceptance criteria.
  Any future atomic skill added to this chain must also be idempotent
  or the composer's re-run guarantee breaks. The integration test
  pins this for today's set.

## Out of scope

- `awf-stage-mvp-play --rollback` / down-staging — Phase D.
- Auto-running `awf-doctor` first — the composer fails fast with
  exit 2 when a child exits 2; the operator runs doctor manually.
- Parallel step execution — all 8 steps run serially. The DAG is
  almost-linear (only `kamal_setup` and `kamal_deploy` could share
  prerequisites with earlier steps); parallelism gain is marginal
  and complicates failure semantics.
- Recording the composer's own `composer_runs[]` history in the
  anchor or a sidecar file — the event log is the system of record
  for "what runs happened when". `awf-log` (C1) will surface it.
- Cross-session correlation IDs (a composer `run_id` propagated to
  children) — defer to C1 / a future logging schema bump if/when
  cross-session queries become common.

## Implementation order

1. `skills/awf-stage-mvp-play/SKILL.md` — drafted from the table
   above plus the exit-code table.
2. `skills/awf-stage-mvp-play/scripts/stage_mvp_play.py` skeleton:
   `ComposerCtx`, `StepResult`, `ctx.invoke`, `STEPS` list, main
   loop, anchor-advance, exit-code mapping.
3. Per-step `step_*` function bodies (each ~10-15 lines: predicate +
   arg derivation + `ctx.invoke`).
4. `tests/skills/test_stage_mvp_play.py` with a `fake_invoke` fixture
   that monkeypatches `ctx.invoke` to return a configured
   `StepResult`. Covers all the unit-test cases in § Acceptance.
5. Integration test: real `subprocess.run` against a tmp_path tree
   containing fake `skills/<name>/scripts/<verb>.py` shims (each is
   a 5-line script that prints a stubbed JSON and exits a chosen
   code). Set `AWF_HOME` env var to the tmp_path for that test
   only (`monkeypatch.setenv("AWF_HOME", str(tmp_path))`).
6. Tier-3 manual happy-path on a real test domain — record in the
   PR description; not a CI gate.
7. Ruff + full pytest run; PR.

---

**Reviewer paragraph:** This plan ships `awf-stage-mvp-play` as a
subprocess-orchestrating composer that chains the ten S3 atomic skills
(plans 008+009) in the spec § B5 sequence, using `uv run … --json` as
the cross-skill protocol and a flat-session logging model
(`08-logging.md`). Key decisions: subprocess Option A with `cwd=
project_root`, conservative `needed()` predicates that delegate
idempotency to the atomic layer, fail-fast on missing prerequisite
state (e.g., empty `Infra.registry.host`), a new composer-level exit
code 5 for gate-hits (distinguishing wait-and-retry from
fix-and-retry), DATABASE_URL passed literally to `awf-app-secret-set`
with explicit redaction in composer-emitted log args. Tensions for
review: (T1) conservative vs aggressive `needed()` predicates — recommend
conservative; (T2) fail-fast vs let-child-fail on missing prereqs —
recommend fail-fast with exit 4; (T3) CF zone is a documented
prerequisite (created upstream by `awf-launch`), not part of this
composer's sequence; (T4) introducing exit 5 vs reusing 3 for gates —
recommend 5; (T5) per-step summary as the composer's `--json` shape
vs full echo — recommend summary. The main forward-looking risk is
DNS-gate detection brittleness: plan_009's `awf-kamal-setup` may need
a one-line amendment to include `"gate": "dns_propagation"` in its
exit-3 JSON output, otherwise the composer falls back to stderr
substring matching (acceptable but uglier). Reviewer should also
confirm whether tier-3 (real-domain) happy-path is a merge gate or a
follow-up validation.

---

### Pass 1 (2026-06-01)

**Reviewer:** Sonnet 4.6
**Verdict: APPROVED — all five tensions resolved as recommended; one blocking prerequisite (plan_009 amendment); one implementation note; tier-3 merge-gate confirmed.**

**T1 — Conservative `needed()` predicates.**
APPROVED. The plan's recommendation is correct and well-reasoned. Delegating idempotency entirely to the atomic layer (each child's `action="skip"` path) is the right tradeoff at S3 scale: the ~1.6 s fixed overhead from 8 `uv run` cold-starts is negligible against the dominated real-work steps (DNS poll up to 600 s, Kamal setup/deploy). More importantly, a conservative composer avoids the silent-correctness hazard: if a `needed()` predicate in the composer diverges from the corresponding skip-logic inside the atomic skill (e.g., a field rename in `Infra`, a new skip condition added to a plan_008/009 follow-up), the composer's short-circuit produces a silent no-op while the atomic layer would have caught the mismatch. A single idempotency truth point (the atomic skill) is worth the 1.6 s. The performance mitigation note ("revisit at 15+ steps, consider in-process import as Option B") is correct. No change required.

**T2 — Fail-fast on missing prerequisite state.**
APPROVED. Option (a) — exit 4 with a clear stderr message naming the owning skill — is the right call. The plan's analysis is accurate: the cost is a handful of `if not infra.registry.host` guards, and the benefit is a diagnostic error at the composer level rather than a harder-to-read `KamalConfigInvariantError` bubbling up from inside a subprocess. The plan_009 Pass 1 code review already recorded a Minor: `awf-kamal-config`'s `return 2` on `KamalError` is inside the `log.session`/`log.invoke` context manager, causing those wrappers to emit `result="ok"` on what is actually an error exit. That Minor is not yet resolved. The fail-fast guard in the composer fires **before** `awf-kamal-config` is invoked, so the plan_009 Minor does not affect the composer's own exit-code semantics. However, the guard's value is highest exactly when the plan_009 Minor is present — a composer-level exit-4 with a clear message ("registry.host not set; run awf-doctor --for-stage mvp-play") is cleaner than the child's incorrect `result="ok"` followed by exit 2. Implementation note: the guard should be placed in `step_kamal_config`'s function body, not in the `needed()` predicate, so that the failure is captured inside the composer's log session with full context. No change to plan required; note for implementer only.

**T3 — CF zone as upstream prerequisite.**
APPROVED. The S1/S2 boundary is correctly placed. The `awf-launch` composer (S1) creates the CF zone; `awf-stage-mvp-play` presupposes a launched site. The plan's handling — document it in SKILL.md prerequisites and map an `awf-cf-dns-record` exit-3 with a "zone not found" message to a composer exit-4 — is the minimum correct handling. One verification from reading `awf-launch/SKILL.md`: that skill does run `awf-setup-domain` (step 4a) before any of the S3 flow, so the zone is guaranteed present for any site that went through the standard launch pipeline. The edge case of a project created outside `awf-launch` (passport-only, zone never created) is correctly handled as a user error surfaced as exit 4. No change required.

**T4 — New composer exit code 5 for gate-hits.**
APPROVED. Exit code 5 is the right choice and the spec § B5 AC #4 ("non-zero exit code" for DNS gate) is satisfied by any value; the semantic distinction between "wait and retry" (5) and "fix and retry" (1–4) is genuinely useful and forward-compatible (future gates like GHCR-token-not-yet-active all map cleanly to 5). The exit-code table is internally consistent. Reading `kamal_setup.py` confirms the current implementation: `KamalDnsTimeout` exits 3 with a bare `print(f"awf-kamal-setup: {exc}", file=sys.stderr)` and returns 3 — **no JSON emitted on the error path** (the `if as_json: print(...)` block at line 90 is only reached on success). This means the composer's gate-detection logic (`result.details.get("gate") == "dns_propagation"`) will **always** fall through to the stderr substring match on the current codebase. The plan acknowledges this risk and calls out a plan_009 amendment; the reviewer confirms this is blocking and not optional — see the note on plan_009 below. No change to the exit code decision required; the blocking note stands.

**T5 — Per-step summary as composer JSON output.**
APPROVED. Option (a) — `{"steps": [{step, skill, action, exit_code}, ...], "stage": "mvp-play", "advanced": true}` — is the correct shape. The atomic skills' full JSON detail is already in the event log under their respective child sessions (observable via `awf-log sessions --days 1`); the composer's `--json` output is the summary view for outer drivers or scripted use. Option (b) (full echo) would bloat stdout with nested JSON from up to 8 children, duplicate what the log already holds, and make the composer's own output format fragile to any change in atomic-skill JSON shape. Option (c) is too thin — `advanced: false` on a mid-run abort needs the step list to diagnose. Option (a) is minimal-and-complete. Implementation note: when `advanced=false` (abort path), the `steps` list should include only the steps that ran before the failure, with the failing step carrying its actual `exit_code` and `action="fail"`. The plan sketch implies this but doesn't make it explicit; the implementer should treat it as specified here.

**DNS-gate brittleness — blocking prerequisite amendment to plan_009.**
The reviewer has inspected `skills/awf-kamal-setup/scripts/kamal_setup.py`. The current implementation on the `KamalDnsTimeout` path (lines 83–85) prints only to stderr and returns 3 with no JSON emitted to stdout. The `--json` block at line 90 is reachable only on success (return 0). This means the composer's primary gate-detection branch (`result.details.get("gate") == "dns_propagation"`) will never fire; the composer falls back to the stderr substring match (`"dns_propagation" in result.stderr`). The plan_007/009 assertion that `awf-kamal-setup` "exits 3 with stderr `DNS for <domain> ...` plus a `gate.hit name=dns_propagation` event in its child session log" is confirmed — but the `gate.hit` event is in the **session log**, not in the subprocess's **stdout JSON**. The composer reads stdout, not the session log. **The plan_009 amendment is therefore required before plan_010 can be implemented, not optional.** The amendment is a one-line change: on `KamalDnsTimeout`, before returning 3, emit `{"action": "gate", "gate": "dns_propagation", "reason": str(exc)}` to stdout when `--json` is set. This makes the structured detection path reliable and demotes the stderr-substring fallback to a defensive second check. This amendment should land as a follow-up commit to the plan_009 branch (or a separate fix-commit) before plan_010 implementation begins.

**Tier-3 happy-path — merge gate or follow-up?**
MERGE GATE. `spec.md § B5` and the build-order note at `spec.md` "PR 4" both state: "End-to-end test on a real test domain is the merge gate." The plan's implementation order item 6 ("Tier-3 manual happy-path — record in PR description; not a CI gate") is accurate about it not being a CI gate, but calling it "not a CI gate" should not be read as optional. The tier-3 run should be recorded in the PR description with a screenshot or log excerpt before the PR is marked ready-for-merge. Given that plan_010 is the architectural proof point for Phase B, skipping or deferring tier-3 is not acceptable. This is not a change to the plan's implementation order — it is a clarification that step 6 is blocking the PR merge even though it runs manually.

---

### Pass 1 (2026-06-01) — code review

**Reviewer:** Sonnet 4.6
**Verdict: changes-requested — 1 Blocker, 2 Majors. Not accepted until Blocker is resolved.**

**Verified:** 265/265 tests green (`uv run --with pytest … pytest tests/ -v`). ruff clean (`uv run --with ruff ruff check` — All checks passed). Diff stat: 4 files changed, 2243 insertions — plan doc, SKILL.md, script, tests. No regressions in baseline.

---

#### Checklist pass

- [x] `log.session(composer="awf-stage-mvp-play", target="mvp-play")` wraps the main orchestration body (lines 536, 529 for dry-run). Correct.
- [x] Each atomic skill invoked via `_invoke()` as subprocess with `--json` appended unconditionally (line 122) and `cwd=str(ctx.root)` (line 131). Correct.
- [x] On mid-run failure: `return final_exit` inside the `with log.session` block before `advance_anchor` is ever called. Anchor not advanced. Tests `test_step5_fails_exits_3` and `test_step3_fails_no_subprocess_after` verify this.
- [x] DNS gate: `step_kamal_setup()` detects `exit_code == 3` and `payload.get("gate") == "dns_propagation"`, emits `log.gate(name="dns_propagation", ...)`, prints stderr instruction, returns `StepResult(exit_code=5)`. Caller in `main()` checks `r7.exit_code == 5` and exits without calling `log.error` a second time (clean gate path, lines 598–603). Correct.
- [x] `awf-kamal-setup` (inspected on this branch) now emits structured JSON `{"action":"gate","gate":"dns_propagation","reason":...}` to stdout when `--json` is set and `KamalDnsTimeout` fires (lines 83–93 of `kamal_setup.py`). The plan's Pass 1 blocking prerequisite was already resolved in the implementation — the structured gate path is live and tested. No stderr-substring fallback is needed or present.
- [x] Re-run / skip: `test_skip_actions_on_rerun` confirms all 8 steps are still invoked (conservative `needed()` per T1) and anchor advances. `action=skip` entries appear in JSON output steps list.
- [x] `--dry-run`: `test_dry_run_no_subprocess` asserts `call_count == 0`. The dry-run path calls `print_dry_run_plan()` and returns 0 without entering the main orchestration block. `log.intent` and `log.note(action=dry-run)` emitted per step.
- [x] Sessions are flat: no nested `log.session` anywhere. The dry-run path and the main path each open exactly one session and never recurse.
- [x] Exit code table consistent: 0/1/2/3/4/5. `_map_child_exit` maps unexpected codes to 3 (line 467–468). Exit 5 is only emitted by the kamal_setup gate path and is never fed back into `_map_child_exit` (the gate path returns before that call).
- [x] `test_integration_happy_path` uses real subprocesses with fake scripts on disk; `test_integration_nonzero_exit` covers the non-zero integration path.
- [x] SKILL.md frontmatter: `name`, `description`, 8-step sequence, inputs table (`--dry-run`, `--play-hostname`, `--port`, `--node-version`, `--json`), exit-code table including code 5, prerequisites with `awf-doctor` reference. All present.

---

#### Blocker

**B1 — DATABASE_URL leaks into `process.invoke` log event via `safe_args` dead code.**

`_invoke()` accepts a `safe_args: dict | None = None` parameter and the call-site comment at line 236 states "safe_args must never include the secret value — only key + source". However, `safe_args` is never used inside `_invoke`. The full `cmd` list is built at line 122 as `["uv", "run", <script>] + args + ["--json"]` and this list — which for step 4 contains `["--value", "<postgres-connection-string>"]` — is passed verbatim to `log.process(cmd=cmd, …)` at line 137. `log.process` stores `"cmd": cmd` in the `process.invoke` JSONL event (confirmed in `lib/log.py` lines 716–717). The DATABASE_URL value therefore lands in the project event log in plaintext on every run.

Fix: either (a) build a redacted `safe_cmd` list before calling `log.process` (replacing the `--value` argument with `<redacted>`), or (b) have `_invoke` accept a `log_cmd: list[str] | None` parameter that overrides the logged command. Option (a) is simpler — a 3-line helper that replaces the value after any `--value` flag with `<redacted>`, applied only when building the argument for `log.process`. The subprocess itself still receives the real `cmd`; only the log call gets the sanitised version.

No test currently catches this because the test suite does not assert the content of `process.invoke` log events. A regression test asserting that no `process.invoke` event contains the literal connection string value should accompany the fix.

---

#### Majors

**M1 — Step 4 fail-fast guard on empty `connection_string` is unreachable on re-run.**

`step_app_secret_set(ctx, connection_string)` exits 4 if `connection_string` is falsy. The value is sourced at line 566 as `r3.payload.get("connection_string", "")`. On a re-run where step 3 returns `action="skip"`, the atomic skill (`awf-neon-branch`) emits only `{"action": "skip"}` — the `connection_string` field is not present in skip payloads (the branch already exists; there is nothing to return). So `connection_string` will be `""` on every re-run, and `step_app_secret_set` will exit 4 with "step 3 (awf-neon-branch) must provide it" even though the secret was already written successfully on the first run. This breaks the re-run / idempotency guarantee that is the core AC of the composer.

Fix: the composer should also check `ctx.infra.neon.branch_connection_secret_ref` or a stored connection-string field in `infra.json` as a fallback source. Alternatively, `awf-neon-branch` should always emit `connection_string` in its JSON output (including on `action="skip"`) — but that requires a plan_009 amendment. The simpler fix at the composer layer is to also check `ctx.infra.neon` for an existing secret reference, and if `DATABASE_URL` is already present in `.kamal/secrets`, treat the step as skippable without re-reading the connection string. The exact approach is for the implementer to choose, but the current code path will break every re-run at step 4.

Test coverage gap: `test_skip_actions_on_rerun` provides a `connection_string` in the step-3 skip response (`_build_happy_responses` is reused for skip responses in this test). This hides the bug — a realistic `action="skip"` payload from `awf-neon-branch` would not include `connection_string`. The test should be updated to use a step-3 skip payload without `connection_string` to exercise the actual re-run scenario.

**M2 — `_invoke` skill-name-to-script-path mapping is fragile for multi-word skills.**

Line 120: `skill_script = AWF_HOME / "skills" / skill / "scripts" / f"{skill.replace('-', '_')}.py"`. This mapping works for all eight current skills (e.g., `awf-shared-infra-get` → `awf_shared_infra_get.py`). However, `awf-cf-dns-record` would resolve to `awf_cf_dns_record.py`, which is correct, while a hypothetical future skill `awf-app-secret-set` resolves to `awf_app_secret_set.py` — which is also correct. The pattern is consistent with the rest of the codebase. Not a current defect. However: there is no existence check before building the command, so a typo in a skill name produces a `uv run <nonexistent-path>` invocation that exits non-zero with a confusing "file not found" stderr rather than a composer-level error. Suggest adding a `skill_script.exists()` check before invoking the subprocess and exiting 4 with a clear message if the script is missing. This is Minor for now, raised to Major because the integration test exercises this path with fake scripts that do exist — a missing-script scenario is untested.

---

#### Minors

**m1 — Duplicate `log.note("starting step …")` and `_invoke`'s own `log.note(action=start)` per step.**
Each step in `main()` emits `log.note("starting step shared_infra_get", …)` (line 537) and then `_invoke` emits a second `log.note(f"step={step_name} action=start …")` (line 124). Two near-identical events are written to the log for every step start. Harmless but noisy; consider removing the per-step `log.note` calls in `main()` and relying solely on `_invoke`'s own start note.

**m2 — `root = anchor._path.parent.parent` accesses a private attribute.**
Line 509 uses `anchor._path` (underscore prefix). If `ProjectAnchor` adds a public `root` property or renames `_path` in a future plan, this silently breaks. Suggest exposing a `anchor.project_root` property in `lib/state.py`, or using `anchor._path.parent` (the `.awf/` dir) and calling `.parent` once in the composer. Low risk today; noted for the next state-model refactor.

**m3 — `_emit_output` always called inside `with log.session` on failure paths but the function itself has no session guard.**
If `_emit_output` were ever called outside a session (e.g., in a future refactor that adds an early-exit before the `with` block), `log.note` calls inside it would emit orphan events. The current code is safe. Minor forward-looking concern only.

---

#### Status update

Update the status log at the top of this file:

| 2026-06-01 | changes-requested | Reviewer | Code review Pass 1: 1 Blocker (DATABASE_URL plaintext in process.invoke log), 2 Majors (re-run breaks at step 4 on skip; missing-script path untested). Not accepted. Fix and re-submit for Pass 2. |
| 2026-06-01 | accepted | Reviewer | Code review Pass 2: B1 + M1 + M2 all resolved; 269/269 green; ruff clean. Accepted. |

---

### Pass 2 (2026-06-01) — code review

**Reviewer:** Sonnet 4.6
**Verdict: accepted.**

**Verified:** 269/269 tests green (`uv run --with pytest --with pydantic --with httpx --with hcloud --with pyyaml pytest tests/ -v`). ruff clean (`uvx ruff check` — All checks passed). 4 net-new tests since Pass 1 (265 → 269).

**B1 resolved.** `_redact_cmd` helper (lines 105–121) replaces the value following `--value` and the path following `--from-file` with `<redacted>`. `_invoke` now builds `redacted_cmd = _redact_cmd(cmd)` and passes that to `log.process`; the subprocess still receives the original `cmd`. The dead `safe_args` parameter is gone. `TestSecretRedaction::test_database_url_not_in_log_after_secret_set` asserts that no `process.invoke` log event contains the literal connection string — regression is covered.

**M1 resolved.** `_kamal_secrets_has_database_url` (lines 251–257) checks `.kamal/secrets` for an existing `DATABASE_URL=` line before attempting to read the connection string from step 3. `step_app_secret_set` returns `StepResult(exit_code=0, payload={"action": "skip"})` immediately when the key is already present, bypassing the falsy-`connection_string` guard entirely. The re-run scenario (step 3 returns `{"action": "skip"}` with no `connection_string` field) no longer exits 4.

**M2 resolved.** `_invoke` checks `skill_script.exists()` at line 145 before building the subprocess command; exits 4 with a clear message naming the missing script. `TestMissingSkillScript::test_missing_script_exits_4` and `test_missing_script_no_subprocess_called` cover both the exit code and the no-subprocess guarantee.

**Path-building fix verified.** `script_stem = skill.removeprefix("awf-").replace("-", "_")` (line 141) correctly strips the `awf-` prefix so `awf-neon-branch` resolves to `<AWF_HOME>/skills/awf-neon-branch/scripts/neon_branch.py`, not `awf_neon_branch.py`. The fix is consistent across all eight invoked skills.

**Pass 1 Minors (m1–m3) acknowledged as carry-forwards.** No new minors introduced. Tier-3 real-domain gate remains a PR-merge prerequisite per Pass 1 ruling; that is unchanged.
