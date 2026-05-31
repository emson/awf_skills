---
name: awf-help
description: Show all available awf-skills, the typical launch pipeline, and what to run next. Use this as the entry point from any blank project directory.
---

# Purpose

Orientation skill for the awf-skills suite. Outputs a human-readable
reference of every available skill, the end-to-end launch pipeline in
order, and contextual next-step guidance based on the current directory.

This skill is Claude-native (body-only). No script — Claude reads this
file and generates the output directly.

# Prerequisites

None. Works before `awf-doctor`, before a project exists, from any
directory.

# Inputs

None required. Optional:

- `--pipeline` — show only the ordered pipeline, no skill descriptions.
- `--next` — show only the recommended next step based on current state.

# Procedure

Output the following sections in order. Tailor "What to do next" to
the user's current directory (check whether `passport.json` exists and
what gates are set).

---

## 1. One-liner

> **awf-skills** — a portable Claude Code skill suite that takes you
> from a blank directory to a live, indexed Svelte website on Cloudflare
> Pages, with analytics and search-engine submission, in one pipeline.

---

## 2. Setup skills (run once)

| Skill | What it does |
|-------|-------------|
| `/awf-init` | First-time onboarding — creates `~/.config/awf/.env`, prompts for credentials, sets `AWF_HOME` in your shell rc. Run once after `./install.sh`. |
| `/awf-doctor` | Pre-flight check — validates CLIs, credentials, and OAuth tokens. Run before any skill that touches a remote API. |

---

## 3. The launch pipeline (in order)

| Step | Skill | Notes |
|------|-------|-------|
| 1 | `/awf-create-project <domain>` | Scaffold the project directory and `passport.json`. |
| 2 | `/awf-doctor` | Last chance to fix env before touching APIs. |
| 3 | `/awf-setup-domain` | Cloudflare zone + Pages project + DNS + HTTPS + www→apex redirect. |
| 4 | `/awf-setup-nameservers` | Point Namecheap registrar at Cloudflare NS. (Requires domain you own.) |
| 5 | `/awf-setup-analytics` | Create Fathom site; writes `fathom_site_id` to passport. |
| 6 | `/awf-generate-content --screenshot <path> --keywords "<kw>"` | **Manual gate**: take a Google SERP screenshot first, then run. Generates site copy and FAQs from the image. |
| 7 | `/awf-review-passport --mark-reviewed` | Lint passport and mark the review gate complete. |
| 8 | `/awf-install` | `npm install` in the project directory. |
| 9 | `/awf-deploy` | Build + deploy to Cloudflare Pages. Site goes live. |
| 10 | `/awf-setup-gsc` | Add domain to Google Search Console + write TXT verification record. |
| 11 | `/awf-verify-gsc` | Wait for DNS, verify GSC property, submit sitemap. |
| 12 | `/awf-submit-bing --confirm-imported` | **Manual gate**: import from GSC in Bing Webmaster first. Then generates IndexNow key, deploys it, submits URLs. |

Run the whole pipeline in one command (with checkpoints at manual gates):

```
/awf-launch <domain> --keywords "<kw>"
```

---

## 4. Maintenance skills

| Skill | What it does |
|-------|-------------|
| `/awf-status` | Live report of what's done — queries Cloudflare, Fathom, GSC directly. Use to resume a partial launch. |
| `/awf-update-template` | Re-overlay a newer template version onto an existing project, preserving content. |

---

## 5. Testing tiers (dry-run first)

| Tier | Needs | What it validates |
|------|-------|------------------|
| **1 — Dry** | git, uv | init, doctor, create-project, review-passport |
| **2 — Local build** | + node/npm | template overlay, `npm run build`, substitution |
| **3 — API-side** | + Cloudflare/Namecheap/Fathom creds | domain setup, analytics (no real domain needed) |
| **4 — Full launch** | + domain you own + wrangler login | end-to-end to a live site |

---

## 6. What to do next

Check whether `passport.json` exists in the current directory:

- **No passport.json** and not set up yet → run `/awf-init`, then `/awf-doctor`.
- **No passport.json** but already set up → run `/awf-create-project <your-domain>`.
- **passport.json exists** → run `/awf-status` to see exactly where the launch left off.
- **Unsure if credentials work** → run `/awf-doctor`.

# Idempotency

Read-only. Safe to run any number of times.

# Failure modes

None — this skill generates output from its own content.

# Manual gates

None.
