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

## Open decisions

These were intentionally deferred when D-001 was made. Each will
become its own D-NNN entry when it is resolved.

- **D-OPEN-A — Image registry default.** Candidates: GHCR (free,
  `gh` integrates), Docker Hub (universal), self-hosted. Decide
  before building `awf-kamal-config`.
- **D-OPEN-B — Cache provider at S5.** Candidates: Upstash Redis
  (serverless, zero-config), self-hosted Redis via Kamal accessory.
- **D-OPEN-C — Object storage default.** Strong default is
  Cloudflare R2 (no egress fees, S3-compatible).
- **D-OPEN-D — Background worker model.** Kamal supports extra
  roles; question is whether to ship a default worker scaffold.
- **D-OPEN-E — Concrete schema for `.awf/project.json` and
  `.awf/infra.json`** beyond the sketches in
  [`07-multi-stage-architecture.md`](07-multi-stage-architecture.md).
- **D-OPEN-F — `lib/project.py` migration.** When to swap the
  walk-up target from `passport.json` to `.awf/project.json`, and
  what shim landing-page skills need during the transition.
