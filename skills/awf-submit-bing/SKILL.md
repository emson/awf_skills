---
name: awf-submit-bing
description: Submit the site's URLs to Bing IndexNow. Generates a per-domain IndexNow key on first run and stores it in passport.json. The browser-only Bing Webmaster import-from-GSC step is named explicitly as a manual gate.
---

# Purpose

Push URLs from the live `sitemap.xml` to Bing's IndexNow endpoint.
Stores the per-domain key in `passport.json#indexnow_key` (A6 trade-off
resolved per-domain).

The Bing Webmaster property creation itself is browser-OAuth-only and
is named as a manual gate (A8) — not automated.

# Prerequisites

- A project root.
- The site is deployed and `https://<domain>/sitemap.xml` is reachable.

# Inputs

- `--regenerate-key` (optional) — rotate the IndexNow key.

# Procedure

1. Confirm the manual gate:
   *"Open https://www.bing.com/webmasters, sign in with the
   appropriate Microsoft account, add the property by importing from
   GSC, and submit the sitemap. Press enter when done."*
   (Skip this prompt if `passport.launch.gates.bing_imported.completed_at`
   is already set.)
2. If `passport.indexnow_key` is empty: generate a UUID hex key, write
   it to `passport.json`, and write
   `<project_root>/static/<key>.txt` containing the key. Remind the
   user to redeploy so the key file is reachable at
   `https://<domain>/<key>.txt`.
3. Run `uv run scripts/submit_indexnow.py`.
4. Script: fetch URLs from `https://<domain>/sitemap.xml`, batch them
   (10,000 max per IndexNow request), POST to
   `https://api.indexnow.org/indexnow`.

# Idempotency

Submitting the same URLs twice is supported by IndexNow. Key file is
created once and reused.

# Failure modes

- Sitemap unreachable → tell the user to deploy first.
- Key file 404 → user hasn't redeployed since key generation; tell
  them to `awf-deploy`.

# Implementation status

✓ Functional. `lib/bing/` ports the IndexNow client + adds key
generation and key-file-reachability checks. Script:
`scripts/submit.py`. Two-stage flow with explicit gates: generate key →
exit 3 (manual gate) until `awf-deploy` makes the key file live →
prompt for Bing-Webmaster import → submit URLs in batches.
