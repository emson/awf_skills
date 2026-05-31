# Decisions

Append-only log of architectural decisions. Each entry records the
question, the chosen answer, the alternatives considered, and the
trigger that would cause us to revisit.

Format follows lightweight ADR conventions; see SpecForge
`decision-patterns.md` for the source format.

---

## D-001 — Multi-stage architecture pattern

**Date:** 2026-05-31
**Status:** Accepted

**Context.** The suite was scoped to launching a static landing page
on Cloudflare Pages. The user now wants the same project to be
promotable through five stages: landing, demo, MVP-play (shared
Hetzner + shared Neon), prescale (dedicated Hetzner + LB + dedicated
Neon), and scale (multi-server + cache). The question was how to
extend the skill model and the on-disk contract without breaking the
existing S1 design.

**Decision.**

1. Adopt a **five-stage ladder** (landing → demo → mvp-play →
   prescale → scale) with deliberate, human-approved promotion.
2. Split skills into **two layers**: atomic resource skills
   (idempotent, one-thing-each) and composer skills (one per stage,
   declarative target-state).
3. Use **Kamal** as the deploy abstraction for S3–S5, hiding the
   1-server vs N-servers difference behind a YAML config.
4. Introduce a new **project anchor** at `.awf/project.json`. It
   carries identity, slug, stage, and a `has:` pointer set. Skills
   walk up to find it.
5. Leave **`passport.json` exactly as designed** — landing-page
   contract, owned by S1/S2. It is no longer the project spine.
6. Stage-specific concerns get their own files:
   `.awf/infra.json` for S3+ resource IDs, `config/deploy.yml` and
   `.kamal/secrets` as Kamal's own native files (we generate but do
   not duplicate them).
7. User-scope shared resources (the play Hetzner server, the play
   Neon project) live in `~/.config/awf/shared.json`.

See [`07-multi-stage-architecture.md`](07-multi-stage-architecture.md)
for the full pattern.

**Alternatives rejected.**

- *Grow `passport.json` to hold infra config.* Conflated identity
  with stage-specific state; bloated the schema with keys that are
  null at any given stage; broke the original "easy to toggle
  landing-page features" intent.
- *Reuse `hetzner_deploy` as a subprocess dependency.* Imposed a
  uv-workspace install + extra `HETZNER_DEPLOY_HOME` env on every
  awf-skills user. Broke portability (A2). Better: port the API
  client into `lib/hetzner.py` and treat `hetzner_deploy` as a
  reference implementation.
- *One mega-skill per stage.* Lost surgical-fix ability and hid the
  dependency graph from the LLM.
- *Terraform / Pulumi for IaC.* Heavy, stateful, conflicted with
  the per-project passport/anchor state model.
- *Auto-promote between stages.* Stages are deliberate; auto-scaling
  belongs inside S5, not across the ladder.

**Revisit if.**

- Kamal becomes unmaintained or a clearly better deploy tool emerges.
- A real need appears to skip S2 entirely (most projects so far do).
- The two-layer model produces too many composers and we need a
  generic "promote to target" engine instead.

---

## D-002 — Logging model

**Date:** 2026-05-31
**Status:** Accepted

**Context.** The suite mutates state across CF, Namecheap, Fathom,
GSC, Bing, and (from S3) Hetzner, Neon, and Kamal. Today the only
records of those mutations are `passport.json` (current state) and
the provider dashboards (distributed, not queryable). That leaves
five gaps: debugging, LLM session handover, audit trail, drift
detection, and resumption beyond gates. The question was how to add a
history layer without an external dependency or operational burden.

**Decision.**

1. **Project-local append-only JSON Lines** at `.awf/log.jsonl` is
   the source of truth for history. **Gitignored by default**;
   opt-in to track is a one-line user change.
2. **Tiny central index** at `~/.config/awf/sessions.jsonl` carries
   one summary line per completed session, enabling cross-project
   queries.
3. **State vs history separation is strict.** Passport / project.json
   answer "what is true now"; the log answers "what happened." Neither
   tries to be the other.
4. **Event schema** is a small required header (ts, session, project,
   stage, actor, type, skill, result, duration_ms) plus a typed
   `data` payload. Schemas tolerate extra keys; old events stay
   readable.
