---
name: awf-setup-nameservers
description: Point the Namecheap registrar at Cloudflare's nameservers. Run after awf-setup-domain has created the Cloudflare zone.
---

# Purpose

Tell Namecheap to delegate DNS to Cloudflare. Reads the nameserver list
from passport (stashed by `awf-setup-domain`) so the user is never
asked to copy them between dashboards.

# Prerequisites

- A project root.
- `awf-setup-domain` has run (its gate is recorded in passport).
- Namecheap creds: `NAMECHEAP_API_USER`, `NAMECHEAP_API_KEY`,
  `NAMECHEAP_USERNAME`, `NAMECHEAP_CLIENT_IP` (the latter must be
  IP-allowlisted in Namecheap's account settings).

# Inputs

- `domain` (derived from `passport.json`).

# Procedure

1. Read `passport.launch.gates.domain_setup.meta.nameservers`. If
   absent, error with "run awf-setup-domain first."
2. Run `uv run scripts/set_namecheap_ns.py <domain> <ns_csv>`.
3. Wait briefly and confirm via Namecheap's `getList` API.

# Idempotency

Setting the same nameservers twice is a no-op for Namecheap. Safe to
re-run; the script reads current NS first and skips if matching.

# Failure modes

- `Invalid request IP` — caller's public IP is not whitelisted in
  Namecheap. Print the IP we sent and the hint to whitelist it.
- Domain not in this Namecheap account — surface the error unchanged.

# Implementation status

✓ Functional. `lib/namecheap/` ports the registrar client (XML API) with
compound-TLD support (`.co.uk`, `.com.au`, …). Script:
`scripts/set_ns.py`. Idempotent: reads current NS via Namecheap, no-ops
when they already match. Target NS source priority: `--nameservers` arg
→ live Cloudflare zone read → passport gate `domain_setup.meta.nameservers`.
