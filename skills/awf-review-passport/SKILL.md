---
name: awf-review-passport
description: Lint passport.json against template expectations and walk the user through any required edits. Run after awf-generate-content, before awf-install.
---

# Purpose

Force a human eyeball on the generated copy before deploy. Lints
required fields, flags empty `features` and short FAQ lists, and
prompts for changes.

# Prerequisites

- A project root.

# Inputs

None.

# Procedure

1. Run `uv run scripts/review.py` (calls `Passport.validate()`).
2. Print problems grouped by severity:
   - **errors** (schema mismatches: domain regex, slug derivation,
     URL mismatch, unsupported stack pin) — block.
   - **warnings** (`site_name` empty, `< 3` FAQs, `features[0]` empty)
     — print but don't block.
3. Open `passport.json` for the user to edit, or accept-as-is.
4. Mark gate `passport.launch.gates.passport_reviewed` complete.

# Idempotency

Pure (modulo the user's edits).

# Manual gates

The review itself.

# Implementation status

✓ Functional. `scripts/review.py` partitions `Passport.validate()`
output into errors (block) and warnings (don't block). With
`--mark-reviewed`, advances the `passport_reviewed` launch gate.
