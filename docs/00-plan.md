# 00 — Plan

Status: living document. This is the canonical "why this repo exists, what it
does, and what it deliberately does not do." Subsequent docs (`01-principles`,
`02-architecture`, etc.) are derived from this one.

---

## 1. What we're building

A standalone, portable suite of Claude Code skills that, used together inside
any directory on disk, can scaffold, configure, deploy, and register a small
Svelte website end-to-end — domain through Bing IndexNow — with the minimum
human intervention compatible with how the underlying services actually work.

The skills replace a Python monorepo (`agent_web_factory`) whose workflows
encoded the same pipeline but required cloning the monorepo, sourcing its
`.env`, and running `uv run python -m agent_factory.workflows.*` from its
root. The new suite carries the tested API logic forward, but installs once,
runs from any cwd, and depends on nothing in the original repo.

Three things change as a consequence of going standalone:

1. **No `agent_factory` import.** The Cloudflare / Namecheap / Fathom / GSC /
   IndexNow logic ships *with* the skills. The original `tools/` modules are
   ported, not referenced.
2. **No fixed cwd.** Credential and project resolution become layered
   lookups. `Path(__file__).parent.parent.parent / .env` magic is gone.
3. **The website project replaces the monorepo as the unit of work.** Each
   launched site is its own dir, its own git repo, its own `passport.json`.
   Skills operate on "the project I am currently inside", not on a
   `projects/<slug>/` subdir of a factory.

---

## 2. The pipeline (what one launch actually does)

Annotated against the legacy `Steps to Launch Website.md` and the underlying
code in `agent_web_factory/agent_factory/`. This is the source of truth for
what the skills must collectively cover.

| # | Phase | Legacy code | Inputs | Side effects | Manual? |
|---|---|---|---|---|---|
| 0 | Pick a domain | — | — | — | **Yes** (human curation) |
| 1 | Git branch | shell | branch name | new local branch | trivial |
| 2 | Scaffold project | `workflows/project_creator_workflow.py` → `template_overwrite_workflow.overwrite_project_website` | `domain` | `projects/<slug>/` populated from `templates/landing_page`; stub `passport.json`; vault notes dir | No |
| 3 | Cloudflare zone + Pages | `workflows/domain_setup_workflow.py` | `domain` | CF zone, Pages project, apex CNAME → `<slug>.pages.dev`, www → apex DNS, always_use_https on, www → apex bulk redirect | No |
| 4 | Namecheap NS swap | `tools/namecheap/namecheap_dns_setter.py` | `domain`, comma-list of CF nameservers | Registrar nameservers updated | **Semi**: user must read NS off CF and pass them in |
| 5 | Fathom analytics | `workflows/analytics_setup_workflow.py` | `domain` | Fathom site created; `fathom_site_id` patched into passport | No |
| 6 | Content generation | `workflows/project_agent_workflow.py` → `agents/coo/project_agent.py` (`ell` + OpenAI gpt-4.1/4o-mini) | SERP screenshot JPEG, project name, keywords, title | Vault notes (`{title} Analysis.md`, `{title} Questions.md`); passport patched with `site_name`, `site_hero`, `site_subtitle`, `site_description`, `category`, `tags`, `faqs[]` | **Yes**: user must take Google SERP screenshot first |
| 7 | Review / edit passport | manual | — | hand-tuned copy | **Yes** |
| 8 | npm install | `workflows/project_install_workflow.py` (`subprocess npm install`) | project path | `node_modules` populated | No |
| 9 | Build + deploy | `workflows/project_deploy_workflow.py` → `npm run build` then `npx wrangler pages deploy` | project path | Site live on Cloudflare Pages | No (wrangler must be authed) |
| 10 | GSC: add + TXT verify DNS | `workflows/gsc_setup_workflow.py` (uses `tools/google_search_console/gsc_client.py` + Cloudflare DNS) | `domain` | `sc-domain:` property added; TXT record on CF | No |
| 11 | GSC: verify + submit sitemap | `workflows/gsc_verify_workflow.py` | `domain` | Verification claimed; `sitemap.xml` submitted | No |
| 12 | Bing Webmaster registration | — | — | Property created via "Import from GSC"; sitemap submitted | **Yes** (browser, MS-OAuth-only) |
| 13 | Bing IndexNow | `tools/bing/indexnow_submit.py` | `domain`, IndexNow key | URLs from sitemap pushed to IndexNow | No |
| 14 | Git merge + push | shell | — | branch merged | trivial |

