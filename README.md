# awf-skills

A portable suite of Claude Code skills that scaffold, configure, deploy,
and register a small Svelte website end-to-end — from a fresh domain
through to Bing IndexNow — usable from any directory on disk.

> **Status (Phases A + B + C complete).** S1 (landing page on
> Cloudflare Pages) and S3 (MVP-play on shared Hetzner + Neon via
> Kamal) are pipeline-validated. The full promotion path from empty
> repo to live S3 site is one command: `/awf-stage-mvp-play`. The
> affordance layer — structured event log, "where am I" status,
> context-aware help, and scoped doctor — is in place for both humans
> and the LLM. **434 tests + 3 skipped (by design); all green.** The
> remaining S4/S5 stages (prescale, scale) reuse the same atomic-skill
> + composer model — implementation is incremental per
> [`docs/spec.md`](docs/spec.md).

---

## Quick start

```bash
# 1. Clone (recommended path is ~/.claude/awf-skills/)
git clone https://github.com/emson/awf-skills.git ~/.claude/awf-skills
cd ~/.claude/awf-skills

# 2. Install (symlinks each skills/awf-* into ~/.claude/skills/)
./install.sh

# 3. Onboard (in a Claude Code session, in any directory)
#    Creates ~/.config/awf/.env, prompts for missing credentials,
#    offers to export AWF_HOME in your shell rc. Idempotent.
> /awf-init

# 4. Verify the runtime
> /awf-doctor
```

---

## What this is

- **A standalone skills repo.** Replaces the workflows previously
  living inside `agent_web_factory`. No dependency on that repo.
- **Project-agnostic.** Skills run from any cwd. The "project" is
  whatever directory contains `passport.json` (walking up).
- **Composable.** Each skill is one verb. The `awf-launch`
  orchestrator sequences them with explicit checkpoints at the
  irreducibly-manual steps.
- **Promotable.** The same project moves up a five-stage ladder
  (landing → demo → MVP-play → prescale → scale) via composer
  skills, without re-architecting. See
  [`docs/07-multi-stage-architecture.md`](docs/07-multi-stage-architecture.md).

## What this is not

- Not a multi-host adapter framework. The S1 stack is hardcoded
  (Cloudflare + Namecheap + Fathom + GSC + Bing); S3–S5 layer on
  Hetzner + Neon + Kamal.
- Not a domain registrar. Buying domains is out of scope.
- Not a credentials manager beyond layered `.env` files.
- Not yet a Claude Code plugin. Plugin packaging is a future polish.

---

## Layout

```
.
├── docs/        ← read 00-plan.md first; spec.md + decisions.md for the build-out
├── lib/         ← shared Python: passport, state (.awf/ schemas), project locator, log, etc.
├── skills/      ← one dir per skill; SKILL.md + optional uv-script
├── templates/   ← versioned Svelte site templates (added separately)
├── tests/       ← pytest suite (75 tests across lib/ and skills/)
└── .claude/agents/  ← Lead / Dev / Reviewer subagent definitions
```

See [`docs/02-architecture.md`](docs/02-architecture.md) for the
full layout, install model, and runtime resolution rules.

---

## Required runtime

