---
name: awf-launch
description: Orchestrate an end-to-end website launch — scaffold, Cloudflare, Namecheap, Fathom, content, deploy, GSC, Bing — with explicit checkpoints at the irreducibly manual steps. Run this when you want the whole pipeline. Idempotent; safely resumes a partial launch.
---

# Purpose

The single command for "launch a site". Sequences every other awf-*
skill and pauses only at gates that genuinely need a human (SERP
screenshot, passport review, Bing webmaster import).

# Prerequisites

- `awf-doctor` passes.
- A `domain` to launch.

# Inputs

- `domain` (required).
- `--keywords "<phrase>"` (required for the content step).
- `--title "<title>"` (optional).
- `--screenshot <path>` (optional; if omitted, the orchestrator
  pauses at the SERP-screenshot gate and prompts).
- `--interactive` (optional) — pause at every step, not just at
  irreducible gates.

# Procedure

1. Run `awf-doctor`. Abort on required failures.
2. Find or create the project (`awf-create-project`); `cd` into it.
3. Run `awf-status` to learn what's already done. Skip steps whose
   gates are already complete in `passport.launch.gates`.
4. For each remaining step, in order:
   a. `awf-setup-domain`
   b. `awf-setup-nameservers`
   c. `awf-setup-analytics`
   d. **GATE: SERP screenshot.** If `--screenshot` not given, prompt:
      *"Take a Google search for `<keywords>`, screenshot the SERP,
      drop the JPEG somewhere, paste the path here."* Wait.
   e. `awf-generate-content` with that screenshot.
   f. **GATE: passport review.** Run `awf-review-passport` and pause
      until accepted.
   g. `awf-install`
   h. `awf-deploy`
   i. `awf-setup-gsc`
   j. *(brief wait for DNS propagation)*
   k. `awf-verify-gsc`
   l. **GATE: Bing webmaster import** (browser).
   m. `awf-submit-bing`
5. Print final summary: deployed URL, Fathom dashboard link, GSC
   property URL.

# Idempotency

Each sub-skill is idempotent; the orchestrator additionally consults
`passport.launch.gates` to skip steps whose human-side is done. Re-run
freely.

# Manual gates

Three: SERP screenshot, passport review, Bing webmaster import. Each
is announced before pausing.

# Failure modes

If a sub-skill fails, the orchestrator stops and reports which one,
with that sub-skill's stderr verbatim. Re-running resumes from there
(per A14).

# Implementation status

✓ Body-only by design (no script). Composes the atomic awf-* skills via
natural-language delegation, consulting `passport.launch.gates` to skip
completed steps. All 14 atomic skills it orchestrates are now
functional.