Two structural details that drove several axioms:

- `domain_to_project_name("devroast.com") → "devroast-com"` (in
  `tools/cloudflare/pages.py`). The legacy doc occasionally passes the dotted
  form, occasionally the hyphenated. Skills must derive the slug
  deterministically and never accept it as a separate argument.
- All legacy workflows assume **cwd = monorepo root** (`./projects`,
  `./awf_vault`, `./templates/landing_page` are hard-coded), and `Config`
  loads `.env` at a `parent.parent.parent` path of `utils/config.py`. Running
  from anywhere else crashes at import. The portable suite cannot inherit
  this.

---

## 3. Credentials surface

From `agent_factory/utils/config.py` plus on-disk artifacts:

| Surface | Vars / files | Used by |
|---|---|---|
| Cloudflare API | `CLOUDFLARE_EMAIL`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_ACCOUNT_ID` | zone, DNS, Pages, bulk redirects, GSC TXT record |
| Cloudflare Pages deploy | `wrangler` CLI auth (separate, browser-OAuth on first run) | deploy |
| Namecheap | `NAMECHEAP_API_USER`, `NAMECHEAP_API_KEY`, `NAMECHEAP_USERNAME`, `NAMECHEAP_CLIENT_IP` | NS swap |
| Fathom | `FATHOM_API_KEY` | analytics site create |
| Google (GSC + Site Verification) | `GOOGLE_APPLICATION_CREDENTIALS` (OAuth desktop client JSON) + `token.json` (cached refresh token) | GSC site add/verify, sitemap |
| LLM | `OPENAI_API_KEY` (legacy content gen via `ell`) — **dropped from launch path**; content is Claude-native |
| Bing | IndexNow key — **moved into passport per-domain**, see §6 |

The single pre-flight skill (`awf-doctor`) is the source of truth for this
list. Adding a new credential = updating `awf-doctor` + `.env.example`. Other
skills never re-implement these checks.

---

## 4. The big simplification lever

Step 6 (content generation) is the only place the legacy workflow leans on
`ell` + OpenAI. The job: given a Google SERP screenshot + keywords, write
`site_name` / `hero` / `subtitle` / `description` / `category` / `tags` and
8–10 FAQs into `passport.json`, and dump the analysis as a vault note. That
is exactly what Claude Code does natively — multimodal image read +
structured edit + file write, no SDK call.

Replacing this in skill form removes:

- `OPENAI_API_KEY` from the launch path
- the `ell` cache directory
- response-format gymnastics (Pydantic models defined for OpenAI structured
  output)
- per-launch OpenAI token spend

It also makes the copy interactively reviewable inside the same Claude
session before deploy. **This is the highest-leverage change** when porting.
The Pydantic schemas (`SiteModel`, `FaqModel`) survive as the contract
documented in the skill — Claude is told "produce this shape", and writes
straight to `passport.json`.

---

## 5. Skill catalogue

Two-tier: one orchestrator, atomic skills underneath, plus health and state
utilities. Atomic skills are mostly thin wrappers — they teach Claude the
right invocation, prerequisites, and failure shapes. The content skill is
Claude-native; doctor and status are net-new logic.

### Orchestrator

- **`awf-launch`** — interactive end-to-end pipeline. Inputs: `domain`,
  `keywords`, optional `title`. Walks the table in §2 with explicit
  checkpoints at the irreducibly manual steps (domain pick, SERP screenshot,
  passport review, Bing webmaster import). Idempotent: at each step queries
  current state and skips/repairs rather than re-creating.

### Atomic skills (one verb each)

0. `awf-init` — first-run onboarding. Creates `~/.config/awf/.env`,
   prompts for missing credentials, offers to add `export AWF_HOME` to
   the shell rc, warns if `install.sh` hasn't run. Idempotent.
1. `awf-doctor` — runs the credential / tool / OAuth / git / cwd preflight.
   Pure read; safe to run anywhere.
2. `awf-status` — given a domain, queries Cloudflare, Fathom, GSC for
   current truth and prints a checklist of done/missing. The linchpin for
   resumability.
3. `awf-create-project` — scaffolds project dir from the current template
   version, writes stub `passport.json`, optionally inits a git repo.
4. `awf-setup-domain` — Cloudflare zone + Pages + DNS + always_use_https +
   bulk redirect. Reads NS back for the next step.
5. `awf-setup-nameservers` — Namecheap NS swap, consuming NS list from
   `awf-setup-domain` rather than asking the user.
6. `awf-setup-analytics` — Fathom site create; patches passport.
7. `awf-generate-content` — **Claude-native, not a Python wrapper.** Reads
   the SERP screenshot directly, writes vault notes, patches passport
   (`site_name`, `site_hero`, etc., plus FAQs). Shows a diff for human
   review.
8. `awf-review-passport` — opens passport, lints required fields against
   template expectations, flags empty `features`/`tags`.
9. `awf-install` — `npm install`.
10. `awf-deploy` — `npm run build` + `npx wrangler pages deploy`. Verifies
    `wrangler whoami` first. Warns on dirty git tree.
11. `awf-setup-gsc` — GSC add property + Cloudflare TXT verification record.
    Handles OAuth re-auth path explicitly (re-runs `gsc_client` if
    `token.json` missing/expired).
12. `awf-verify-gsc` — verify property + submit sitemap.
13. `awf-submit-bing` — IndexNow URL submission. Documents the irreducible
    Bing Webmaster import-from-GSC browser step and waits for confirmation
    before submitting.
14. `awf-update-template` — re-overlay a newer template version onto an
    existing project, preserving content via the template's preserve-list.

### What is *not* a skill

- **Domain purchase.** Not in the legacy workflow; not in scope.
- **Git operations beyond `init` and `status`/`diff`.** Skills suggest
  branch/commit/merge commands; they never execute destructive ones.
- **Bing Webmaster initial registration.** Browser-OAuth-only; named as a
  manual gate, not automated.

---

## 6. Axioms

These are the design principles the suite is built on. The full list lives
in [`01-principles.md`](01-principles.md) — summarised here.

The headline:

- **A1.** The project is the cwd; the contract is `passport.json`.
- **A2.** Skills are portable by default; only `bash`, `git`, `node`/`npm`,
  `uv`, `wrangler`, `gh` plus credentials are required.
- **A3.** Companion code is Python-via-uv-script (PEP 723). No venv, no
  install step.
- **A4.** One verb per atomic skill; one orchestrator above.
- **A5.** Idempotent or refused: every mutating skill checks state via the
  upstream API and either no-ops, repairs, or errors with `--force`.
- **A6.** Credentials are layered and discoverable, never magic. Order:
  process env → `./.env` → `$AWF_HOME/.env` → `~/.config/awf/.env`.
- **A7.** State lives where the API stores it. The only local state is
  `passport.json`, which is config not cache.
- **A8.** Manual steps are first-class; they are gates, not bugs.
- **A9.** Doctor before doing. Pre-flight is its own skill.
- **A10.** The template is a separately-versioned artifact. Scaffolding
  pins a version; an update skill re-overlays.
- **A11.** The passport has a schema version; skills refuse schemas they
  don't understand and migrate explicitly.
- **A12.** Derive the slug, never accept it. `domain` is the only input.
- **A13.** Surface upstream errors unchanged.
- **A14.** Skills compose via file artifacts (`passport.json`), not bash
  chaining. Run any skill out of order while recovering from a partial
  launch.
- **A15.** No destructive git from skills.
- **A16.** Hardcode the stack (Cloudflare + Namecheap + Fathom + GSC +
  Bing). Adapter pattern is over-engineering before a second user.
- **A17.** Content generation is Claude-native, not an SDK call.

---

## 7. Architecture

See [`02-architecture.md`](02-architecture.md) for the full layout. Summary:

```
awf_skills/
├── README.md
├── PRINCIPLES.md → docs/01-principles.md
├── .env.example
├── install.sh
├── docs/
├── lib/                 ← shared Python: passport, slug, config, project, awf_home
├── skills/              ← one dir per skill, each with SKILL.md (+ optional scripts/)
├── templates/           ← versioned template directories (or submodule pointer)
└── tests/
```

**Install model.** Clone repo to `~/.claude/awf-skills/` (or anywhere). Run
`./install.sh`, which symlinks each `skills/awf-*/` into `~/.claude/skills/`
and exports `AWF_HOME`. Skills become invocable in any cwd.

**Companion script convention.** Each script that needs API logic is a
single file with PEP 723 inline metadata:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cloudflare>=3", "python-dotenv"]
# ///
```

