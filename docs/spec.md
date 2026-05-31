# Build Spec

Build-ready specification for the next development phase. Implements
the design locked in by ADRs D-001 through D-009 (see
[`decisions.md`](decisions.md)).

This document is the answer to "what do we build, in what order, and
when is each piece done." It does not re-state architecture (see
[`02-architecture.md`](02-architecture.md),
[`07-multi-stage-architecture.md`](07-multi-stage-architecture.md))
or operational rules (see [`01-principles.md`](01-principles.md),
[`08-logging.md`](08-logging.md)).

---

## Build phases at a glance

| Phase | Goal | Unblocks |
|---|---|---|
| **A. Foundation** | `.awf/` schemas, project locator migration, logging library | Everything else |
| **B. S3 enablers** | Hetzner/Neon/Kamal libs + atomic skills + first composer | The MVP-play pipeline |
| **C. Affordances** | `awf-status` rebuild, `awf-help` redesign, `awf-doctor` scoping, `awf-log` skill | LLM ergonomics across all stages |
| **D. Cheap-essentials** | `awf-cost`, `awf-teardown`, `awf-env-create` | Closing the cost lifecycle |
| **E. Deferred** | Email, uptime, Sentry, recovery, fleet ops | Earn-their-place when concrete demand appears |

Phases A and B must land before C is useful; C can be reordered
internally. D and E are gated on real usage.

---

# Phase A — Foundation

Without these, nothing else can be built coherently. All four ship
together as the foundation PR.

## A1. `.awf/` schemas (D-003)

**Module:** `lib/state.py` (new, sibling of `lib/passport.py`).

**Purpose.** Pydantic v2 dataclasses for `ProjectAnchor`, `Infra`,
`Shared`. Each supports `load()`, `save()`, `validate()`. Schemas
tolerate extra keys; invalid required-field state raises with the
bad key in the message.

**Public API.**

```python
from lib.state import ProjectAnchor, Infra, Shared

anchor = ProjectAnchor.load()          # walks up; raises if not found
anchor.stage = "mvp-play"; anchor.save()

infra = Infra.load_or_create()         # creates empty .awf/infra.json if absent
shared = Shared.load_or_create()       # ~/.config/awf/shared.json, lazy
```

**Behaviour.**

- `load()` calls `find_project_root()` (D-004) and reads the file.
- `save()` writes atomically via temp-file-and-rename.
- Every `save()` emits a `state.change` log event (D-002) with the
  diff between before and after.
- `validate()` runs Pydantic + cross-field invariants (e.g.,
  `stage == "landing"` implies `has.infra == False`).

**Errors handled.**

- Missing file (anchor) → `ProjectNotFound` with cwd context.
- Malformed JSON → `StateCorruptError` pointing to the byte offset.
- Schema-invalid → `StateValidationError` listing failing fields.

**Acceptance criteria.**

- [ ] Round-trip: load → mutate → save → load yields identical state.
- [ ] Forward-compat: a file with extra top-level keys loads without
      error.
- [ ] `save()` produces a `state.change` log entry.
- [ ] Cross-field invariants enforced (test: `stage="landing"` with
      `has.infra=true` rejects).
- [ ] Atomic write: simulated mid-write crash leaves prior file
      intact.

## A2. `lib/project.py` dual-walk (D-004)

**Module:** `lib/project.py` (extend existing).

**Public API.**

```python
from lib.project import find_project_root, ensure_anchor

root = find_project_root()      # walks up for .awf/project.json OR passport.json
                                # raises ProjectNotFoundError if neither
ensure_anchor(root)             # idempotent: creates .awf/project.json from
                                # passport.json fields if missing
```

**Behaviour.**

- Prefers `.awf/project.json` when both exist.
- On a passport-only project, `find_project_root()` succeeds but
  flags `anchor_missing=True` on the returned object.
- `ensure_anchor()` is the migration entry point. Called explicitly
  by `awf-migrate`, implicitly by composer skills on first run.
- Migration is **append-only**: never deletes `passport.json`, never
  moves passport keys.