5. **Default-deny redaction** for secrets via `safe_log()`. Raw
   headers, `.env` values, and credentials are never logged.
6. **Logging never raises.** Best-effort writes; stderr warning on
   failure; skills continue.
7. **`lib/log.py`** exposes a tiny API (`session`, `invoke`, `api`,
   `state_change`, `gate`, `error`, `intent`, `note`) with context
   managers that thread `session_id` automatically.
8. **`awf-log` skill** is the human + LLM surface (`tail`, `session`,
   `find`, `replay`, `diff`, `note`, `sessions`).
9. **`awf-status` prints the last 5 events at the top of its
   output**, so "check status" naturally surfaces history.

See [`08-logging.md`](08-logging.md) for the full spec.

**Alternatives rejected.**

- *SQLite database.* Binary; not greppable, cattable, or
  diff-friendly; premature for query volume we'll never reach.
- *Centralized log in `~/.config/awf/`.* Breaks project-locality;
  ties history to user's machine; loses on directory moves.
- *External observability SaaS (Honeycomb, Datadog).* Massive overkill
  for a one-person tooling layer; adds dependency and cost.
- *Git commits as the log.* Noisy; can't capture API calls; only
  records state, not intent or order.
- *Per-run files (`logs/<ts>.jsonl`).* Directory clutter; cross-session
  grep awkward. Single project-local file wins.

**Locked-in defaults** (the open questions from the design pass):

- Gitignored by default (Q1).
- `awf-status` shows last 5 events (Q2).
- `intent` events only on `--dry-run` (Q3); `AWF_LOG_INTENTS=1` opt-in.
- ULID for session IDs, ~20-line inline implementation (Q4).

**Revisit if.**

- Log files routinely exceed 10 MB and tail/grep latency degrades.
- Need to query across more than ~20 projects (the index may then
  need indexing structure, not just append).
- A clear case for remote shipping emerges (compliance, team audit).

---

## D-003 — `.awf/` schemas (project.json, infra.json, shared.json)

**Date:** 2026-05-31
**Status:** Accepted (closes D-OPEN-E)

**Context.** D-001 introduced a project anchor at `.awf/project.json`,
an infra ledger at `.awf/infra.json`, and a user-scope shared-infra
file at `~/.config/awf/shared.json`. Sketches existed; concrete
schemas were deferred until needed for the first S3 composer build.

**Decision.** Lock the three schemas as below. Pydantic v2 dataclasses
in `lib/state.py` (sibling of `lib/passport.py`). Schemas tolerate
extra keys (forward-compat). All three are validated on read; invalid
state is a hard error with a pointer to the bad key.

`.awf/project.json` — always present from S1.

```json
{
  "awf_version": "0.1.0",
  "domain": "example.com",
  "slug": "example",
  "stage": "landing",
  "created": "2026-05-31T20:00:00Z",
  "has": { "passport": true, "infra": false, "kamal": false, "content": false }
}
```

`.awf/infra.json` — appears at S3 promotion.

```json
{
  "registry": { "host": "ghcr.io", "image": "user/example", "user": "user" },
  "hetzner": {
    "servers": [
      { "id": "123", "ip": "1.2.3.4", "role": "web", "shared": true, "cost_eur_month": 0 }
    ],
    "lb_id": null,
    "network_id": null
  },
  "neon": {
    "project_id": "neon_proj_xxx",
    "branch_id": "br_xxx",
    "branch_name": "example",
    "mode": "shared-branch",
    "connection_secret_ref": "DATABASE_URL"
  },
  "kamal": { "config_path": "config/deploy.yml", "last_deploy_image": null }
}
```

`~/.config/awf/shared.json` — user-scope, lazily created on first
S3 promotion that needs shared resources.

```json
{
  "play_server": {
    "hetzner_id": "...",
    "ip": "...",
    "hostname": "play.example",
    "registry": "ghcr.io/user",
    "created": "..."
  },
  "play_neon_project_id": "...",
  "default_registry": { "host": "ghcr.io", "user": "user" }
}
```

**Alternatives rejected.**

- *One mega-schema in `project.json`.* Reverts to the bloat that
  D-001 explicitly rejected.
- *Schema-less JSON (no validator).* Loses early detection of
  malformed state, which is exactly the failure mode that makes
  drift recovery hard.

