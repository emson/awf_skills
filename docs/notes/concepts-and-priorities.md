# Concepts & Priorities — Brainstorm

Captured during the design pass on 2026-05-31, after locking in D-001
(multi-stage pattern) and D-002 (logging). Not yet decisions — this
is the thinking space for "what else makes awf-skills the definitive
approach to deploying websites cheaply." Promote items here to
`decisions.md` (D-NNN) as they are committed to.

---

## Where are we — the "stage detection" affordance

Answerable in one read by humans, LLMs, and skills.

**Single source of truth:** `.awf/project.json.stage`. One string, one
of the five canonical stage values (landing, demo, mvp-play, prescale,
scale). Anything else — passport, kamal config, infra.json — is a
*consequence* of being at that stage, not evidence of it.

**Three layers of answer:**

| Question | Answer | Surfaced by |
|---|---|---|
| What stage is this project at? | `.awf/project.json.stage` | `awf-status` first line |
| What was just done? | last 5 log events | `awf-status` event tail |
| What can I do next? | composers filtered to current stage + 1 | `awf-status` "next" block; `awf-help` |

**`awf-status` becomes the canonical "where am I" command.** Today it
reports CF/Fathom/GSC state. Extend it to print, in order:

```
Project: invoicetracker (invoicetracker.io)
Stage:   landing                              ← from .awf/project.json
Drift:   none                                 ← world vs passport/infra diff
Recent:  [last 5 log events]                  ← from .awf/log.jsonl
Next:    awf-stage-demo  awf-stage-mvp-play   ← composers that take you forward
```

The LLM is instructed: when you don't know where the user is, run
`awf-status` first.

**Edge cases**

- *No `.awf/` directory.* `awf-status` returns `Stage: none, suggest
  /awf-help to scaffold`. Doesn't error.
- *Drift.* Passport says zone exists; CF says it doesn't.
  `awf-status` reports the divergence and names the atomic skill that
  would re-converge. State file lying to you is worse than no state
  file.
- *Multiple stages partially present.* (e.g. landing passport intact,
  infra.json half-built.) `stage` field is the authority; drift block
  reports the unexpected files.

→ Candidate D-003 once `awf-status` is rebuilt.

---

## awf-help — reshape as the entry point

Today's `awf-help` is closer to a `--overview` mode. Add context
awareness as the default.

**Behaviour, in order:**

1. Walk up to find `.awf/project.json`.
2. If found, read stage. If not, "no project" mode.
3. Output is filtered to what's relevant *right now*.

**Three output modes:**

- **No project found:** "You're not in an awf project. To start one:
  `/awf-create-project`. To learn the system: `/awf-help --overview`."
- **In a project at stage X:** "You're at stage X. Composer to
  advance: `/awf-stage-<next>`. Atomic skills relevant here: …
  Common operations: status, log, deploy, teardown."
- **`--overview`:** the full stage ladder, links to docs 07 + 08,
  every skill grouped by stage.

→ Candidate D-004.

---

## awf-doctor — pre-flight, scoped

Today checks all credentials. Three improvements make it cheap enough
to invoke as routine pre-flight:

1. **`--for-stage X`** — check only what stage X needs. Promoting to
   MVP-play needs Hetzner + Neon + registry; checking Bing on the way
   is noise.
2. **`--for-skill awf-X`** — even narrower. Useful before retrying a
   failed atomic skill.
3. **Read recent log errors.** If the log shows a credential-shaped
   error in the last session, doctor calls it out specifically rather
   than re-running every check.

These together turn doctor into "the LLM's pre-action habit," not "a
big check."

→ Candidate D-005.

---

## Definitive-cheap: what else is missing

Filtered by the "if absent, cheap silently becomes expensive" lens.

### Tier 1 — direct cost lifecycle (must address)

- **`awf-cost`.** Reads passport/infra across all projects via the
  sessions index; reports €/mo per project. Promotion skills call
  it: *"promoting to prescale will cost ~€10/mo more, confirm?"*
- **`awf-teardown` + idle detection.** Abandoned projects keep
  billing. Teardown is named in 07. Add: `awf-status` flags *"no
  deploys in 90 days, Fathom shows no traffic — consider teardown."*
- **Multi-environment via Neon branches.** Neon branches are free
  within plan limits. `awf-env-create staging` makes a branch + a
  kamal deploy target. Avoids the "we need a $20/mo staging server"
  trap.

### Tier 2 — every site needs eventually, often skipped

- **Backup audit.** Not a backup *tool*; a backup *check*.
  `awf-backup-check` reports DB recoverability (Neon PITR window
  etc).
- **Email setup.** Resend free tier or SES. `awf-setup-email` adds
  DNS (SPF/DKIM/MX), wires the API key into Kamal secrets, scaffolds
  `lib/mail.ts`. Every signup form needs it.
- **Uptime monitoring.** UptimeRobot free tier. `awf-setup-uptime`
  registers the URL, stores the monitor ID.
- **Error tracking.** Sentry free tier. `awf-setup-sentry` creates
  the project, wires DSN, drops SDK into SvelteKit hooks.

### Tier 3 — affordance (long-term maintainability)

- **Skill versioning.** `.awf/project.json` carries `awf_version`.
  `awf-migrate` walks projects across breaking changes. Rails-style.
- **Project recovery.** `awf-recover` reads CF + Hetzner + Neon and
  reconstructs `.awf/` from world state. Possible because skills use
  search-or-create — every resource has discoverable identity.
- **Cross-project ops.** From the sessions index: `awf-fleet status`
  (all projects, one table), `awf-fleet cost` (sum), `awf-fleet
  drift`. Matters once you have >3 sites.
- **Stage-specific templates.** Current `landing-page-v1` covers S1.
  For S3, a `saas-mvp-v1` template with Dockerfile, `/up`, `lib/db.ts`,
  basic auth scaffold pre-done. Makes the S2→S3 code gap 80% closed
  by template.

### Tier 4 — nice but defer

- Auth scaffolding (Lucia / Better Auth) inside templates.
- Payments — Stripe credential wiring + success/cancel URL flags in
  passport.
- Performance audits — Lighthouse CI on deploy, results in the log.
- Compliance scaffolding — privacy/terms templates.
- Multi-account profiles — `AWF_PROFILE=work` switches credential
  roots.

### Anti-features (explicitly NOT building)

- **Custom dashboard / web UI.** CLI + `awf-status` is the interface.
- **Project-internal queue/worker abstraction.** App-level, not
  skills-level.
- **Vendor-escape hatches.** Designing for "what if we leave
  Cloudflare" makes everything 2× more complex. Pick the stack;
  commit; document migration only if it ever actually comes up.

---

## Recommended priority order

If building incrementally:

1. **First S3 composer (`awf-stage-mvp-play`).** Proves the model
   end-to-end. Forces `lib/log.py`, `lib/hetzner.py`, and
   `.awf/project.json` to exist.
2. **`awf-status` extension.** Stage + drift + log tail + next.
   Single highest-value affordance.
3. **`awf-help` context-aware mode.** Entry point for new sessions.
4. **`awf-doctor --for-stage`.** Preflight before promotion.
5. **`awf-cost`.** Cheap to build, makes "cheap" measurable.
6. **`awf-teardown`.** Closes the lifecycle, plugs the money leak.
7. **`awf-setup-email`.** First "real site needs this" addition.
8. Everything else (uptime, sentry, recovery, fleet, templates) when
   concrete demand appears.

---

## How this graduates

When an item here is committed to, write a D-NNN entry in
`decisions.md`, link back to this note for the rejected-alternatives
context, and remove the candidate marker.
