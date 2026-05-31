---
name: awf-verify-gsc
description: Verify the Google Search Console property (after the TXT record has propagated) and submit the sitemap. Run after awf-setup-gsc, with a small wait for DNS propagation.
---

# Purpose

Close the GSC verification loop and submit `sitemap.xml`.

# Prerequisites

- `awf-setup-gsc` has run.
- `<domain>` already serves `https://<domain>/sitemap.xml` (the Svelte
  template generates one at build time).

# Procedure

1. Run `uv run scripts/verify_gsc.py`.
2. Script: call GSC `verify` for the `sc-domain:<domain>` resource;
   wait briefly; submit `https://<domain>/sitemap.xml`.

# Idempotency

Verifying a verified property is a no-op. Submitting the same sitemap
twice is harmless.

# Failure modes

- Verification fails: usually means TXT record hasn't propagated yet.
  Suggest waiting a few minutes and re-running.
- Sitemap not reachable: suggest checking the deploy.

# Implementation status

✓ Functional. `scripts/verify_gsc.py`. Detects the "TXT not propagated"
failure mode and surfaces a wait-and-retry hint rather than crashing.
Records partial success (verified-but-sitemap-failed) in
`passport.launch.gates.gsc_verify.meta`.
