---
name: awf-generate-content
description: Generate site copy and FAQs from the domain name — site_name, hero, subtitle, description, category, tags, and 8–10 FAQs — and write them into passport.json. Infers the niche directly from the domain; use --keywords to steer the angle. No screenshot needed.
---

# Purpose

Populate the content fields in `passport.json` so the landing-page
template has real copy to substitute. The domain name is the primary
signal — most domains encode their niche directly
(`londondentalimplants.co.uk`, `devroast.com`). The `--keywords` flag
steers the SEO angle when the domain is ambiguous or you want a specific
phrase to rank for.

This skill is Claude-native (A17): no script, no external API — Claude
reads `passport.json` and writes the content directly.

# Prerequisites

- A project root with `passport.json` (created by `awf-create-project`).

# Inputs

- `--keywords "<phrase>"` — optional. The primary keyword phrase to rank
  for. If omitted, Claude infers the topic from the domain name.
- `--title "<Title>"` — optional. Used for vault-note filenames only;
  defaults to `site_name` once generated.

# Procedure

1. Read `passport.json` from the project root. Extract `domain`,
   `project_name`, and any existing content fields.

2. Infer the niche. Examine the domain name:
   - Split on hyphens, dots; identify the topic, location, and audience
     signals embedded in it.
   - If `--keywords` was given, treat that phrase as the primary keyword
     and align all copy to it. The domain inference plays a supporting
     role.
   - If the domain is a brand name with no obvious meaning
     (e.g. `acmecorp.com`), lean on `--keywords` if provided; otherwise
     ask the user one short clarifying question before generating.

3. Generate the following fields. Write for SEO: lead with the keyword,
   be specific, avoid filler phrases.

   | Field | Notes |
   |-------|-------|
   | `site_name` | Short, readable brand name (e.g. "London Dental Implants") |
   | `site_hero` | H1 line — keyword-led, punchy, ≤ 10 words |
   | `site_subtitle` | One sentence expanding the hero; value prop |
   | `site_description` | Meta description, 140–160 chars, includes primary keyword |
   | `category` | Single best-fit category (e.g. "Health", "Technology") |
   | `tags` | CSV of 4–6 relevant tags |
   | `faqs` | 8–10 Q&A pairs — real questions people search for around this topic; answers 2–4 sentences each |

4. Show a unified diff of the passport changes and prompt the user to
   accept, edit, or abort before writing.

5. On accept, patch `passport.json` with the new fields and mark the
   gate `launch.gates.content_generated` complete.

6. Optionally write vault notes (if `awf_vault/` exists in the project):
   - `awf_vault/1_Projects/<slug>/<title> Analysis.md` — topic analysis
   - `awf_vault/1_Projects/<slug>/<title> Questions.md` — the raw FAQs

# FAQ quality bar

Good FAQs answer questions people actually type into Google:
- "How much does X cost in [location]?"
- "What is the difference between X and Y?"
- "Is X covered by insurance / warranty / [relevant qualifier]?"
- "How long does X take?"

Avoid generic filler questions ("What is your company?"). If the topic
is location-specific (e.g. "plumbers london"), weight questions toward
local concerns.

# Idempotency

Re-running re-prompts before overwriting any existing content. Pass
`--force` to skip the confirmation and overwrite directly.

# Failure modes

- Domain is a pure brand name with no topic signal and no `--keywords`:
  ask one clarifying question, then generate.
- `passport.json` not found: tell the user to run `awf-create-project`
  first.

# Manual gates

The user must accept the diff before the passport is written. This is
the only required human step.