**Acceptance criteria.**

- [ ] Legacy project (passport only): `find_project_root()` returns
      root with `anchor_missing=True`.
- [ ] After `ensure_anchor()`: `.awf/project.json` exists with
      `stage="landing"`, `has.passport=true`.
- [ ] New project (both files): `.awf/project.json` is preferred.
- [ ] No project (neither file): raises `ProjectNotFound` with
      message including cwd and the directories walked.

## A3. `lib/log.py` (D-002)

**Module:** `lib/log.py` (new).

**Public API.** As specified in [`08-logging.md`](08-logging.md):

```python
log.session(composer, target)         # context manager
log.invoke(skill, args)               # context manager, redacted
log.api(provider, method, path, status_code, resource_id)
log.state_change(file, key, before, after)
log.gate(name, reason, instructions)
log.error(msg, hint=None)
log.intent(action, impact)            # gated by --dry-run / AWF_LOG_INTENTS
log.note(text, by="human")
```

**Behaviour.**

- Writes to `<project>/.awf/log.jsonl`, append-only, atomic for
  events <4 KB; `fcntl.flock` for larger.
- ULID session IDs threaded through context managers.
- Redaction via `safe_log()` denylist (D-002).
- Logging never raises — `OSError` becomes a stderr warning.
- Each `session.end` writes one summary line to
  `~/.config/awf/sessions.jsonl` (the central index).

**Acceptance criteria.**

- [ ] 1000 concurrent appends produce 1000 valid JSON lines, no
      interleaving.
- [ ] Bearer token in `args` is redacted to `***`.
- [ ] Read-only file → stderr warning, no exception raised.
- [ ] Session context manager auto-emits `session.start` /
      `session.end` with `duration_ms`.
- [ ] Central index gains one line per completed session.

## A4. `awf-migrate` skill (new)

**Purpose.** Explicit one-shot upgrade for legacy projects.

**Behaviour.**

- Calls `ensure_anchor()`.
- Reports what changed (one line per file).
- Idempotent: running on already-migrated project is a no-op with
  "already migrated" message.

**Acceptance criteria.**

- [ ] Idempotent.
- [ ] Emits `state.change` events for every file touched.
- [ ] Exits 0 on no-op; 0 on successful migration; non-zero only on
      I/O failure.

---

# Phase B — S3 enablers

Goal: a project at S1/S2 can be promoted to S3 (MVP-play) with one
command. Everything below ships together as the "S3 PR."

## B1. `lib/hetzner.py` (port from `hetzner_deploy`)

**Purpose.** Idempotent Hetzner Cloud client, scoped to what S3–S5
needs: servers, networks, firewalls, LBs, SSH keys.

**Source.** Port the proven client from
`/Users/emson/Dropbox/devel/projects/hetzner_deploy/packages/common`
and `packages/provision`. Do not depend on the external package;
copy the code, keep the tests.

**Public API.**

```python
from lib.hetzner import HetznerClient
hz = HetznerClient.from_env()

server = hz.servers.get_or_create(name, type="cx22", image="docker-ce",
                                   ssh_keys=[...])
hz.firewalls.ensure(name, rules=[...])
hz.lb.get_or_create(name, targets=[...], health_check={...})
```

**Operating rules.**

- Every mutating call wraps the API in search-or-create (A1).
- Every call emits `api.call` log events.
- Network errors are surfaced with retry hints, not retried
  automatically (composers decide retry policy).

**Acceptance criteria.**

- [ ] Port the 25 unit tests from `hetzner_deploy` that exercise the
      ported public API surface (search-or-create semantics, error
      shape, logging contract). The remaining upstream tests cover
      `ProvisioningState` / CLI / YAML config and are not in scope
      per D-001. See plan_005 § "Test reality check".
- [ ] Re-creating an existing server is a no-op that logs `skip`.
- [ ] Bearer token never appears in log output.

## B2. `lib/neon.py` (new)

**Purpose.** Idempotent Neon client: projects, branches, connection
strings.

**Public API.**