`uv` resolves and caches deps on first run. No venv, no install. Scripts
locate `lib/` via `$AWF_HOME` (or by realpath-walking up from `__file__`).

**Project locator.** Walk up from cwd looking for `passport.json`. Absent →
error with "run `awf-create-project` first or `cd` to an existing project".

**Skills repo locator.** `AWF_HOME` env var → `~/.claude/awf-skills/`
default → realpath of the running script.

---

## 8. Open trade-offs

These have two reasonable answers; flagged for explicit decisions.

1. **Template repo: same repo as skills, or separate?**
   Same = atomic versioning, easier dev. Separate = template can be consumed
   without skills; iteration doesn't churn skill commits. **Default: same
   repo, tagged releases.** Split later if needed.

2. **Does `awf-create-project` `git init` automatically?**
   Yes makes cwd-as-project clean and deploy/branching scriptable.
   **Default: yes, with a `--no-git` opt-out.**

3. **OpenAI fallback for content gen, or Claude-only?**
   Pure Claude-native is simpler and removes a credential. Keeping OpenAI
   lets `awf-generate-content` run in a future cron. **Default: Claude-only.**

4. **IndexNow key: per-domain or shared?**
   Per-domain matches Fathom-ID-in-passport pattern and survives
   revocation. **Default: per-domain, generated on Bing setup, stored in
   `passport.json` as `indexnow_key`.**

