# 13 — Architecture Tracks: Choosing the Right Shape

> **Status: proposed.** This document reframes the linear S1–S5 ladder
> ([`07-multi-stage-architecture.md`](07-multi-stage-architecture.md))
> as **two tracks plus a set of states**, and names the pieces that are
> designed-but-unbuilt or entirely missing. It does not change any
> accepted decision; it proposes new ones. The reframe and the new
> primitives (serverless track, hibernate state, symmetric descale,
> Stripe, `awf-db-migrate`) should each earn a `D-0NN` entry in
> [`decisions.md`](decisions.md) before being built.

> **Who this is for.** Someone deciding *which* architecture a given
> app should use, *when* to move between them, and *how to descale or
> park* an app without losing it. It complements the per-stage deep
> dives (docs 10, 11) with the cross-cutting "which option, and why."

---

## 1. The problem this document solves

The accepted ladder is linear: S1 → S2 → S3 → S4 → S5. It is correct,
but it conflates two independent questions and skips one rung:

1. **Compute model** — static CDN, serverless functions, or a
   long-running container?
2. **Tenancy** — shared lab infrastructure, or dedicated production
   infrastructure?

The ladder also has a chasm. Doc 07 states the defining transition
plainly: *"The phase change is S2 → S3."* A project goes from **static
HTML on a CDN** straight to **operating a Linux box** (Docker + Kamal +
SSH + Postgres + Let's Encrypt). There is nothing in between — yet a
large class of real production apps (auth + database + Stripe checkout,
I/O-bound, no long-running work) belongs exactly in that gap.

This document adds the missing rung, separates the two axes, and makes
descaling and parking first-class.

---

## 2. The pivot: one codebase, three deploy targets

The whole reframe rests on a property of SvelteKit that the current
design underuses — the **adapter**. The same application source compiles
to three different deploy shapes by swapping one build adapter:

```
adapter-static     →  prerendered HTML            →  S1 / S2  (CF Pages)
adapter-cloudflare →  Pages Functions / Workers   →  TRACK A  (serverless)   ← new
adapter-node       →  long-running Node in Docker →  TRACK B  (server: S3–S5)
```

Because it is the *same code*, choosing an architecture is a build-time
decision, not a re-architecture — and **moving between tracks is a
redeploy**, which is what finally makes a real descale path possible
(§5).

---

## 3. The two tracks

```
                          ┌─ adapter-static ─────→ S1 Landing / S2 Demo
                          │                         CF Pages · €0 · static
   one SvelteKit app  ────┤
                          ├─ adapter-cloudflare ─→ TRACK A — Serverless Prod   ← NEW
                          │                         CF Pages Functions + Neon
                          │                         + Stripe + Fathom
                          │                         €0–19/mo · zero ops · auto-scale
                          │                         no LB · no SSH · no TLS mgmt
                          │
                          └─ adapter-node (Kamal) → TRACK B — Server
                                                     S3 shared lab → S4 dedicated+LB
                                                     → S5 multi-server
                                                     €4–41/mo · full control
                                                     workers · websockets · heavy SSR
```

### The one question that picks the track

> **Does this app need a long-running process?**
> websockets at scale · background workers / cron · heavy CPU SSR ·
> or you want to avoid per-request pricing.

- **No →** Track A (serverless). Cheaper, zero server ops, auto-scaling,
  and — critically — **no descale problem**, because there is nothing
  to descale.
- **Yes →** Track B (server). The existing S3 → S4 → S5 ladder.

When unsure, start on Track A. The cost of being wrong is a redeploy
with `adapter-node`, not a rebuild.

---

## 4. Use cases mapped to tracks and stages

The four operator use cases map directly onto the model. Two of them
carry concerns the accepted architecture does not yet model (Stripe,
app-level Fathom) — called out below.

| Operator use case | Track / stage | Built today | Notes |
|---|---|---|---|
| Cloudflare static pages (NS, CF Pages, Fathom) | Static — **S1/S2** | ✅ Full | Mature path; leave as-is |
| Shared Hetzner (multi-container, shared Neon, prototyping) | Server — **S3 MVP-play** | ✅ Composer + 8 atomics | Design's sweet spot; needs hardening (§7) |
| Basic prod (NS, LB, 1 server, Neon, **Stripe**, **Fathom**) | Server — **S4 Prescale**, *or* **Track A** | ❌ S4 0% built | For many apps, Track A is the better basic-prod |
| Scaling prod (multi-server, serious DB) | Server — **S5 Scale** | ❌ 0% built | Additive on S4; lowest concern |

**Two concerns absent from the current architecture:**

1. **Stripe / payments.** No `awf-stripe`, no payment template, no
   webhook handling, no Stripe secret flow exists anywhere. It is a
   cross-cutting concern (applies to Track A and all of Track B).
2. **App-level Fathom.** Fathom today is S1-only (marketing analytics).
   The running app (S3+) gets none. The Fathom site ID already lives in
   `passport.json`; nothing injects it into the container or the Worker.

---

## 5. Migration, descale, and the missing "hibernate" state

The accepted operational rule is *"stage promotion is forward-only;
demotion = teardown + re-promote"* (doc 07, rule 3). That is right in
one place and wrong in two.

| Transition | Current stance | Assessment | Proposed skill |
|---|---|---|---|
| S4 → S5 (scale out) | additive ✅ | Correct, well-designed | exists in spirit (`awf-kamal-deploy` + LB target) |
| **S5 → S4 (scale in)** | "teardown + re-promote" ❌ | **Wrong** — symmetric and data-free: drain server, drop from LB and `deploy.yml`, redeploy, destroy server | `awf-scale-in` (new) |
| S4 → S3 (de-dedicate) | teardown + re-promote | Honest — DB moves back to shared Neon, LB torn down. Label as rebuild, but automate the DB move | `awf-db-migrate` + `awf-stage-mvp-play` |
| **Track B → Track A** | not modeled | Newly possible: redeploy with `adapter-cloudflare`, point DNS at Pages, tear down server | composer (new) |

### Hibernate — a state, not a stage

The primitive the *experiments* use case needs most is entirely
missing. The current model is binary: an app is **running** (paying) or
**torn down** (gone, DB lost, DNS released). Real prototyping wants a
third option:

> Experiment paused / client demo over / might revive in 3 months —
> don't destroy it, don't keep paying for it.

```
HIBERNATE
  pg_dump → R2 (D-006)        keep all .awf/ state files
  stop app containers          park DNS record (note original target)
  suspend Neon compute         cost → ~€0

RESUME
  restore dump → Neon          redeploy (kamal or CF)
  unpark DNS                    back to the prior stage, unchanged
```

`awf-hibernate` / `awf-resume` is a small, high-value pair that serves
"prototyping, experiments" far better than the binary it replaces. It is
orthogonal to the stage — a Track A or any Track B app can hibernate.

---

## 6. The state machine, restated

```
                         NO_PROJECT
                              │ awf-create-project
                              ▼
                      ┌─── S1 Landing ───┐
                      │   (adapter-static)│
                      │        │          │
                      │     S2 Demo       │
                      └────────┬──────────┘
                               │ "does it need a long-running process?"
                  ┌────────────┴─────────────┐
            no    │                          │   yes
                  ▼                          ▼
       TRACK A — Serverless            TRACK B — Server
       (adapter-cloudflare)           (adapter-node, Kamal)
         CF Pages Fns + Neon            S3 shared lab
         + Stripe + Fathom                 │ promote
         auto-scales; terminal             ▼
                  │                      S4 prescale (LB from day one)
                  │                         │ scale out / in
                  │                         ▼
                  │                      S5 multi-server
                  │                          │
                  └──────────┬───────────────┘
                             ▼
                    HIBERNATE  ⇄  RESUME   (any stage, any track)
                             │
                             ▼
                         TEARDOWN
```

Promotion within a track is forward and human-approved (unchanged).
**Scale-in within Track B is symmetric.** **Track B → Track A** is a
redeploy. **Hibernate** is reachable from any live state and reversible.

---

## 7. Per-track build status and gaps

### Track A — Serverless (entirely new)

| Piece | Status | Skill |
|---|---|---|
| `adapter-cloudflare` template variant | ❌ | template work |
| CF Pages Functions deploy | partial (`awf-deploy` is static-only) | `awf-stage-serverless` composer |
| Neon serverless driver wiring | ❌ | `awf-app-add-db-client` (serverless mode) |
| Secrets → CF (not `.kamal/secrets`) | ❌ | `awf-app-secret-set` (CF target) |
| Stripe | ❌ | `awf-app-add-stripe` |

### Track B — Server

| Stage | Status | Gaps |
|---|---|---|
| **S3 shared lab** | ✅ built | Per-container resource limits (default — today one app OOMs the box, E-17); `awf-fleet` (what's running where); hibernate |
| **S4 prescale** | ❌ 0% built | `awf-db-migrate` + `awf-maintenance-mode` (the friction crux); `awf-hetzner-lb/network/firewall` (libs exist per doc 11 — need skill wrappers); `awf-cf-proxy-toggle`; `awf-cf-ssl-mode`; `awf-app-add-stripe`; `awf-stage-prescale` composer |
| **S5 scale** | ❌ 0% built | Mostly additive: `awf-hetzner-lb-add-target` + `deploy.yml` host entry; `awf-scale-in` for symmetry |

### Cross-cutting management (thin everywhere)

| Concern | Today | Proposed |
|---|---|---|
| Cost visibility | none | `awf-cost` (D-OPEN-J) — per-project € across CF/Hetzner/Neon |
| Idle reaping | none | detect 0-traffic-for-N-days → suggest hibernate |
| Fleet view | SSH + `docker stats` | `awf-fleet` |
| Uptime monitoring | LB health check only (S4) | Phase E — `/up` poller → notify |
| DB migration | manual `pg_dump` | `awf-db-migrate` |
| Inventory / audit | ✅ `awf-log inventory/where/history` (D-012) | already good — keep leaning on it |

---

## 8. Do we need other use cases? Verdict

After scenario analysis, the answer is **two additions, not two new
tiers**:

- **✅ ADD — Serverless prod (Track A).** The real missing rung. Best
  fit for most DB + Stripe SaaS apps that do not need a server. Highest
  impact.
- **✅ ADD — Hibernate (state).** Not a tier; a parked state. Directly
  serves experiments.
- **🟡 MAYBE — Background-worker / cron apps** (D-OPEN-D). Kamal supports
  extra roles. Add a worker scaffold *when the first app needs one*.
- **❌ REJECT — Internal-tool / no-DNS tier.** Already covered by
  `awf-preview` (temporary `workers.dev` URLs).
- **❌ REJECT — "Shared prod" tier** (paying apps on shared compute).
  Reintroduces the noisy-neighbor risk S4 exists to escape. Use Track A
  for cheap-prod instead.

---

## 9. Scenario walk-through (the reasoning behind the verdicts)

**S-1 — "SvelteKit SaaS with Stripe, <1000 users."**
Ladder says S3 lab → S4 (€33/mo, manage a server). Better: **Track A** —
CF Pages + Neon + Stripe, €0–19/mo, zero ops, auto-scale. Climb to S4
only on hitting Worker CPU limits or needing websockets/workers.
*Lesson: the ladder pushes users to manage Linux boxes prematurely.*

**S-2 — "10 idle prototypes, occasional client demos."**
**S3 shared lab is ideal** (€4.35/mo total). Gaps: no hibernate to park
a finished demo; no fleet view. *Lesson: S3 is excellent; it needs
hibernate + fleet.*

**S-3 — "Lab app 3 just signed a paying customer with an SLA."**
Promote S3 → S4. Today this is a ~16-step manual slog (doc 11) with
manual gates (DB migration, CF Origin Cert, orange-cloud toggle) at the
exact moment that matters. *Lesson: building S4 properly is the
highest-value work; `awf-db-migrate` is the crux.*

**S-4 — "Traffic spike, add a server, then it subsides."**
Scale-out S4 → S5 is well-designed (additive, LB from day one).
Scale-in S5 → S4 is genuinely easy and data-free but undocumented and
unbuilt. *Lesson: descale within Track B should be symmetric.*

**S-5 — "Experiment failed; might revive in 3 months."**
Want hibernate, not teardown. Today: keep paying, or lose the DB.
*Lesson: hibernate is the missing primitive.*

---

## 10. Recommended build order

Highest leverage first. Each item should get a `D-0NN` entry before
implementation, and a `plan_NNN` file per the repo's lead/dev workflow.

1. **`awf-db-migrate` + `awf-maintenance-mode`** — unblocks every
   tier-crossing migration. The friction crux of the whole ladder.
2. **Track A serverless** — `awf-stage-serverless` composer
   (adapter-cloudflare + Neon + CF secrets) + `awf-app-add-stripe`.
   Opens the missing rung.
3. **`awf-hibernate` / `awf-resume`** — cheap, high-value, serves
   experiments.
4. **S4 atomics → `awf-stage-prescale`** — `awf-hetzner-lb/network/
   firewall`, `awf-cf-proxy-toggle`, `awf-cf-ssl-mode`, then the
   composer.
5. **S3 hardening** — per-container resource limits (default) +
   `awf-fleet`.
6. **`awf-scale-in`** (symmetric descale) + **`awf-cost`**.
7. **S5 composer** — last; mostly additive.

---

## 11. What this changes vs. what it preserves

**Preserves (no accepted decision is reversed):**
- The S1–S5 stage names and the two-layer skill model (D-001).
- Kamal as the Track B deploy abstraction (D-001).
- `.awf/` state schemas, GHCR, R2, logging/inventory (D-003, D-005,
  D-006, D-002, D-012).

**Proposes (each needs its own ADR):**
- A **second track** (serverless) as a first-class peer to the server
  ladder, with the SvelteKit adapter as the pivot.
- **Hibernate** as a reversible state orthogonal to stage.
- **Symmetric scale-in** within Track B (relaxing "forward-only" only
  where no data moves).
- **Stripe** and **app-level Fathom** as modeled, supported concerns.
- **`awf-db-migrate`** to automate the one remaining scary manual gate.

---

## 12. References

- [`07-multi-stage-architecture.md`](07-multi-stage-architecture.md) — the accepted S1–S5 ladder
- [`10-s3-architecture-guide.md`](10-s3-architecture-guide.md) — shared lab deep dive
- [`11-s4-architecture-guide.md`](11-s4-architecture-guide.md) — prescale deep dive
- [`12-llm-workflow-guide.md`](12-llm-workflow-guide.md) — routing / state machine for the LLM
- [`decisions.md`](decisions.md) — ADR log (D-001 … D-012; open: D-OPEN-B/D/J/K)