`bash`, `git`, `node`/`npm`, `uv`, `wrangler`, plus credentials. `gh`
is optional. Companion scripts use [`uv` inline-script
metadata (PEP 723)](https://docs.astral.sh/uv/guides/scripts/), so
there is no Python virtualenv to manage.

`awf-doctor` validates the whole list.

---

## Documentation

In recommended reading order:

1. [`docs/00-plan.md`](docs/00-plan.md) — the comprehensive plan: what
   we're building, the pipeline, the credentials surface, why the
   suite exists, open trade-offs.
2. [`docs/01-principles.md`](docs/01-principles.md) — the 17 axioms.
3. [`docs/02-architecture.md`](docs/02-architecture.md) — repo
   layout, install model, runtime resolution.
4. [`docs/03-passport-contract.md`](docs/03-passport-contract.md) —
   the `passport.json` schema (v1.0).
5. [`docs/04-skill-authoring.md`](docs/04-skill-authoring.md) — how
   to add or modify a skill.
6. [`docs/05-credentials.md`](docs/05-credentials.md) — every
   credential, where it comes from, where it's looked up.
7. [`docs/06-experimentation-guide.md`](docs/06-experimentation-guide.md) —
   hands-on walkthrough across four tiers (dry → local build →
   API-side → full launch).
8. [`docs/07-multi-stage-architecture.md`](docs/07-multi-stage-architecture.md) —
   the S1–S5 stage ladder, two-layer skill model, project anchor
   split. Locked-in pattern; implementation is incremental.
9. [`docs/08-logging.md`](docs/08-logging.md) — per-project event
   log, redaction policy, `awf-log` skill.
10. [`docs/spec.md`](docs/spec.md) — build-ready module spec for
    Phases A–E (foundation → S3 → affordances → cheap-essentials).
11. [`docs/decisions.md`](docs/decisions.md) — append-only ADR log
    (D-001 … D-009).

---

## Skill catalogue

| Skill | Purpose | Status |
|---|---|---|
| `awf-init` | First-run onboarding: write `~/.config/awf/.env`, prompt for missing creds, set shell rc | ✅ functional |
| `awf-doctor` | Pre-flight: CLIs, credentials, OAuth, git hygiene | ✅ functional |
| `awf-status` | Live state from CF + Fathom + GSC | ✅ functional |
| `awf-create-project` | Scaffold from current template | ✅ functional |
| `awf-setup-domain` | Cloudflare zone + Pages + DNS + redirects | ✅ functional |
| `awf-setup-nameservers` | Namecheap NS swap | ✅ functional |
| `awf-setup-analytics` | Fathom site create | ✅ functional |
| `awf-generate-content` | SERP screenshot → site copy + FAQs (Claude-native) | ✓ body-only by design |
| `awf-review-passport` | Lint passport against template expectations | ✅ functional |
| `awf-install` | `npm install` | ✅ functional |
| `awf-deploy` | Build + `wrangler pages deploy` | ✅ functional |
| `awf-setup-gsc` | Add GSC property + TXT record | ✅ functional |
| `awf-verify-gsc` | Verify property + submit sitemap | ✅ functional |
| `awf-submit-bing` | Generate IndexNow key + push URLs | ✅ functional |
| `awf-update-template` | Re-overlay newer template version | ✅ functional |
| `awf-launch` | Orchestrator | ✓ body-only by design (composes the above) |
| `awf-migrate` | Idempotent legacy → `.awf/`-anchor migration (Phase A) | ✅ functional |

### Multi-stage foundation (Phase A complete)

| Module | Purpose | Notes |
|---|---|---|
| `lib/state.py` | `.awf/` schemas: `ProjectAnchor`, `Infra`, `Shared` (Pydantic v2) | atomic-write, forward-compat, log-hook |
| `lib/project.py` | Dual-walk locator: prefers `.awf/project.json`, falls back to `passport.json` | `find_anchor_state()`, `ensure_anchor()` |
| `lib/log.py` | Structured event log (`session`/`invoke`/`api`/`state_change`/`gate`/`error`/`intent`/`note`/`process`/`file_write`) | ContextVar-threaded, ULID-IDed, redaction-by-denylist, best-effort writes |
| `skills/awf-migrate` | One-shot upgrade from legacy passport-only projects | Wraps `ensure_anchor()`; session-aware |

### S3 stack (Phase B complete — proof of architecture)

| Module / Skill | Purpose | Notes |
|---|---|---|
| `lib/hetzner/` | Idempotent Hetzner Cloud client (servers, firewalls, LB, SSH keys, networks) | Single `_call` chokepoint; bearer-token redaction; hcloud SDK transport |
| `lib/neon/` | Idempotent Neon REST client (projects, branches, connection strings) | httpx transport; `?sslmode=require` enforcement; `napi_*` token redaction |
| `lib/kamal/` | Kamal YAML renderer + subprocess wrapper | Pure `render()`; DNS-before-TLS gate; golden fixture; `KamalDnsTimeout` |
| `awf-shared-infra-get` | Mint/reuse the user-scope play Hetzner server + play Neon project | Writes `~/.config/awf/shared.json` |
| `awf-hetzner-server` | Mint one project-scope Hetzner VM | Writes `.awf/infra.json` |
| `awf-neon-project` | Mint one Neon project | Writes `.awf/infra.json` |
| `awf-neon-branch` | Mint one Neon branch on a project | Writes `.awf/infra.json` |
| `awf-cf-dns-record` | Create/update one Cloudflare DNS record | Writes `passport.cloudflare` |
| `awf-app-dockerize` | Scaffold `Dockerfile`, `/up` healthcheck, `lib/db.ts` | Versioned constant; drift-aware (never clobbers user edits) |
| `awf-app-secret-set` | Upsert one `KEY=VAL` in `.kamal/secrets` | Mutually-exclusive `--value` / `--from-env` / `--from-file` |
| `awf-kamal-config` | Render `config/deploy.yml` from anchor + infra | Pure; idempotent |
| `awf-kamal-setup` | First-time `kamal setup` (DNS-gated) | Emits structured `gate=dns_propagation` JSON on timeout |
| `awf-kamal-deploy` | Rolling `kamal deploy` | Updates `Infra.kamal.last_deploy_image` |
| `awf-stage-mvp-play` | **Composer** — promote project to `stage=mvp-play` | Subprocess-chains the 10 atomic skills; secret-redaction in logs; idempotent re-runs |

### Affordances (Phase C complete)

| Skill / Module | Purpose | Notes |
|---|---|---|
| `awf-log` | CLI surface for the event log | `tail`, `session`, `find`, `replay`, `note`, `sessions`, `diff` (stub) — JSONL + `--json` |
| `awf-status` (rebuilt) | Canonical "where am I" | Fixed order: Project / Stage / Drift / Recent / Next; drift v1 (CF/Hetzner/Neon); `--json` schema-validated; idle detection |
| `awf-help` (rebuilt) | Context-aware orientation | 3 modes: fresh-start / in-project / `--overview`; never mutates state |
| `awf-doctor` (extended) | Scoped pre-flight | `--for-stage <name>` / `--for-skill <name>`; recent-error surfacing from log |
| `lib/stages.py` | Single source of truth for stage→{composer, hint, relevant skills, subsystem requirements} | Consumed by `awf-status`, `awf-help`, `awf-doctor` |

### Multi-agent build workflow

Active builds use a three-role swarm — Lead (opus, planning), Dev
(sonnet, implementation), Reviewer (sonnet, audit) — defined in
[`docs/multi_agent_prompt.md`](docs/multi_agent_prompt.md). All work
flows through `docs/plans/plan_NNN_<slug>.md`. See
[`docs/decisions.md`](docs/decisions.md) for the ADR log.
