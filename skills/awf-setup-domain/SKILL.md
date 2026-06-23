---
name: awf-setup-domain
description: Set up the domain on Cloudflare from scratch — create the zone, the Pages project, the apex/www DNS records, force HTTPS, and the www→apex bulk redirect. For a single extra record on an already-created zone, use awf-cf-dns-record instead. Run after awf-create-project, before awf-setup-nameservers.
---

# Purpose

Idempotently establish all Cloudflare-side resources for the project.
Mirrors the legacy `domain_setup_workflow.py` but ports the logic into
this repo (no `agent_factory` import).

# Prerequisites

- A project root.
- `CLOUDFLARE_EMAIL`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`.

# Inputs

- `domain` (derived from `passport.json`).

# Procedure

1. Verify creds via `awf-doctor` (or its own minimal check).
2. Run `uv run scripts/setup_domain.py`.
3. The script does, in order:
   - get-or-create zone for `domain`
   - get-or-create Pages project named `<slug>`
   - get-or-create custom domain on the Pages project
   - get-or-create CNAME apex → `<slug>.pages.dev`
   - get-or-create www → apex DNS record
   - enable always_use_https on the zone
   - get-or-create www → apex bulk redirect
4. Read the zone's nameservers back from Cloudflare and stash them
   in `passport.json#launch.gates.domain_setup.meta.nameservers`. The
   next skill (`awf-setup-nameservers`) consumes them.

# Idempotency

Every step is search-or-create (matches the legacy
`get_or_create_zone` / `search_or_create_pages_project` pattern). Safe
to re-run.

# Failure modes

- `403` from Cloudflare: invalid API key or account_id mismatch.
- Zone already on a *different* Cloudflare account: surface the error
  unchanged (A13); the user must reclaim it.

# Implementation status

✓ Functional. `scripts/setup_domain.py` composes `lib/cf/` (already
ported) — every step is search-or-create, so the whole pipeline is
idempotent. On success, stashes the zone's nameservers in
`passport.launch.gates.domain_setup.meta.nameservers` for the next
skill to consume.
