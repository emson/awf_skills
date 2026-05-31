---
name: awf-setup-gsc
description: Add the domain to Google Search Console and create the TXT verification DNS record on Cloudflare. Run after awf-deploy (the site must respond at the domain for verification to succeed later).
---

# Purpose

Step 1 of the two-step GSC dance: add the `sc-domain:<domain>` property
and write the verification TXT record on Cloudflare. The TXT-then-verify
split exists because DNS propagation is non-zero; verification happens
in `awf-verify-gsc` once propagation is done.

# Prerequisites

- A project root.
- Cloudflare creds (for the TXT record).
- `GOOGLE_APPLICATION_CREDENTIALS` pointing at the OAuth desktop client
  JSON.
- A cached `token.json` next to the project / in `$AWF_HOME` / in
  `~/.config/awf/`. If absent, the OAuth flow runs in-browser on first
  invocation.

# Procedure

1. Run `uv run scripts/setup_gsc.py`.
2. Script:
   - get-or-create `sc-domain:<domain>` property
   - read `permissionLevel`; if not `siteOwner`, fetch the TXT
     verification token
   - get-or-create the TXT DNS record on Cloudflare
3. Print: "wait a few minutes for DNS propagation, then run
   `awf-verify-gsc`."

# Idempotency

Search-or-create on both the GSC property and the TXT record.

# Failure modes

- Expired `token.json` → script removes it and prompts the user to
  re-run (which kicks off OAuth).
- TXT record already present from a prior run → reused (Cloudflare
  returns the existing one).

# Implementation status

✓ Functional. `lib/gsc/` ports the auth flow and API operations;
script: `scripts/setup_gsc.py`. Token resolution per A6 (project
root → `$AWF_HOME` → `~/.config/awf/`). Idempotent: skip when already
verified, reuse existing TXT record on Cloudflare via the
search-or-create in `lib/cf/dns.py`.