**Revisit if.** A field is needed by multiple skills and consistently
proxied through nested `data:`; promote it to a top-level field.

---

## D-004 — `lib/project.py` dual-walk migration

**Date:** 2026-05-31
**Status:** Accepted (closes D-OPEN-F)

**Context.** The new project anchor is `.awf/project.json` (D-001)
but the current `find_project_root()` walks up looking for
`passport.json`. We need a transition path that doesn't break the
working S1 pipeline.

**Decision.** `find_project_root()` walks up looking for **either**
`.awf/project.json` **or** `passport.json`, preferring `.awf/` when
both exist. On finding a passport-only project, the next mutating
skill that touches the project auto-creates `.awf/project.json` with
`stage: "landing"`, `has.passport: true`, and migrates no other data.
Skills that need `.awf/project.json` (composer, status, log) require
it; calling them on a legacy project triggers a one-shot migration
nudge: *"This project predates the multi-stage model. Run
`awf-migrate` to upgrade, or call any composer to auto-migrate."*

`awf-migrate` is the explicit one-shot skill; it's also called
implicitly by the first composer invocation.

**Alternatives rejected.**

- *Hard cutover.* Cleaner conceptually but breaks every in-flight
  project until migration runs. Violates A11.
- *Keep `passport.json` as anchor.* Walks back D-001.

**Revisit if.** Auto-migration produces a `.awf/project.json` that
diverges from what users hand-write; then formalize the migration
output schema.

---

## D-005 — Image registry default: GHCR

**Date:** 2026-05-31
**Status:** Accepted (closes D-OPEN-A)

**Context.** Kamal needs a Docker registry. We needed a default that
new users can adopt without ceremony.

**Decision.** **GitHub Container Registry (GHCR)** is the default.
- Free for public + private images at small scale.
- Auth via `gh auth token` or a Personal Access Token (PAT) with
  `write:packages`; both fit the layered config model.
- `awf-kamal-config` writes `registry.server: ghcr.io` and
  `registry.username` from `~/.config/awf/shared.json`.
- Registry password is read from `GHCR_TOKEN` via layered config
  (project `.env` → AWF home → user `~/.config/awf/.env`).
- Per-project image path is `ghcr.io/<user>/<slug>`.

Docker Hub remains supported by manual override in `infra.json`;
`awf-doctor` accepts any registry whose CLI auth check succeeds.

**Alternatives rejected.**

- *Docker Hub.* Rate-limits anonymous pulls; private-image pricing
  starts at 1 image; lower predictability.
- *Hetzner-side self-hosted registry.* Adds a bootstrap step and a
  single-point-of-failure on day one. Defensible at scale, not at S3.

**Revisit if.** GHCR usage caps or pricing changes; if `gh` CLI
becomes unavailable or unreliable for the auth flow.

---

## D-006 — Object storage default: Cloudflare R2

**Date:** 2026-05-31
**Status:** Accepted (closes D-OPEN-C)

**Context.** S4–S5 sites need object storage (user uploads, generated
assets, backups). We needed a default that doesn't undermine "cheap."

**Decision.** **Cloudflare R2** is the default.
- S3-compatible API; standard SDKs work.
- Zero egress fees — the dominant cost in object storage at low
  scale.
- Native to the Cloudflare-centric stack; same credential surface.
- `awf-r2-bucket` (deferred until first need) creates the bucket and
  writes `r2.bucket_name`, `r2.public_url` into `infra.json`.

Hetzner Object Storage remains a manual override.

**Alternatives rejected.**

- *Hetzner Object Storage.* Same provider as compute is appealing,
  but egress is not free and CF edge integration is weaker.
- *S3 / B2 / etc.* Add a credential surface for a marginal feature.

**Revisit if.** R2 SLA or pricing changes; or if a project's egress
profile is genuinely zero-CF.

---

## D-007 — `awf-status` as canonical "where am I"

**Date:** 2026-05-31
**Status:** Accepted (closes D-OPEN-G)

**Context.** Humans, LLMs, and skills all need a single answer to
"what stage is this project at, and what should happen next." Today
`awf-status` reports per-provider state; it doesn't carry the stage
or the recent log.

**Decision.** Rebuild `awf-status` as the canonical "where am I"
surface. Output, in fixed order:

