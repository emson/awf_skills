# awf-skills

A portable suite of Claude Code skills that scaffold, configure, deploy,
and register a small Svelte website end-to-end — from a fresh domain
through to Bing IndexNow — usable from any directory on disk.

> Status: scaffold + spine. `awf-doctor` is functional; the pipeline
> skills are stubs (SKILL.md only) being filled in per the build order
> in [`docs/00-plan.md`](docs/00-plan.md).

---

## Quick start

```bash
# 1. Clone (recommended path is ~/.claude/awf-skills/)
git clone <this-repo> ~/.claude/awf-skills
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

## What this is not

- Not a multi-host adapter framework. The stack is hardcoded:
  Cloudflare + Namecheap + Fathom + GSC + Bing.
- Not a domain registrar. Buying domains is out of scope.
- Not a credentials manager beyond layered `.env` files.
- Not yet a Claude Code plugin. Plugin packaging is a future polish.

---

## Layout

```
.
├── docs/        ← read 00-plan.md first
├── lib/         ← shared Python (passport schema, slug, layered config, project locator)
├── skills/      ← one dir per skill; SKILL.md + optional uv-script
├── templates/   ← versioned Svelte site templates (added separately)
└── tests/
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