```python
from lib.neon import NeonClient
neon = NeonClient.from_env()

project = neon.projects.get_or_create(name)
branch = neon.branches.get_or_create(project_id, name="myapp")
conn_str = neon.branches.connection_string(branch_id, role="app")
```

**Acceptance criteria.**

- [ ] `get_or_create` is idempotent; second call returns same IDs.
- [ ] Connection string includes `?sslmode=require`.
- [ ] Token never appears in log output.
- [ ] `branches.delete()` exists and works (needed for teardown).

## B3. `lib/kamal.py` (new)

**Purpose.** Render `config/deploy.yml` from `ProjectAnchor` +
`Infra`; thin subprocess wrapper around the `kamal` CLI.

**Public API.**

```python
from lib.kamal import KamalConfig, KamalRunner

KamalConfig(anchor, infra).render(path="config/deploy.yml")
runner = KamalRunner(cwd=project_root)
runner.setup()       # first-time on server: install Docker, etc.
runner.deploy()      # roll forward
runner.rollback()
runner.app_logs(tail=200)
```

**Behaviour.**

- `render()` is pure: no I/O beyond reading anchor/infra and writing
  the YAML file. Deterministic; whitespace stable; safe to re-run.
- `KamalRunner` shells out to `kamal`; captures stdout+stderr; emits
  `api.call`-equivalent log events (`type: "process.invoke"`).