5. **`awf-launch` autonomy.**
   Prompt at every gate, or only at irreducible ones (SERP screenshot,
   passport review, Bing import)? **Default: only irreducible, with
   `--interactive` to slow down.**

6. **Distribution: plain git clone or Claude Code plugin manifest?**
   **Default: plain git clone + `install.sh`.** Plugin packaging is a
   future polish, not a blocker.

---

## 9. Build order

Spine first, then validation, then verbs.

1. `docs/` (this plan, principles, architecture, contract, authoring,
   credentials)
2. `lib/` (passport schema, slug, layered config, project locator)
3. `skills/awf-doctor/` — first real skill; validates the spine
4. `skills/awf-status/` — second real skill; validates idempotency model
5. Atomic skills, in pipeline order: create-project → setup-domain →
   setup-nameservers → setup-analytics → generate-content →
   review-passport → install → deploy → setup-gsc → verify-gsc →
   submit-bing
6. `awf-launch` orchestrator (last; it composes all the above)
7. `awf-update-template`
8. Tests, CI, plugin packaging — when the suite has stabilised

---

## 10. Non-goals

Stated explicitly to keep scope honest:

- Multi-host adapters (Vercel, Netlify, Plausible, etc.). One stack, one
  story. See A16.
- Mass-launch tooling (batch over a CSV of domains). Adjacent, but a
  different shape; defer until the single-launch path is robust.
- A web UI / dashboard. The skills *are* the UI.
- Automated domain purchase. Not in the legacy workflow; not in scope.
- Replacing `gh` / `wrangler` with bespoke implementations. We rely on the
  upstream CLIs.
- A credentials manager beyond layered `.env` files.

---

## 11. References

- Legacy launch checklist: `agent_web_factory/awf_vault/1_Projects/agentwebfactory-com/Steps to Launch Website.md`
- Legacy workflows: `agent_web_factory/agent_factory/workflows/`
- Legacy tool clients: `agent_web_factory/agent_factory/tools/`
- Legacy `Config`: `agent_web_factory/agent_factory/utils/config.py`
- Legacy template: `agent_web_factory/templates/landing_page/`
- Example deployed project (passport shape): `agent_web_factory/projects/devroast-com/passport.json`