```
Project: <slug> (<domain>)
Stage:   <stage>                          ← .awf/project.json.stage
Drift:   <none|description>               ← world vs state diff
Recent:  <last 5 log events>              ← .awf/log.jsonl tail
Next:    <composer skills for stage+1>    ← derived from stage
```

- `--json` outputs machine-readable equivalent.
- `--verbose` lengthens the event tail and prints per-provider state.
- No project anchor → `Stage: none` and a one-line suggestion to run
  `/awf-help`. Doesn't error.
- Drift block calls the atomic skill that would re-converge, by name.
- The LLM directive: when location is uncertain, run `awf-status`
  first. Documented in `04-skill-authoring.md`.

**Alternatives rejected.**

- *Keep `awf-status` per-provider only.* Forces the LLM to compose
  the answer from many calls.
- *Separate `awf-where` skill.* Two skills for one job; LLM
  ambiguity.

**Revisit if.** Output becomes too tall to scan; consider a `--brief`
mode that emits only the first three lines.

---

## D-008 — `awf-help` context-aware redesign

**Date:** 2026-05-31
**Status:** Accepted (closes D-OPEN-H)

**Context.** Today's `awf-help` is closer to a printed catalogue.
For a system with two stage families and a growing skill set, the
useful default is "what's relevant right now."

**Decision.** Three modes, automatic detection:

1. **No `.awf/project.json` upward** → fresh-start mode:
   "You're not in an awf project. Start one with
   `/awf-create-project` or `/awf-launch`. Learn the system with
   `/awf-help --overview`."
2. **In a project at stage X** → contextual mode:
   - Composer to advance: `/awf-stage-<next>` (named).
   - Atomic skills relevant to stage X.
   - Common operations: `status`, `log tail`, `doctor`, `teardown`.
3. **`--overview`** → full catalogue grouped by stage; links to
   `07-multi-stage-architecture.md` and `08-logging.md`.

`awf-help` itself never mutates state. Read-only.

**Alternatives rejected.**

- *Static help text.* Cheaper to ship; useless once the suite grows.
- *Auto-suggest based on log heuristics.* Fragile; users want
  predictability from help.

**Revisit if.** Skill count exceeds ~30 and the contextual list
becomes too long; then group by sub-category within stage.

---

## D-009 — `awf-doctor` scoping flags

**Date:** 2026-05-31
**Status:** Accepted (closes D-OPEN-I)

**Context.** Today doctor checks every credential and CLI on every
invocation. As the suite grows that becomes noise — the LLM stops
using it because it's expensive.

**Decision.** Two scope flags + one targeted hint:

1. **`--for-stage <name>`** — check only credentials + CLIs needed to
   operate at that stage. (e.g. `--for-stage mvp-play` checks
   Hetzner, Neon, GHCR; skips Bing IndexNow.) Stage-to-check mapping
   lives in `lib/doctor.py`.
2. **`--for-skill <skill>`** — narrowest scope: what does this one
   skill need. Useful as a pre-flight before retrying a failed
   atomic skill.
3. **Recent-error surfacing** — doctor reads the last session's log;
   if a credential-shaped error appears, it leads with that specific
   check before doing the full run.

Default (no flag) keeps current behaviour.

**Alternatives rejected.**

- *Always full check.* Friction kills habit.
- *Inferred scope from cwd alone.* Doesn't capture intent ("about
  to do X").

**Revisit if.** Stage definitions diverge enough that
`for-skill` becomes the primary mode and `for-stage` is redundant.

---

## Open decisions

Remaining items deliberately deferred to "earn their place" through
concrete need.

- **D-OPEN-B — Cache provider at S5.** Decide when first S5 skill
  needs it. Candidates: Upstash Redis (serverless, zero-config),
  self-hosted Redis via Kamal accessory.
- **D-OPEN-D — Background worker model.** Decide when first app
  needs a worker. Kamal supports extra roles; question is whether
  to ship a default worker scaffold.
- **D-OPEN-J — Definitive-cheap backlog.** Tier 1–4 concept list +
  anti-features in
  [`notes/concepts-and-priorities.md`](notes/concepts-and-priorities.md#definitive-cheap-what-else-is-missing).
  Each tier-1 item (`awf-cost`, `awf-teardown` + idle detection,
  multi-env via Neon branches) will earn its own D-NNN when built.
