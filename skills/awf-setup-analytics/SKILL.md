---
name: awf-setup-analytics
description: Create a Fathom Analytics site for this domain and write fathom_site_id into passport.json. Run any time after awf-create-project.
---

# Purpose

Provision Fathom and persist the issued site ID into `passport.json`.
The Svelte template reads `fathom_site_id` at build time to inject the
tracking snippet.

# Prerequisites

- A project root.
- `FATHOM_API_KEY`.

# Inputs

- `domain` (derived).

# Procedure

1. Run `uv run scripts/setup_fathom.py`.
2. Script: list sites → if a site for this domain exists, reuse its ID;
   else create one. Patch passport with `fathom_site_id`.

# Idempotency

`fathom_site_id` already set + the API confirms the site exists → no-op.
ID set but Fathom doesn't know it → warn (drift) and offer `--force` to
recreate.

# Failure modes

- `401` from Fathom: bad API key.

# Implementation status

✓ Functional. `lib/fathom/` is the ported client; the script is at
`scripts/setup_fathom.py`. Idempotent on `passport.fathom_site_id`:
verifies an existing id is live (no-op), surfaces drift (passport id
not in Fathom) with a `--force` escape hatch, otherwise search-or-
creates and patches the passport.