- `setup()` waits for DNS A-record to resolve to server IP before
  invoking (handles the DNS-before-TLS race; D-001 op rule #1).

**Acceptance criteria.**

- [ ] `render()` output diff-stable across runs (no timestamps in
      YAML).
- [ ] DNS-not-propagated produces a `gate.hit` event with name
      `dns_propagation`, exits non-zero, instructions explain what
      to wait for.
- [ ] `kamal deploy` failure surfaces stderr in the `error` event
      with a hint pointing at common causes (Dockerfile, secrets,
      registry auth).

## B4. Atomic skills (Phase B)

Each follows the standard skill anatomy (see
[`04-skill-authoring.md`](04-skill-authoring.md)) and emits
`skill.invoke` / `skill.complete` log events. All idempotent.

| Skill | Owns | Reads | Writes |
|---|---|---|---|
| `awf-shared-infra-get` | the play server + play neon project | `~/.config/awf/shared.json` | same |
| `awf-hetzner-server` | one Hetzner VM by name | env, anchor | `.awf/infra.json` |
| `awf-neon-project` | one Neon project | env, anchor | `.awf/infra.json` |
| `awf-neon-branch` | one Neon branch | env, anchor, infra | `.awf/infra.json` |
| `awf-app-dockerize` | Dockerfile + /up route + lib/db.ts | anchor | source tree |
| `awf-app-secret-set` | one Kamal secret | env | `.kamal/secrets` |
| `awf-kamal-config` | `config/deploy.yml` | anchor, infra, shared | `config/deploy.yml` |
| `awf-kamal-setup` | first-time server bootstrap | infra | none |
| `awf-kamal-deploy` | one deploy cycle | infra | none (kamal-managed) |
| `awf-cf-dns-record` | one CF DNS record | infra | passport.cloudflare |

**Acceptance criteria (per skill).**

- [ ] Idempotent: second invocation with same inputs logs `skip`.
- [ ] Each mutation emits both `api.call` (or `process.invoke`) and
      `state.change` events.
- [ ] Failure modes documented in SKILL.md "errors handled" section.
- [ ] Each can be run standalone (LLM or human can invoke directly).

## B5. `awf-stage-mvp-play` composer (the proof point)

**Purpose.** Promote a project from any prior stage to `mvp-play`.

**Behaviour.** Reads anchor, computes diff against target state,
calls atomic skills in dependency order, terminates on first gate.

**Sequence (per D-001 operational rules):**

1. `awf-shared-infra-get` — ensure play server + play Neon project.
2. `awf-app-dockerize` — code-level scaffolding if missing.
3. `awf-neon-branch` — branch on the shared Neon project.
4. `awf-app-secret-set` — write `DATABASE_URL` into `.kamal/secrets`.
5. `awf-kamal-config` — render `config/deploy.yml`.
6. `awf-cf-dns-record` — A record to play server IP, grey cloud.
7. **Wait/poll**: `dig +short` matches server IP (hard timeout →
   `gate.hit dns_propagation`).
8. `awf-kamal-setup` (first time on shared server only) →
   `awf-kamal-deploy`.
9. Update anchor: `stage = "mvp-play"`, `has.infra = true`,
   `has.kamal = true`.

**Acceptance criteria.**

- [ ] Wraps the whole run in one `log.session(composer="awf-stage-mvp-play", target="mvp-play")`.
- [ ] On mid-run failure, anchor is **not** advanced; partial
      `infra.json` reflects what was created.
- [ ] Re-run after fix: skips completed sub-steps (idempotency).
- [ ] On DNS-propagation gate: exits cleanly with `gate.hit` event,
      stderr instruction, non-zero exit code.
- [ ] End-to-end happy path on a real test domain produces a live
      site within 5 minutes.

---

# Phase C — Affordances

These reshape the LLM/human surface. Ship one at a time; each is
self-contained and unlocks immediate ergonomic value.

## C1. `awf-log` skill (D-002)

CLI surface as specified in [`08-logging.md`](08-logging.md):

```
awf-log tail [-n 50]
awf-log session [<id>|last]
awf-log find <pattern>
awf-log diff
awf-log note "<text>"
awf-log replay <session>
awf-log sessions [--days 30]
```

**Acceptance criteria.**

- [ ] `tail -n 5` prints 5 lines in last-out-first order.
- [ ] `session last` finds the most recent `session.start` and
      prints all events between it and its matching `session.end`.
- [ ] `replay` summarises a session as a 1-paragraph narrative
      (LLM-renderable) plus a step list.
- [ ] `find` accepts regex; output is JSON Lines unchanged.

## C2. `awf-status` rebuild (D-007)

Replace existing implementation. Output order is fixed (Project,
Stage, Drift, Recent, Next). `--json` flag for machine output.
`--verbose` extends event tail.

**Acceptance criteria.**

- [ ] No project anchor → `Stage: none` + help suggestion. Exit 0.
- [ ] Drift detection: compares state files against world via
      per-provider clients; lists divergences and names the atomic
      skill that would re-converge.
- [ ] `--json` output validates against a documented schema in
      `lib/state.py`.
- [ ] LLM-directive line in SKILL.md: "Run this first when location
      is uncertain."

## C3. `awf-help` redesign (D-008)

Three modes auto-detected: no-project, in-project, `--overview`.
Read-only.

**Acceptance criteria.**

- [ ] No `.awf/project.json` upward → fresh-start mode with one
      recommended next command.
- [ ] In-project mode lists the named composer for `stage+1` and
      the atomic skills relevant to the current stage.
- [ ] `--overview` is the full catalogue grouped by stage; links
      docs 07 and 08.
- [ ] Never mutates state; never calls external APIs.

## C4. `awf-doctor` scoping (D-009)

Add `--for-stage <name>` and `--for-skill <skill>` flags. Implement
recent-error surfacing from the log.

**Acceptance criteria.**

- [ ] `--for-stage mvp-play` checks Hetzner, Neon, GHCR, SSH key
      reachability; skips Bing IndexNow, GSC.
- [ ] `--for-skill awf-kamal-deploy` checks registry auth, kamal
      CLI presence, ssh to target server.
- [ ] If last session's log contains a credential-shaped error
      (`401`, `403`, `auth` in error message), doctor leads with
      that specific check.
- [ ] Default (no flag) behaviour unchanged for backwards-compat.

---

# Phase D — Cheap-essentials

Earn-their-place ordering, but worth specifying now so they're
build-ready when triggered.

## D1. `awf-cost`

**Purpose.** Read `.awf/infra.json` across all projects (via the
central sessions index path hint) and report €/mo per project.

**Behaviour.**

- Per-resource cost table in `lib/costs.py`; manually maintained.
- Output: table with project, stage, monthly cost, breakdown.
- Hook called by composer promotion: "promoting to prescale will
  add ~€X/mo, confirm?"

**Acceptance criteria.**

- [ ] Returns correct sum for known fixtures (Hetzner CX22 = €5/mo,
      Hetzner LB = €5/mo, Neon Free = €0).
- [ ] Composer hook prints diff before/after promotion.

## D2. `awf-teardown` + idle detection

**Purpose.** Destroy dedicated infra. Flag idle projects from
`awf-status`.

**Behaviour.**

- Destroys Hetzner VMs/LBs owned by this project, dedicated Neon
  projects, registry images. Leaves shared resources alone.
- `--scorched-earth` removes shared Neon branches and rolls back CF
  DNS / Pages too.
- Idle detection: `awf-status` flags projects where last `session.end`
  is >90 days ago.

**Acceptance criteria.**

- [ ] Default teardown leaves play server, play Neon project intact.
- [ ] `--scorched-earth` removes only resources tagged as owned by
      this project's slug.
- [ ] Dry-run prints intended deletions; nothing actually destroyed
      without `--confirm`.

## D3. `awf-env-create` (Neon branches for staging)

**Purpose.** Create a staging branch off the project's Neon branch
and a parallel Kamal deploy target.

**Acceptance criteria.**

- [ ] Creates `<slug>-staging` Neon branch.
- [ ] Renders `config/deploy.staging.yml` for `kamal deploy -d staging`.
- [ ] Records branch in `infra.json.neon.environments`.

---

# Phase E — Deferred

Items in
[`notes/concepts-and-priorities.md`](notes/concepts-and-priorities.md)
under tier 2+ stay deferred until concrete demand:

- Email setup (Resend / SES)
- Uptime monitoring (UptimeRobot)
- Error tracking (Sentry)
- Project recovery (`awf-recover`)
- Fleet ops (`awf-fleet ...`)
- Stage-specific templates (`saas-mvp-v1`)

Each will earn a D-NNN ADR + its own spec section when triggered.

---

# Build order summary

1. **PR 1 — Foundation:** A1 + A2 + A3 + A4 (one PR; they're entangled).
2. **PR 2 — S3 enablers, libs only:** B1 + B2 + B3 (no skills yet).
3. **PR 3 — S3 atomic skills:** B4 (8 skills, one commit each).
4. **PR 4 — First composer:** B5 (`awf-stage-mvp-play`). End-to-end
   test on a real test domain is the merge gate.
5. **PR 5 — `awf-log`:** C1.
6. **PR 6 — Status/help/doctor:** C2 + C3 + C4 (parallelizable).
7. **PR 7+ — Phase D items** in priority order.

Each PR is mergeable on its own. PR 4 is the milestone that proves
the architecture.

---

# Five Questions (handoff readiness check)

1. **Could a competent developer build this from the spec alone?**
   Yes for Phases A and B (interfaces, behaviours, acceptance
   criteria documented). Phase C needs the existing `awf-status` /
   `awf-help` / `awf-doctor` source to diff against, but that's in
   the repo.
2. **Is every error condition handled?** Each module lists the
   errors it raises. Composer error semantics covered by D-001
   operational rules (idempotency + gates).
3. **Are the interfaces precise?** Phase A and B yes. Atomic skill
   SKILL.md files will fill in last-mile detail when written.
4. **Is the build order clear?** Yes — phases A→B→C, with PR 4 as
   the proof milestone.
5. **Do acceptance criteria trace to charter success?** Charter is
   implicit in `00-plan.md` ("definitive cheap website
   deployment"); each acceptance criterion serves a concrete
   capability listed there. Explicit trace deferred.
6. **Do test specifications cover every acceptance criterion?** Not
   yet — acceptance criteria here imply test cases but `tests/`
   layout is empty. Test-spec generation is the first task in each
   PR.

The spec is build-ready for Phase A and B. C, D, E land as
incremental ADRs + spec amendments.
